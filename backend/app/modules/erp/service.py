"""ERP/MES 集成业务逻辑（API §28）。

职责边界（总设计文档 §2.2）：CRM 只同步与展示订单履约关键状态，
**不复制整套 ERP/MES 能力**。所以这里只做四件事：
  1. 把销售订单推过去（幂等）；
  2. 拉回履约状态并写进 `order_status_history`（source=ERP）；
  3. 维护 `external_mappings` 的外部单号映射；
  4. 收发件时的集成日志留痕。

幂等与"结果未知"（第八批 §8.11，2026-10-06 修）：
  - 对方是否真按 `idempotency_key` 去重**尚未真实验收**，所以本地不能只靠报文
    里有个字段就认为不会重复：`push_order` 自己用订单行锁保证同一张单不会被
    两个连接同时推出去；
  - "已经推过"的判断要同时看 `sales_orders.erp_order_id` **和**
    `external_mappings`：只有映射、主表为空是真实存在的中间状态，
    原来这种情况会再推一次，对方系统里就多出一张单；
  - 推送结果分四类落库：成功 / 明确失败（对方拒绝）/ 明确未发出（配置或验收缺失）/
    **结果未知**（超时、响应缺成功字段、没有外部单号）。结果未知必须先把外部单号
    核对清楚才能重试，这一条靠库里持久化的请求状态来保证，不靠内存。

回调的事件留痕（同 §8.11）：
  - 双编号（CRM 订单号 + 外部单号）必须**同源一致**，不一致进异常队列、
    不改任何一个订单；
  - 未匹配/冲突的事件保存**完整事件**（含 raw_status/remark/原始报文）与稳定事件键，
    补上映射后重放同一事件即可恢复；
  - 同一事件重放不再写历史与日志。
"""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.data_scope import scoped_owner_ids
from app.core.deps import CurrentUser
from app.core.errors import AppError, ErrorCode
from app.modules.customer.model import Customer
from app.modules.erp import adapter as erp_adapter
from app.modules.erp.adapter import (
    ERR_CLASS_CREDENTIAL,
    ERR_CLASS_SHOP_PERMISSION,
    ERR_CLASS_SIGNATURE,
    ERR_CLASS_TRANSPORT,
    ERR_CLASS_UNCLASSIFIED,
    STATE_CONFIGURED_UNVERIFIED,
    STATE_FAULT,
    STATE_LABELS,
    STATE_NOT_CONFIGURED,
    STATE_READONLY_VERIFIED,
    STATE_WRITE_VERIFIED,
    SYSTEMIC_ERROR_CLASSES,
    ErpError,
    ErpMappingMismatch,
    ErpNotConfigured,
    ErpResultUnknown,
    get_adapter,
)
from app.modules.integration.model import ExternalMapping, IntegrationLog
from app.modules.order.model import ORDER_STATUS_LABEL, OrderStatusHistory, SalesOrder, SalesOrderItem
from app.modules.product.model import Sku

# ---------------------------------------------------------------- 状态常量
#
# integration_logs.status 是 String(16)，下面每个值都要放得下。
# 用常量而不是散落的字面量：筛选、异常队列、readiness 都要按同一套口径读它。

LOG_STATUS_SUCCESS = "success"
LOG_STATUS_FAILED = "failed"          # 对方明确拒绝（报文/业务错误）
LOG_STATUS_SKIPPED = "skipped"        # 未配置：明确没发出去
LOG_STATUS_NOT_SENT = "not_sent"      # 能力未验收：明确没发出去
LOG_STATUS_SENDING = "sending"        # 请求已发出，结果还没回来
LOG_STATUS_UNKNOWN = "unknown"        # 请求可能已发出，结果无法确认
LOG_STATUS_CONFLICT = "conflict"      # 双编号不同源 / 本地映射与主表冲突
LOG_STATUS_UNMATCHED = "unmatched"    # CRM 里找不到对应订单
LOG_STATUS_DUPLICATE = "duplicate"    # 并发重复请求：另一路已完成，本次让位
LOG_STATUS_SUPERSEDED = "superseded"  # 同事件键的旧记录，已被后续成功处理取代

#: 出现这两个状态说明"上一次推单的结果不知道"，重推之前必须先核对（§8.11）。
INFLIGHT_PUSH_STATUS = (LOG_STATUS_SENDING, LOG_STATUS_UNKNOWN)

#: "明确未发出"的两种状态：订单**不能**被标成已推送。
NOT_SENT_STATUS = (LOG_STATUS_SKIPPED, LOG_STATUS_NOT_SENT)

#: 需要人来处理、不属于"正常同步完成"的状态 —— 异常队列就是按它筛的。
EXCEPTION_QUEUE_STATUS = (
    LOG_STATUS_CONFLICT,
    LOG_STATUS_UNMATCHED,
    LOG_STATUS_UNKNOWN,
    LOG_STATUS_SKIPPED,
    LOG_STATUS_NOT_SENT,
)

#: 事件处理状态（写在 response_data["processing_status"]，与上面的 status 一起用：
#: status 是给筛选的短标签，processing_status 是这次接收的完整结论）。
EVENT_RECEIVED = "received"
EVENT_PROCESSED = "processed"
EVENT_UNMATCHED = "unmatched"
EVENT_CONFLICT = "conflict"
EVENT_PUSHED = "pushed"

#: 回调原始报文最多留存多少字符。状态回调本身很小，设上限是防止有人拿超大报文
#: 把 integration_logs 撑爆（超限只留摘要，并**明确标出被截断**，不假装是全文）。
RAW_EVENT_MAX_CHARS = 8192

#: 看"全公司集成诊断"（各配置项配没配、全量统计）需要的权限。
#: 用已有的 settings:manage，而不是新造权限码：新码要配角色种子与迁移，
#: 本轮不该动那些文件（交接说明 §0.3）。
DIAGNOSTIC_PERMISSION = "settings:manage"

#: readiness 判断"故障"时回看多少条最近日志。
RECENT_FAILURE_SCAN = 20


def serialize_log(row: IntegrationLog) -> dict:
    response_data = row.response_data if isinstance(row.response_data, dict) else {}
    return {
        "id": row.id,
        "integration_type": row.integration_type,
        "provider": row.provider,
        "direction": row.direction,
        "business_type": row.business_type,
        "business_id": row.business_id,
        "request_data": row.request_data,
        "response_data": row.response_data,
        "status": row.status,
        # 这两个是异常队列最需要的两列：处理到哪一步、以及能不能按事件键重放。
        "processing_status": response_data.get("processing_status"),
        "event_key": response_data.get("event_key"),
        "error_message": row.error_message,
        "created_at": row.created_at,
    }


def serialize_mapping(row: ExternalMapping) -> dict:
    return {
        "id": row.id,
        "system_type": row.system_type,
        "business_type": row.business_type,
        "internal_id": row.internal_id,
        "external_id": row.external_id,
        "external_code": row.external_code,
        "last_sync_at": row.last_sync_at,
    }


async def build_order_payload(session: AsyncSession, order: SalesOrder) -> dict:
    """组装推送报文。用 CRM 订单号做幂等键，重试不会在对方系统建出两张单。"""
    customer = await session.get(Customer, order.customer_id)
    items = (
        await session.execute(
            select(SalesOrderItem, Sku.sku_code)
            .outerjoin(Sku, Sku.id == SalesOrderItem.sku_id)
            .where(SalesOrderItem.order_id == order.id)
            .order_by(SalesOrderItem.id.asc())
        )
    ).all()
    return {
        # 聚水潭口径：so_id 是外部（我方）单号
        "so_id": order.order_no,
        "idempotency_key": order.order_no,
        "shop_id": None,
        "order_date": order.created_at.strftime("%Y-%m-%d %H:%M:%S") if order.created_at else None,
        "pay_amount": float(order.total_amount),
        "currency": order.currency,
        "receiver_name": None,
        "remark": order.remark,
        "customer": {
            "name": customer.name if customer else None,
            "tax_no": customer.tax_no if customer else None,
        },
        "items": [
            {
                "sku_code": sku_code,
                "sku_name": item.sku_snapshot,
                "qty": float(item.quantity),
                "price": float(item.unit_price),
                "amount": float(item.amount),
                "remark": item.remark,
            }
            for item, sku_code in items
        ],
        "delivery_date": order.delivery_date.isoformat() if order.delivery_date else None,
        "payment_terms": order.payment_terms,
    }


# ---------------------------------------------------------------- 推单（§8.11）


async def _lock_order(session: AsyncSession, order_id: int) -> SalesOrder | None:
    """锁住订单行，并用**数据库里的最新值**刷新它（推单的串行化点）。

    为什么要它：两个连接同时点"推送"时，各自读到的 `erp_order_id` 都是空，
    于是各建一张外部单。`SELECT ... FOR UPDATE` 让后来者等前一个事务提交，
    再用 `populate_existing` 强制重读——对方推完了就能看见，不会再推一次。

    `populate_existing` 不能省：会话是 `expire_on_commit=False` 的，
    普通重查会命中身份映射里的旧对象，读到的是提交前的空值。

    SQLite 等方言不支持 FOR UPDATE（会被忽略），所以真正的并发验收
    必须在 PostgreSQL 上做（scripts/check_erp_push_recovery.py）。
    """
    stmt = (
        select(SalesOrder)
        .where(SalesOrder.id == order_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalars().first()


async def _find_order_mapping(
    session: AsyncSession, system_type: str, internal_id: int
) -> ExternalMapping | None:
    """订单在某个外部系统里的映射行（同一订单只认最早那一行）。"""
    stmt = (
        select(ExternalMapping)
        .where(
            ExternalMapping.system_type == system_type,
            ExternalMapping.business_type == "order",
            ExternalMapping.internal_id == internal_id,
        )
        .order_by(ExternalMapping.id.asc())
    )
    return (await session.execute(stmt)).scalars().first()


async def _list_inflight_pushes(session: AsyncSession, order_id: int) -> list[IntegrationLog]:
    """这个订单上"结果未知"的推送请求记录（sending / unknown）。"""
    stmt = (
        select(IntegrationLog)
        .where(
            IntegrationLog.integration_type == "erp",
            IntegrationLog.direction == "outbound",
            IntegrationLog.business_type == "order",
            IntegrationLog.business_id == order_id,
            IntegrationLog.status.in_(INFLIGHT_PUSH_STATUS),
        )
        .order_by(IntegrationLog.id.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def _find_inflight_push(session: AsyncSession, order_id: int) -> IntegrationLog | None:
    rows = await _list_inflight_pushes(session, order_id)
    return rows[0] if rows else None


def _unknown_push_message(order: SalesOrder, adapter, detail: str) -> str:
    return (
        f"订单 {order.order_no} 的推单结果未知：{detail}。"
        f"请求可能已经发出，请先在{adapter.label}里按单号 {order.order_no} 核对是否已建单，"
        f"确认后用该订单的「核对并登记外部单号」接口登记，系统不会自动重推"
        f"（盲目重推会在对方系统里多建一张单）"
    )


async def _persist_push_request(*, adapter, order: SalesOrder, payload: dict) -> int:
    """用**独立会话**把"这次推送请求已经发出"落盘，返回日志 id。

    为什么是独立会话（顺序在 `push_order` 里不能换）：
      · 先 commit 再抢行锁 → 行锁丢了，两个连接各自判定"还没推过"，双击推送建两张单；
      · 在同一个未提交事务里写日志 → 请求失败时路由层 rollback 会把这条记录一起抹掉，
        库里连"发过请求"都没有，重启后无从判断该不该重推（正是要修的缺陷）。
    独立会话写并提交：既保住行锁，又让这条记录不受本次请求成败影响。
    """
    async with SessionLocal() as side:
        row = IntegrationLog(
            integration_type="erp",
            provider=adapter.label,
            direction="outbound",
            business_type="order",
            business_id=order.id,
            request_data=payload,
            status=LOG_STATUS_SENDING,
            response_data={
                "processing_status": LOG_STATUS_SENDING,
                "request_key": payload.get("idempotency_key"),
                "order_no": order.order_no,
            },
        )
        side.add(row)
        await side.commit()
        return int(row.id)


async def _finish_push(
    session: AsyncSession,
    log: IntegrationLog,
    *,
    status: str,
    error_message: str | None = None,
    error_class: str | None = None,
    response_data: dict | None = None,
) -> None:
    """把这次推单请求的最终状态**先落盘**再往外抛。

    沿用仓库既有纪律（企微转接同一套）：外部调用已经发生，失败痕迹必须先 commit，
    因为路由层捕获异常后会 `rollback()` —— 不先落盘就白记。
    """
    log.status = status
    log.error_message = error_message
    merged = {**(log.response_data or {}), "processing_status": status}
    if error_class:
        merged["error_class"] = error_class
    if response_data:
        merged.update(response_data)
    log.response_data = merged
    await session.commit()


def _require_external_credential(result: dict, *, payload: dict, adapter) -> str:
    """取出**可信的外部受理凭据**：对方系统自己的订单号。

    没有它就一律按"结果未知"处理（§8.11 验收：响应为空 / 缺成功字段 /
    无可靠外部凭据都不能标已同步）。特别地：**本地 CRM 单号不能充当外部单号**——
    原来 `external_code` 会回退成 `payload["so_id"]`，于是
    `sales_orders.erp_order_id` 被写成本地单号，界面显示"已推送"，
    对方系统里其实什么都没有。
    """
    external_id = str((result or {}).get("external_id") or "").strip()
    local_no = str(payload.get("so_id") or "").strip()
    if not external_id:
        raise ErpResultUnknown(
            f"{adapter.label}的响应里没有外部订单号（external_id 为空），无法确认是否已建单",
            api="push_order",
        )
    if local_no and external_id == local_no:
        raise ErpResultUnknown(
            f"{adapter.label}把本地单号 {external_id} 原样当成外部单号返回，"
            "这不构成外部受理凭据",
            api="push_order",
        )
    return external_id


async def _resolve_existing_push(
    session: AsyncSession, *, adapter, order: SalesOrder
) -> dict | None:
    """本地底账已经能回答的三种情况；都不成立时返回 None（继续推）。

    调用点**必须在抢到订单行锁之后**：只有这样，"库里存在 sending/unknown 记录"
    才能被解释成"上一次尝试已经死掉"。任何还活着的尝试都持有那把行锁，
    不可能同时在跑。
    """
    mapping = await _find_order_mapping(session, adapter.system_type, order.id)
    mapped = (mapping.external_id or "").strip() if mapping is not None else ""
    bound = (order.erp_order_id or "").strip()

    if bound and mapped and bound != mapped:
        # 主表与映射互相矛盾：两个都不改、也不重推——再推一次可能造出**第三张**外部单。
        # 人先核对，然后用 reconcile 把两边对齐。
        reason = (
            f"订单 {order.order_no} 主表登记的外部单号是 {bound}，"
            f"external_mappings 里是 {mapped}：本地两处不一致，已拒绝再推送"
        )
        session.add(
            IntegrationLog(
                integration_type="erp",
                provider=adapter.label,
                direction="outbound",
                business_type="order",
                business_id=order.id,
                status=LOG_STATUS_CONFLICT,
                request_data={"order_no": order.order_no},
                response_data={
                    "processing_status": LOG_STATUS_CONFLICT,
                    "erp_order_id": bound,
                    "mapped_external_id": mapped,
                    "order_no": order.order_no,
                },
                error_message=reason,
            )
        )
        await session.commit()
        raise ErpMappingMismatch(reason)

    if bound:
        return {
            "pushed": True,
            "already_synced": True,
            "erp_order_id": bound,
            "mapping": serialize_mapping(mapping) if mapping is not None else None,
            "message": "该订单已经推送过，未重复建单",
        }

    if mapped:
        # 已有映射、主表为空（历史只落了一边 / 进程中途退出）：先按映射补齐主表，
        # **不再建单**。原来只在主表非空时才提前返回，这种情况会真的再推一次。
        order.erp_order_id = mapped
        if mapping.last_sync_at is None:
            mapping.last_sync_at = datetime.now(UTC)
        return {
            "pushed": True,
            "already_synced": True,
            "recovered": True,
            "erp_order_id": mapped,
            "mapping": serialize_mapping(mapping),
            "message": f"映射表里已有外部单号 {mapped}，只补齐了本地主表，未重复建单",
        }

    inflight = await _find_inflight_push(session, order.id)
    if inflight is not None:
        raise ErpResultUnknown(
            _unknown_push_message(
                order,
                adapter,
                f"库里还有一条未了结的请求记录（#{inflight.id}，状态 {inflight.status}）",
            ),
            api="push_order",
        )
    return None


async def push_order(
    session: AsyncSession, *, order: SalesOrder, operator_id: int | None
) -> dict:
    """推送订单到 ERP/MES。已推送过就直接返回，不重复建单。

    四步顺序不能换（§8.11）：
      1. 抢订单行锁（抢到之后不可能有别人正在调外部系统）；
      2. 本地底账能回答的直接返回：已同步 / 可用映射补主表 / 上次结果未知；
      3. 用独立会话落"请求已发出"（行锁要跨外部调用保持，所以不能在主会话里提交）；
      4. 调外部，按结果落四类状态之一。
    """
    adapter = get_adapter()

    locked = await _lock_order(session, order.id)
    if locked is not None:
        order = locked

    resolved = await _resolve_existing_push(session, adapter=adapter, order=order)
    if resolved is not None:
        await session.commit()
        return resolved

    payload = await build_order_payload(session, order)
    log_id = await _persist_push_request(adapter=adapter, order=order, payload=payload)
    log = await session.get(IntegrationLog, log_id)
    if log is None:  # pragma: no cover - 理论不可达，显式报错好过静默丢凭据
        raise ErpError(
            "推单请求记录写入后读不回来，已中止推送（避免无凭据地重复建单）", api="push_order"
        )

    try:
        result = await adapter.push_order(payload)
    except ErpNotConfigured as error:
        # 明确未发出：日志留痕，订单**不**标记已推送。
        await _finish_push(
            session, log, status=LOG_STATUS_SKIPPED, error_message=str(error)
        )
        raise
    except ErpError as error:
        status = (
            LOG_STATUS_UNKNOWN if error.kind == "result_unknown" else LOG_STATUS_FAILED
        )
        await _finish_push(
            session,
            log,
            status=status,
            error_message=str(error),
            error_class=getattr(error, "error_class", None),
        )
        raise
    except Exception as error:
        # ⚠️ 这一类最要命：网络超时、连接重置、适配器内部异常**都不是 ErpError**。
        # 原来它们会直接穿过去，路由层 rollback 把已经 flush 的日志一起回滚 ——
        # 结果是"请求可能已经发出去了，库里却什么都没有"，重启后无从判断该不该重推。
        detail = f"{type(error).__name__}: {error}"
        message = _unknown_push_message(order, adapter, detail)
        await _finish_push(
            session,
            log,
            status=LOG_STATUS_UNKNOWN,
            error_message=message,
            error_class=ERR_CLASS_TRANSPORT,
        )
        raise ErpResultUnknown(message, api="push_order") from error

    try:
        external_id = _require_external_credential(result, payload=payload, adapter=adapter)
    except ErpError as error:
        # 响应回来了但没有可靠凭据：既不能算成功，也不能当"明确失败"（对方可能已经建单）。
        await _finish_push(
            session,
            log,
            status=LOG_STATUS_UNKNOWN,
            error_message=str(error),
            error_class=getattr(error, "error_class", None),
        )
        raise

    external_code = str((result or {}).get("external_code") or "").strip() or None
    now = datetime.now(UTC)
    mapping = await _find_order_mapping(session, adapter.system_type, order.id)
    if mapping is None:
        mapping = ExternalMapping(
            system_type=adapter.system_type,
            business_type="order",
            internal_id=order.id,
        )
        session.add(mapping)
        await session.flush()
    mapping.external_id = external_id
    mapping.external_code = external_code
    mapping.last_sync_at = now
    # 主表只写**对方自己的**单号。库里两处（主表 + 映射）保持一致，
    # 库层唯一约束的迁移建议见交接回报。
    order.erp_order_id = external_id
    log.status = LOG_STATUS_SUCCESS
    log.response_data = {
        **(log.response_data or {}),
        "processing_status": EVENT_PUSHED,
        "external_id": external_id,
        "external_code": external_code,
        "order_no": order.order_no,
    }
    session.add(
        OrderStatusHistory(
            order_id=order.id,
            old_status=order.status,
            new_status=order.status,
            source="ERP",
            operator_id=operator_id,
            remark=f"已推送 {adapter.label}，外部单号 {external_id}",
            created_at=now,
        )
    )
    await session.commit()
    return {
        "pushed": True,
        "already_synced": False,
        "erp_order_id": external_id,
        "mapping": serialize_mapping(mapping),
        "message": f"已推送到{adapter.label}",
    }


async def reconcile_push(
    session: AsyncSession, *, order: SalesOrder, external_id: str, operator_id: int | None
) -> dict:
    """人工核对外部系统之后，把外部单号登记回来 —— 推单"结果未知"的恢复出口。

    为什么要人核对这一步：按我方单号反查"对方是否已经建单"要依赖真实的接口合同
    （聚水潭 / 企业 erp-bridge 的资料还没拿到，交接说明 §0.3 第 5 条），
    凭记忆猜一个查询端点比不做更危险。所以这里**不假装自动查询**：
    由人在外部系统里查到单号后登记，系统负责把它落成可信凭据，
    并把遗留的 sending/unknown 记录标成已核对 —— 之后再推就会直接返回"已同步"。
    """
    adapter = get_adapter()
    external_id = (external_id or "").strip()
    if not external_id:
        raise AppError(
            ErrorCode.REQUIRED_FIELD_MISSING,
            "external_id 必填：请填写在外部系统里核对到的订单号",
            422,
        )
    if external_id == (order.order_no or "").strip():
        raise AppError(
            ErrorCode.PARAM_ERROR,
            "外部单号不能等于 CRM 订单号：本地单号不是外部受理凭据",
            422,
        )

    locked = await _lock_order(session, order.id)
    if locked is not None:
        order = locked
    mapping = await _find_order_mapping(session, adapter.system_type, order.id)
    bound = (order.erp_order_id or "").strip()
    mapped = (mapping.external_id or "").strip() if mapping is not None else ""
    if bound and bound != external_id:
        raise AppError(
            ErrorCode.DUPLICATE,
            f"订单 {order.order_no} 已登记外部单号 {bound}，与本次核对结果 {external_id} 不一致："
            "请先确认哪一个是真实单号，不要用本次结果覆盖",
            409,
        )
    if mapped and mapped != external_id:
        raise AppError(
            ErrorCode.DUPLICATE,
            f"映射表里订单 {order.order_no} 已登记外部单号 {mapped}，与本次核对结果 {external_id} 不一致",
            409,
        )

    now = datetime.now(UTC)
    if mapping is None:
        mapping = ExternalMapping(
            system_type=adapter.system_type,
            business_type="order",
            internal_id=order.id,
        )
        session.add(mapping)
        await session.flush()
    mapping.external_id = external_id
    if not mapping.external_code:
        mapping.external_code = order.order_no
    mapping.last_sync_at = now
    order.erp_order_id = external_id

    pending = await _list_inflight_pushes(session, order.id)
    for row in pending:
        row.status = LOG_STATUS_SUCCESS
        row.response_data = {
            **(row.response_data or {}),
            "processing_status": "reconciled",
            "reconciled_external_id": external_id,
            "reconciled_by": operator_id,
        }
        row.error_message = None
    session.add(
        IntegrationLog(
            integration_type="erp",
            provider=adapter.label,
            direction="outbound",
            business_type="order",
            business_id=order.id,
            status=LOG_STATUS_SUCCESS,
            request_data={"order_no": order.order_no},
            response_data={
                "processing_status": "reconciled",
                "external_id": external_id,
                "cleared_requests": len(pending),
                "operator_id": operator_id,
            },
        )
    )
    await session.commit()
    return {
        "reconciled": True,
        "erp_order_id": external_id,
        "cleared": len(pending),
        "message": (
            f"已登记外部单号 {external_id}"
            + (f"，并了结 {len(pending)} 条结果未知的推送请求" if pending else "")
        ),
    }


#: 对方状态 → CRM 状态的兜底检查：只有这六个值能落进 sales_orders.status
VALID_STATUS = set(ORDER_STATUS_LABEL)


async def refresh_status(
    session: AsyncSession, *, order: SalesOrder, operator_id: int | None
) -> dict:
    """拉回履约状态并写状态历史（source=ERP）。"""
    adapter = get_adapter()
    if not order.erp_order_id:
        raise AppError(
            ErrorCode.PARAM_ERROR, "该订单还没推送到 ERP/MES，无法拉取履约状态", 422
        )

    log = IntegrationLog(
        integration_type="erp",
        provider=adapter.label,
        direction="inbound",
        business_type="order",
        business_id=order.id,
        request_data={"erp_order_id": order.erp_order_id},
        status="pending",
    )
    session.add(log)
    await session.flush()

    try:
        result = await adapter.fetch_order_status(order.erp_order_id)
    except ErpNotConfigured:
        log.status = LOG_STATUS_SKIPPED
        log.error_message = "ERP/MES 未配置，未实际拉取"
        await session.commit()  # 同 push_order：失败痕迹必须先落盘再抛
        raise
    except ErpError as error:
        log.status = LOG_STATUS_FAILED
        log.error_message = str(error)
        log.response_data = {
            "processing_status": LOG_STATUS_FAILED,
            "error_class": getattr(error, "error_class", ERR_CLASS_UNCLASSIFIED),
        }
        await session.commit()
        raise
    except Exception as error:
        # 拉取是只读的，不会产生外部副作用；但也要如实留痕，别让"刷新失败"
        # 在库里查不到原因。
        log.status = LOG_STATUS_UNKNOWN
        log.error_message = f"{type(error).__name__}: {error}"
        log.response_data = {
            "processing_status": LOG_STATUS_UNKNOWN,
            "error_class": ERR_CLASS_TRANSPORT,
        }
        await session.commit()
        raise ErpResultUnknown(
            f"读取 {adapter.label} 履约状态失败（{type(error).__name__}）：{error}", api="fetch_order_status"
        ) from error

    log.status = LOG_STATUS_SUCCESS
    log.response_data = {"processing_status": "fetched", "raw_status": result.get("raw_status")}

    new_status = result.get("status")
    changed = False
    blocked_reason: str | None = None
    # 对方返回了不认识的状态：不改 CRM 状态，但日志里留 raw_status，
    # 否则"为什么状态没变"要靠猜。
    if new_status and new_status in VALID_STATUS and new_status != order.status:
        # 必须走订单状态服务，**不能直接赋值**（§4.1.8）。
        # 原来这里是 `order.status = new_status`：ERP 能把已取消的订单"复活"成已发货，
        # 也能在还有未发量时把整单标成完成——§3.5 刚立的闸门被这条后门绕过去。
        # （webhook 那条早就改走状态服务了，手动刷新这条当时漏了。）
        from app.modules.order import service as order_service

        try:
            await order_service.change_status(
                session,
                order,
                new_status=new_status,
                operator_id=operator_id,
                source="ERP",
                remark=f"{adapter.label} 回传状态",
            )
            changed = True
        except AppError as error:
            # 被守卫拦下：CRM 状态不动，但把原因写进日志与返回值——
            # 否则"刷新了却没变"只能靠猜。拉取类接口不该因为守卫而 500。
            blocked_reason = error.message
            log.error_message = f"状态未变更（被订单状态守卫拦下）：{error.message}"

    return {
        "status": order.status,
        "status_label": ORDER_STATUS_LABEL.get(order.status, order.status),
        "changed": changed,
        "blocked_reason": blocked_reason,
        "raw_status": result.get("raw_status"),
        "shipped_at": result.get("shipped_at"),
    }


# ---------------------------------------------------------------- 回调（§8.11）


def build_status_event_key(
    *,
    order_no: str | None,
    erp_order_id: str | None,
    raw_status: str,
    remark: str | None = None,
) -> str:
    """稳定事件键：同一事件每次算出来都一样，不同事件必然不同。

    为什么不用"对方给的事件 id"：真实协议里到底有没有这个字段还没验收
    （交接说明 §0.3 第 5 条），凭空规定一个字段名等于猜接口。所以键从
    **我们确实收到的业务字段**推出来：字段值完全相同 → 同一事件 → 不重复写历史；
    状态 / 备注 / 单号任一不同 → 新事件 → 分开处理。排序后的 JSON 保证
    "字段顺序不同"不会算成两个事件。
    """
    payload = {
        "order_no": (order_no or "").strip(),
        "erp_order_id": (erp_order_id or "").strip(),
        "status": (raw_status or "").strip(),
        "remark": (remark or "").strip(),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "erp-status-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _build_event_payload(
    *,
    event_key: str,
    order_no: str | None,
    erp_order_id: str | None,
    raw_status: str,
    remark: str | None,
    received_at: datetime,
    raw_event: dict | None,
) -> dict:
    """要长期留存的那份**完整事件**。

    原来未匹配的事件只记两个编号，事后连"当时推的是哪个状态"都查不到，
    补了映射也无法重放。这里把业务字段全留下，并附上对方原始报文
    （超限只留摘要，且明确标出被截断，不假装是全文）。
    """
    payload: dict[str, Any] = {
        "event_key": event_key,
        "order_no": order_no,
        "erp_order_id": erp_order_id,
        "status": raw_status,
        "remark": remark,
        "received_at": received_at.isoformat(),
    }
    if raw_event is not None:
        try:
            text = json.dumps(raw_event, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            text = str(raw_event)
        if len(text) <= RAW_EVENT_MAX_CHARS:
            payload["raw"] = raw_event
        else:
            payload["raw_truncated"] = {
                "size_chars": len(text),
                "head": text[:RAW_EVENT_MAX_CHARS],
                "note": "原始报文超过留存上限，只保留了开头；字段顺序被规范化",
            }
    return payload


def webhook_number_consistency(
    *,
    order_no: str | None,
    erp_order_id: str | None,
    by_no: SalesOrder | None,
    by_ext: SalesOrder | None,
    mapped_external_id: str | None,
) -> tuple[SalesOrder | None, str | None]:
    """双编号同源校验（**纯函数**，便于不连库地把每条分支都举到）。

    返回 `(订单, 冲突原因)`，两者互斥：
      · 都没匹配上 → `(None, None)`：未匹配，走"先未匹配、后补映射可恢复"；
      · 同源一致   → `(订单, None)`；
      · 不一致     → `(None, 原因)`：进异常队列，**不改任何一个订单**。

    `mapped_external_id` 是 `by_no` 这单在 `external_mappings` 里登记的外部单号。
    """
    if order_no and erp_order_id:
        erp_order_id = erp_order_id.strip()
        if by_no is None and by_ext is None:
            return None, None
        if by_no is None:
            return None, (
                f"CRM 里没有订单号 {order_no}，但外部单号 {erp_order_id} 指向订单 "
                f"{getattr(by_ext, 'order_no', None)}（id={getattr(by_ext, 'id', None)}）："
                "两个编号不同源"
            )
        if by_ext is not None and by_ext.id != by_no.id:
            return None, (
                f"CRM 订单号 {order_no}（id={by_no.id}）与外部单号 {erp_order_id}"
                f"（指向 id={by_ext.id}）指向不同的订单：两个编号不同源"
            )
        bound = (by_no.erp_order_id or "").strip()
        if bound and bound != erp_order_id:
            return None, (
                f"订单 {order_no} 登记的外部单号是 {bound}，回调给的是 {erp_order_id}："
                "两个编号不同源，拒绝按任意一个改状态"
            )
        if not bound and (mapped_external_id or "").strip() != erp_order_id:
            return None, (
                f"订单 {order_no} 尚未登记外部单号（映射表里是 {mapped_external_id or '无'}），"
                f"回调却同时给了外部单号 {erp_order_id}：无法证明两个编号同源"
            )
        return by_no, None
    return (by_no if order_no else by_ext), None


async def _load_order_by_no(session: AsyncSession, order_no: str) -> SalesOrder | None:
    stmt = select(SalesOrder).where(SalesOrder.order_no == order_no)
    return (await session.execute(stmt)).scalars().first()


async def _load_order_by_external(
    session: AsyncSession, system_type: str, erp_order_id: str
) -> SalesOrder | None:
    """按外部单号定位订单：先看主表，再看映射表。

    只看主表不够：主表 `erp_order_id` 为空但映射已有外部单号是真实存在的中间状态
    （只落了一边 / 崩溃恢复），那种单在回调里必须认得出来，否则会一直被判成未匹配。
    """
    stmt = select(SalesOrder).where(SalesOrder.erp_order_id == erp_order_id)
    order = (await session.execute(stmt)).scalars().first()
    if order is not None:
        return order
    mapping = (
        await session.execute(
            select(ExternalMapping).where(
                ExternalMapping.system_type == system_type,
                ExternalMapping.business_type == "order",
                ExternalMapping.external_id == erp_order_id,
            )
        )
    ).scalars().first()
    if mapping is None:
        return None
    return await session.get(SalesOrder, mapping.internal_id)


async def _find_processed_event(session: AsyncSession, event_key: str) -> IntegrationLog | None:
    """同一事件键**已经处理成功**的那些记录。

    只认 success：未匹配 / 冲突的记录不算"处理过"，否则"先未匹配、后补映射"
    这条恢复路径会被去重永久堵死（§8.11 验收要求它能恢复）。
    """
    stmt = (
        select(IntegrationLog)
        .where(
            IntegrationLog.integration_type == "erp",
            IntegrationLog.direction == "inbound",
            IntegrationLog.status == LOG_STATUS_SUCCESS,
            IntegrationLog.response_data["event_key"].as_string() == event_key,
        )
        .order_by(IntegrationLog.id.desc())
    )
    return (await session.execute(stmt)).scalars().first()


async def _mark_superseded(session: AsyncSession, *, event_key: str, keep_log_id: int) -> int:
    """把同一事件键的旧记录标成"已被后续处理取代"（异常队列里不再挂旧账）。"""
    stmt = select(IntegrationLog).where(
        IntegrationLog.integration_type == "erp",
        IntegrationLog.direction == "inbound",
        IntegrationLog.id != keep_log_id,
        IntegrationLog.status.in_((LOG_STATUS_UNMATCHED, LOG_STATUS_CONFLICT)),
        IntegrationLog.response_data["event_key"].as_string() == event_key,
    )
    rows = list((await session.execute(stmt)).scalars().all())
    for row in rows:
        response_data = row.response_data if isinstance(row.response_data, dict) else {}
        row.status = LOG_STATUS_SUPERSEDED
        row.response_data = {
            **response_data,
            "processing_status": LOG_STATUS_SUPERSEDED,
            "superseded_by": keep_log_id,
        }
    return len(rows)


async def apply_status_webhook(
    session: AsyncSession,
    *,
    order_no: str | None,
    erp_order_id: str | None,
    raw_status: str,
    remark: str | None = None,
    raw_event: dict | None = None,
) -> dict:
    """处理对方推送过来的状态变更（API §28 的两个 webhook 共用）。

    按 CRM 订单号**或**外部单号定位订单；两个都给时必须同源一致，否则记一条
    冲突记录（异常队列）并拒绝改任何一个订单。找不到就记一条完整的未匹配事件，
    返回处理结果而不是抛异常——对方系统不该因为我们没这个单就反复重试。
    """
    adapter = get_adapter()
    order_no = (order_no or "").strip() or None
    erp_order_id = (erp_order_id or "").strip() or None
    if not order_no and not erp_order_id:
        raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "order_no 或 erp_order_id 必填", 422)

    received_at = datetime.now(UTC)
    event_key = build_status_event_key(
        order_no=order_no,
        erp_order_id=erp_order_id,
        raw_status=raw_status,
        remark=remark,
    )

    # ① 同事件重放：已处理成功过就直接回报，不再写历史/日志。
    prior = await _find_processed_event(session, event_key)
    if prior is not None:
        prior_response = prior.response_data if isinstance(prior.response_data, dict) else {}
        return {
            "matched": bool(prior_response.get("matched")),
            "changed": False,
            "replayed": True,
            "event_key": event_key,
            "message": "同一事件此前已处理过（结论一致），本次不重复写入",
        }

    event = _build_event_payload(
        event_key=event_key,
        order_no=order_no,
        erp_order_id=erp_order_id,
        raw_status=raw_status,
        remark=remark,
        received_at=received_at,
        raw_event=raw_event,
    )
    log = IntegrationLog(
        integration_type="erp",
        provider=adapter.label,
        direction="inbound",
        business_type="order",
        status=EVENT_RECEIVED,
        request_data=event,
        response_data={"event_key": event_key, "processing_status": EVENT_RECEIVED},
    )
    session.add(log)
    await session.flush()

    # ② 定位订单：双编号必须同源一致
    system_type = getattr(adapter, "system_type", "ERP")
    by_no = await _load_order_by_no(session, order_no) if order_no else None
    by_ext = (
        await _load_order_by_external(session, system_type, erp_order_id)
        if erp_order_id
        else None
    )
    mapped_external_id = None
    if by_no is not None:
        mapping = await _find_order_mapping(session, system_type, by_no.id)
        mapped_external_id = mapping.external_id if mapping is not None else None
    order, conflict = webhook_number_consistency(
        order_no=order_no,
        erp_order_id=erp_order_id,
        by_no=by_no,
        by_ext=by_ext,
        mapped_external_id=mapped_external_id,
    )

    if conflict:
        log.status = LOG_STATUS_CONFLICT
        log.error_message = conflict
        log.response_data = {
            "event_key": event_key,
            "processing_status": EVENT_CONFLICT,
            "matched": False,
            "changed": False,
            "order_no": order_no,
            "erp_order_id": erp_order_id,
            "order_id_by_no": getattr(by_no, "id", None),
            "order_id_by_external": getattr(by_ext, "id", None),
            "conflict": conflict,
        }
        await session.commit()
        return {
            "matched": False,
            "changed": False,
            "conflict": True,
            "event_key": event_key,
            "message": conflict,
        }

    if order is None:
        log.status = LOG_STATUS_UNMATCHED
        log.error_message = "CRM 里找不到对应的销售订单（订单号与外部单号都没匹配上）"
        log.response_data = {
            "event_key": event_key,
            "processing_status": EVENT_UNMATCHED,
            "matched": False,
            "changed": False,
            "order_no": order_no,
            "erp_order_id": erp_order_id,
        }
        await session.commit()
        return {
            "matched": False,
            "changed": False,
            "unmatched": True,
            "event_key": event_key,
            "message": "CRM 里找不到对应的销售订单；事件已完整留存，补上映射后重放同一事件即可恢复",
        }

    log.business_id = order.id
    new_status = adapter.STATUS_MAP.get(raw_status) if hasattr(adapter, "STATUS_MAP") else None
    changed = False
    if new_status and new_status in VALID_STATUS and new_status != order.status:
        # ERP callbacks must pass through the same lifecycle guards as normal
        # CRM status changes (cancelled orders stay terminal; completion checks
        # unfinished shipment batches; cancellation records cancelled_at).
        # Directly assigning order.status here previously bypassed those rules.
        from app.modules.order import service as order_service

        try:
            await order_service.change_status(
                session,
                order,
                new_status=new_status,
                operator_id=None,
                source="ERP",
                remark=remark or f"{adapter.label} 推送状态 {raw_status}",
            )
        except AppError as exc:
            log.status = LOG_STATUS_FAILED
            log.error_message = exc.message
            log.response_data = {
                "event_key": event_key,
                "processing_status": LOG_STATUS_FAILED,
                "matched": True,
                "changed": False,
                "mapped_status": new_status,
                "raw_status": raw_status,
                "order_no": order.order_no,
            }
            await session.commit()
            return {
                "matched": True,
                "order_id": order.id,
                "order_no": order.order_no,
                "status": order.status,
                "changed": False,
                "raw_status": raw_status,
                "mapped": True,
                "event_key": event_key,
                "blocked_reason": exc.message,
                "message": exc.message,
            }
        # The shared service already adds the corresponding history row.
        changed = True

    log.status = LOG_STATUS_SUCCESS
    log.response_data = {
        "event_key": event_key,
        "processing_status": EVENT_PROCESSED,
        "matched": True,
        "changed": changed,
        "mapped_status": new_status,
        "raw_status": raw_status,
        "order_no": order.order_no,
    }
    # 同一事件键此前留下的未匹配/冲突记录标成"已被取代"：异常队列里只剩真正待办的。
    await _mark_superseded(session, event_key=event_key, keep_log_id=log.id)
    await session.commit()
    return {
        "matched": True,
        "order_id": order.id,
        "order_no": order.order_no,
        "status": order.status,
        "changed": changed,
        "raw_status": raw_status,
        "mapped": bool(new_status),
        "event_key": event_key,
        "message": "已处理",
    }


# ---------------------------------------------------------------- 查询


async def sync_logs(
    session: AsyncSession,
    *,
    user: CurrentUser,
    direction: str | None,
    status: str | None,
    business_id: int | None,
    page: int,
    page_size: int,
) -> tuple[list[dict], int]:
    stmt = select(IntegrationLog).where(IntegrationLog.integration_type == "erp")
    # 按订单数据范围过滤（P2 修复）：日志里带着订单号、请求报文和错误信息，
    # 不能让所有 order:view 的人看到**别人订单**的同步细节。
    # 非订单类日志（business_type 不是 order）对非全量范围的人一律不可见——
    # **看不到归属就看不到**：宁可少给，也不要越权给。
    owner_ids = await scoped_owner_ids(session, user)
    if owner_ids is not None:
        stmt = stmt.where(
            IntegrationLog.business_type == "order",
            IntegrationLog.business_id.in_(
                select(SalesOrder.id).where(SalesOrder.owner_id.in_(owner_ids or [0]))
            ),
        )
    if direction:
        stmt = stmt.where(IntegrationLog.direction == direction)
    if status:
        stmt = stmt.where(IntegrationLog.status == status)
    if business_id:
        stmt = stmt.where(
            IntegrationLog.business_id == business_id,
            IntegrationLog.business_type == "order",
        )
    total = (
        await session.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar_one()
    rows = (
        await session.execute(
            stmt.order_by(IntegrationLog.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return [serialize_log(row) for row in rows], int(total)


async def exception_queue(
    session: AsyncSession,
    *,
    user: CurrentUser,
    status: str | None,
    page: int,
    page_size: int,
) -> tuple[list[dict], int]:
    """异常队列：冲突 / 未匹配 / 结果未知 / 未发出的集成留痕（§8.11）。

    为什么要单独一个入口：这些行恰恰是**没有正常归属**的那些
    （未匹配的没有 business_id、冲突的不敢挂到任何一单），混在 sync_logs 里
    一按订单范围过滤就永远看不见——而它们才是最需要人来处理的。
    """
    owner_ids = await scoped_owner_ids(session, user)
    wanted = (status,) if status else EXCEPTION_QUEUE_STATUS
    stmt = select(IntegrationLog).where(
        IntegrationLog.integration_type == "erp",
        IntegrationLog.status.in_(wanted),
    )
    if owner_ids is not None:
        # 有归属的行按订单范围给；没有归属的（未匹配/冲突）只给全量范围的人。
        stmt = stmt.where(
            IntegrationLog.business_type == "order",
            IntegrationLog.business_id.in_(
                select(SalesOrder.id).where(SalesOrder.owner_id.in_(owner_ids or [0]))
            ),
        )
    total = (
        await session.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar_one()
    rows = (
        await session.execute(
            stmt.order_by(IntegrationLog.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return [serialize_log(row) for row in rows], int(total)


# ---------------------------------------------------------------- 就绪度（§8.12）


def is_integration_admin(user: Any) -> bool:
    """能不能看"全公司集成诊断"。管理员角色直接放行，其余要 settings:manage + 全量范围。"""
    roles = set(getattr(user, "roles", None) or ())
    if "admin" in roles:
        return True
    has = getattr(user, "has", None)
    return bool(
        callable(has)
        and has(DIAGNOSTIC_PERMISSION)
        and getattr(user, "data_scope", None) == "all"
    )


def connection_state(
    *, configured: bool, capabilities: dict, latest_failure: Any | None
) -> str:
    """五态判定（**纯函数**，便于把每条分支都举到）。

    顺序有讲究：**故障优先于"已验收"**。昨天验收通过、今天凭据过期，
    还显示"已验收"会让人以为能继续建单。配置都不齐时只报"未配置"。
    """
    if not configured:
        return STATE_NOT_CONFIGURED
    if latest_failure is not None:
        return STATE_FAULT
    write = capabilities.get("write")
    read = capabilities.get("read")
    if write is not None and write.verified:
        return STATE_WRITE_VERIFIED
    if read is not None and read.verified:
        return STATE_READONLY_VERIFIED
    return STATE_CONFIGURED_UNVERIFIED


def failure_reason(row: Any) -> str:
    response_data = row.response_data if isinstance(row.response_data, dict) else {}
    error_class = response_data.get("error_class") or ERR_CLASS_UNCLASSIFIED
    label = {
        ERR_CLASS_SIGNATURE: "签名错误",
        ERR_CLASS_CREDENTIAL: "凭据过期/无效",
        ERR_CLASS_SHOP_PERMISSION: "无店铺权限",
    }.get(error_class, "未分类错误（对方错误码表待外部资料）")
    return f"{label}：{row.error_message or ''}".strip()


async def _latest_systemic_failure(session: AsyncSession, *, provider: str | None):
    """最近一次**系统性**失败（签名 / 凭据 / 店铺权限），没有就返回 None。

    只认系统性错误：某一单的报文错误（比如"数量不合法"）不代表这条通道不通，
    把它报成"故障"会让 readiness 一直红着，反而没人看。
    """
    stmt = (
        select(IntegrationLog)
        .where(
            IntegrationLog.integration_type == "erp",
            IntegrationLog.status.in_((LOG_STATUS_FAILED, LOG_STATUS_UNKNOWN)),
            IntegrationLog.provider == provider,
        )
        .order_by(IntegrationLog.id.desc())
        .limit(RECENT_FAILURE_SCAN)
    )
    rows = (await session.execute(stmt)).scalars().all()
    for row in rows:
        response_data = row.response_data if isinstance(row.response_data, dict) else {}
        if (response_data.get("error_class") or "") in SYSTEMIC_ERROR_CLASSES:
            return row
    return None


async def _scoped_counts(
    session: AsyncSession, *, owner_ids: list[int] | None
) -> dict[str, int]:
    """订单/映射/日志三个计数，**按请求人的数据范围**算。

    原来这里是三句不带条件的 `count(*)`：任何有 order:view 的人都能拿到
    全公司的推送量、映射量与日志量——那是**集成统计**，不是他的业务数据（§8.12）。
    非全量范围的人只能看到自己范围内订单的相关数字。
    """
    orders_stmt = select(func.count()).select_from(SalesOrder)
    pushed_stmt = select(func.count()).select_from(SalesOrder).where(
        SalesOrder.erp_order_id.isnot(None)
    )
    mapping_stmt = select(func.count()).select_from(ExternalMapping).where(
        ExternalMapping.business_type == "order"
    )
    log_stmt = select(func.count()).select_from(IntegrationLog).where(
        IntegrationLog.integration_type == "erp"
    )
    if owner_ids is not None:
        scope_ids = owner_ids or [0]
        scoped_orders = select(SalesOrder.id).where(SalesOrder.owner_id.in_(scope_ids))
        orders_stmt = orders_stmt.where(SalesOrder.owner_id.in_(scope_ids))
        pushed_stmt = pushed_stmt.where(SalesOrder.owner_id.in_(scope_ids))
        mapping_stmt = mapping_stmt.where(ExternalMapping.internal_id.in_(scoped_orders))
        log_stmt = log_stmt.where(
            IntegrationLog.business_type == "order",
            IntegrationLog.business_id.in_(scoped_orders),
        )
    total = int((await session.execute(orders_stmt)).scalar_one())
    pushed = int((await session.execute(pushed_stmt)).scalar_one())
    mappings = int((await session.execute(mapping_stmt)).scalar_one())
    logs = int((await session.execute(log_stmt)).scalar_one())
    return {
        "orders": total,
        "pushed": pushed,
        "not_pushed": total - pushed,
        "mappings": mappings,
        "logs": logs,
    }


async def readiness(session: AsyncSession, *, user: Any) -> dict:
    """ERP 接入状态：配置与验收**如实**汇总 + 按权限给的诊断数字（§8.12）。

    接口"ready"不能代表接通：三个变量齐全也只到"已配置未验证"，
    `connected` 只有在对应能力真实验收通过后才为真。
    """
    adapter = get_adapter()
    info = adapter.readiness()
    capabilities = adapter.capabilities_map()
    latest_failure = await _latest_systemic_failure(session, provider=adapter.label)
    state = connection_state(
        configured=info["configured"],
        capabilities=capabilities,
        latest_failure=latest_failure,
    )
    owner_ids = await scoped_owner_ids(session, user)
    counts = await _scoped_counts(session, owner_ids=owner_ids)
    full = is_integration_admin(user)

    result: dict[str, Any] = {
        "provider": settings.erp_provider or None,
        "adapter": adapter.label,
        "state": state,
        "state_label": STATE_LABELS.get(state, state),
        "configured": bool(info["configured"]),
        # 只有"真实环境验收过的能力"才算接通；"只读通过"也不会自动把写入打开。
        "connected": state in (STATE_READONLY_VERIFIED, STATE_WRITE_VERIFIED),
        # 开发/调试环境一律显式标成模拟：这种环境下的数字不能当真实接通证据。
        "simulated": bool(settings.debug),
        "capabilities": {key: cap.as_dict() for key, cap in capabilities.items()},
        "scope": {
            "limited": owner_ids is not None,
            "data_scope": getattr(user, "data_scope", None),
        },
        "counts": counts,
    }
    if latest_failure is not None:
        result["fault_reason"] = failure_reason(latest_failure)
        created_at = getattr(latest_failure, "created_at", None)
        result["fault_at"] = created_at.isoformat() if created_at is not None else None
    if full:
        # 配置明细（哪个变量配了、缺哪个）只给集成管理员：它对普通订单查看者
        # 没有用处，却是"这套对接怎么配的"这类信息。
        result["diagnostics"] = {
            "capability_evidence": {
                key: cap.evidence for key, cap in capabilities.items()
            },
            "missing": info["missing"],
            "configured_variables": {
                "base_url": bool(settings.erp_base_url),
                "app_key": bool(settings.erp_app_key),
                "app_secret": bool(settings.erp_app_secret),
            },
        }
    else:
        result["diagnostics"] = {"restricted": "集成诊断明细仅对集成管理员开放"}
    return result


__all__ = [
    "ErpError",
    "ErpNotConfigured",
    "erp_adapter",
    "apply_status_webhook",
    "build_order_payload",
    "build_status_event_key",
    "connection_state",
    "exception_queue",
    "is_integration_admin",
    "push_order",
    "readiness",
    "reconcile_push",
    "refresh_status",
    "serialize_log",
    "serialize_mapping",
    "sync_logs",
    "webhook_number_consistency",
]
