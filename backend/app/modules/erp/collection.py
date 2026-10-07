"""外部订单 / 发货 / 售后的**只读采集**（第八批 §8.13 分段一、二）。

## 这一层负责什么

    拉取（适配器，未验收） → 规范化 → 幂等落库（原始事实） → 建/更新映射 → 待匹配队列
    水位推进 / 分页断点 / 采集租约 / 失败留痕

它**不**负责对账（那在 `reconcile.py`），也**绝不**写回款、应收、业绩或订单状态：
外部事实进入本地的方式只有一条 —— 先进原始事实表，再由人工在差异队列里核定。

## 三条硬性质是怎么落地的

1. **同一外部订单重复拉取不重复**
   唯一约束 `(system_type, shop_id, object_type, dedupe_key)` 兜底；服务层先查再比
   内容摘要 `raw_digest`，没变就只累加 `collected_count`，不写任何字段。

2. **分页中断后续拉不漏**
   "至少一次投递 + 幂等落库"：**每落完一页才推进 `page_token` 并提交**。
   崩在取第 3 页时，断点仍然是"第 3 页之前的游标"，重启后会重拉第 3 页 ——
   重复的那一页被唯一键吃掉，所以既不漏也不重。

3. **两店同编号不串**
   `shop_id` 进唯一键、进映射键。A 店的 `SO-1` 与 B 店的 `SO-1` 是两行事实、
   两个映射；即使本地只认得其中一个，另一个也只是"待匹配"，不会互相覆盖。

## 拆单 / 合单 / 部分退换货靠什么不双算

靠**明细行级的稳定键**：请求 `order` 时，一页里可以同时带单据头和它的明细行
（明细用 `object_type` 自己声明），明细的稳定键是 `父单键#行键`，并且记得住
自己属于哪张单。于是"一单拆多次发货"与"多单并一次发货"都能在行级说清归属，
汇总时按唯一键去重即可 —— 不需要、也不能靠订单总号硬扛。

## 真实对接在哪

一句话：**没有**。`ErpAdapter.fetch_records` 的基类实现一律抛 `ErpNotVerified`，
聚水潭适配器也明确不实现（端点/分页/增量/店铺授权都没有官方资料）。
所以 `collect_once` 在一个真实环境里跑出来的结论是"未接通"，并且这个结论
**落库**（`external_sync_watermarks.status` = not_configured / not_verified），
接口和页面显示的是同一件事。可模拟验收走的是测试里的替身适配器。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import json_safe
from app.core.errors import AppError, ErrorCode
from app.modules.customer.model import Customer
from app.modules.erp.adapter import (
    ErpNotConfigured,
    ErpNotVerified,
    ExternalPage,
    get_adapter,
)
from app.modules.integration.model import (
    ExternalObjectMapping,
    ExternalRecord,
    ExternalSourceRegistry,
    ExternalSyncWatermark,
    IntegrationLog,
)
from app.modules.integration.vocab import (
    ALL_SHOPS,
    COLLECT_OBJECT_TYPES,
    CURSOR_FAILED,
    CURSOR_IDLE,
    CURSOR_NOT_CONFIGURED,
    CURSOR_NOT_VERIFIED,
    CURSOR_RUNNING,
    MAPPED_OBJECT_TYPES,
    MATCH_CONFLICT,
    MATCH_MATCHED,
    MATCH_PENDING,
    OBJECT_AFTERSALE_ITEM,
    OBJECT_ORDER,
    OBJECT_ORDER_ITEM,
    OBJECT_SHIPMENT_ITEM,
    PRIMARY_OBJECT_TYPES,
    SOURCE_UNVERIFIED,
    SOURCE_VERIFIED,
    line_dedupe_key,
)
from app.modules.order.model import SalesOrder, SalesOrderItem
from app.modules.product.model import Sku

#: 一页默认多少条、一次最多跑多少页。
#: 上限存在的理由：采集是同步请求，页数不封顶时一个坏游标就能把请求挂到超时。
DEFAULT_PAGE_SIZE = 50
MAX_PAGES_PER_RUN = 200

#: 采集租约（秒）。取 5 分钟：比任何一次正常采集都长，又短到"进程被 kill"
#: 之后不用人工介入就能被下一轮接管。
LEASE_SECONDS = 300

#: 采集日志里一条消息最多留多少字符（同 `service.RAW_EVENT_MAX_CHARS` 的理由）。
ERROR_MESSAGE_MAX = 2000

#: 采集批次在 `integration_logs` 里的 business_type。用独立的 business_type 而不是
#: 硬塞进 "order"：采集是**按店铺/时间窗**的动作，没有单个订单归属，
#: 塞成订单日志会让"按订单查同步记录"多出一堆看不懂的行。
BUSINESS_TYPE_COLLECT = "external_collect"

#: 采集日志的状态（与 `erp/service.py` 同一套取值，前端筛选口径一致）。
LOG_STATUS_SUCCESS = "success"
LOG_STATUS_FAILED = "failed"
#: 未配置/未验收：**明确没发出去**，不是"失败"，运维不该去查对方日志。
LOG_STATUS_NOT_SENT = "not_sent"


def _now() -> datetime:
    return datetime.now(UTC)


def _clip(text: str) -> str:
    return text if len(text) <= ERROR_MESSAGE_MAX else text[:ERROR_MESSAGE_MAX] + "…（已截断）"


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_decimal(value: Any) -> Decimal | None:
    """把外部给的数字转成 Decimal；转不了就返回 None 并**不猜**。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = str(value).strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def _as_datetime(value: Any) -> datetime | None:
    """解析外部时间。没有时区的按 **UTC** 解释，不做本地时区猜测。

    为什么按 UTC 而不是本地时区：对方给"2026-09-30 23:30"到底指哪个时区，
    没有接口资料无法确定（交接说明 §0.3 第 5 条）。按 UTC 解释只影响跨期分期的
    边界个案，而按服务器本地时区解释会让同一份数据在时区/夏令时变更后改变归属期。
    真实接入时按官方资料把这一步换成明确的时区规则。
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    text = str(value).strip()
    if not text:
        return None
    normalized = text.replace("/", "-").replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y%m%d"):
            try:
                parsed = datetime.strptime(normalized, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _iso(value: Any) -> str | None:
    parsed = _as_datetime(value)
    return parsed.isoformat() if parsed is not None else (_text(value) or None)


def record_digest(item: dict) -> str:
    """一条**规范化**记录的内容摘要。

    它的作用是"内容没变就不写库"：重复拉取同一张单时只累加次数，
    不刷新 `payload`、也不产生新的差异候选。字段排序后取 sha256，
    所以"同一个 JSON 键顺序不同"不会被算成内容变了。
    """
    canonical = {
        "external_id": _text(item.get("external_id")),
        "external_code": _text(item.get("external_code")),
        "parent_key": _text(item.get("parent_key")),
        "order_key": _text(item.get("order_key")),
        "kind": _text(item.get("kind")),
        "line_key": _text(item.get("line_key")),
        "occurred_at": _iso(item.get("occurred_at")),
        "external_updated_at": _iso(item.get("external_updated_at")),
        "quantity": _text(item.get("quantity")),
        "amount": _text(item.get("amount")),
        "currency": _text(item.get("currency")),
        "payload": item.get("payload"),
    }
    raw = json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def normalize_item(item: dict, *, object_type: str, position: int) -> dict:
    """把适配器给的一条记录规范化；缺关键字段**当场报错并指出是第几条**。

    为什么宁可报错也不跳过：静默跳过一条会让"本期少了这张单"看起来像
    "对方没有这张单"，对账差异从此查不出来源（§8.13 点名的正是这类问题）。
    """
    if not isinstance(item, dict):
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"采集返回的第 {position} 条不是对象结构（{type(item).__name__}）："
            f"object_type={object_type}",
            422,
        )
    if object_type not in MAPPED_OBJECT_TYPES:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"不认识的采集对象类型 {object_type}（支持：{'、'.join(MAPPED_OBJECT_TYPES)}）",
            422,
        )

    external_id = _text(item.get("external_id"))
    external_code = _text(item.get("external_code"))
    parent_key = _text(item.get("parent_key"))
    order_key = _text(item.get("order_key"))
    kind = _text(item.get("kind"))
    line_key = _text(item.get("line_key"))
    dedupe_key = _text(item.get("dedupe_key"))

    if object_type in PRIMARY_OBJECT_TYPES:
        if not external_id:
            raise AppError(
                ErrorCode.PARAM_ERROR,
                f"第 {position} 条（object_type={object_type}）缺少 external_id："
                "单据/客户/SKU 没有对方编号就无法去重，也无法在回调里定位",
                422,
            )
        dedupe_key = dedupe_key or external_id
        if object_type == OBJECT_ORDER:
            # 订单自己就是归属锚点：对账按它聚合。单据头（发货/售后）不强制给，
            # 因为"合单"的发货单同时服务多张订单，归属只能落在行上。
            order_key = order_key or dedupe_key
    else:
        if not parent_key:
            raise AppError(
                ErrorCode.PARAM_ERROR,
                f"第 {position} 条（object_type={object_type}）缺少 parent_key："
                "明细行必须说清自己属于哪张单，否则拆单/合单会重复计数",
                422,
            )
        if not line_key and not external_id:
            raise AppError(
                ErrorCode.PARAM_ERROR,
                f"第 {position} 条（object_type={object_type}）缺少 line_key 与 external_id："
                "明细行没有行键就没有稳定身份，重拉会被当成新行",
                422,
            )
        # 明细的稳定键 = 父单键 + 行键；行键优先用对方行号，退回对方行编号。
        dedupe_key = dedupe_key or line_dedupe_key(parent_key, line_key or external_id or "")

    if not dedupe_key:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"第 {position} 条（object_type={object_type}）算不出稳定去重键",
            422,
        )

    return {
        "object_type": object_type,
        "dedupe_key": dedupe_key,
        "parent_key": parent_key,
        "order_key": order_key,
        "kind": kind,
        "line_key": line_key,
        "external_id": external_id,
        "external_code": external_code,
        "quantity": _as_decimal(item.get("quantity")),
        "amount": _as_decimal(item.get("amount")),
        "currency": _text(item.get("currency")),
        "occurred_at": _as_datetime(item.get("occurred_at")),
        "external_updated_at": _as_datetime(item.get("external_updated_at")),
        # payload 必须能进 JSON 列：对方给的可能是 datetime/Decimal（JSON 序列化
        # 会在 flush 时炸，而且报错出现在提交阶段，很难定位）。统一转一遍。
        "payload": json_safe(item["payload"]) if isinstance(item.get("payload"), dict) else None,
    }


# ---------------------------------------------------------------- 来源台账


async def _find_source(
    session: AsyncSession, *, system_type: str, shop_id: str, source_kind: str
) -> ExternalSourceRegistry | None:
    return (
        await session.execute(
            select(ExternalSourceRegistry).where(
                ExternalSourceRegistry.system_type == system_type,
                ExternalSourceRegistry.shop_id == shop_id,
                ExternalSourceRegistry.source_kind == source_kind,
            )
        )
    ).scalars().first()


async def get_source_state(
    session: AsyncSession, *, system_type: str, shop_id: str, source_kind: str
) -> dict:
    """某个来源的核实/授权状态。**没有登记过就是"未核实"**，不给任何默认信任。"""
    row = await _find_source(
        session, system_type=system_type, shop_id=shop_id, source_kind=source_kind
    )
    if row is None:
        return {
            "system_type": system_type,
            "shop_id": shop_id,
            "source_kind": source_kind,
            "verified": False,
            "status": SOURCE_UNVERIFIED,
            "evidence": None,
            "authorization_note": None,
            "verified_by": None,
            "verified_at": None,
        }
    return {
        "system_type": row.system_type,
        "shop_id": row.shop_id,
        "source_kind": row.source_kind,
        "verified": bool(row.verified),
        "status": SOURCE_VERIFIED if row.verified else SOURCE_UNVERIFIED,
        "evidence": row.evidence,
        "authorization_note": row.authorization_note,
        "verified_by": row.verified_by,
        "verified_at": row.verified_at,
    }


async def register_source(
    session: AsyncSession,
    *,
    system_type: str,
    shop_id: str,
    source_kind: str,
    verified: bool,
    evidence: str | None,
    authorization_note: str | None,
    operator_id: int | None,
    now: datetime | None = None,
) -> dict:
    """登记来源的核实结论（**必须带证据才能标成已核实**）。

    为什么强制要证据：这一轮的真实签名/端点/店铺授权都拿不到，若不要求证据，
    任何人点一下就能把来源标成"可信"，那 §8.14 的"来源未核实显示待核实"
    就退化成一句可以随时点掉的话。
    """
    if verified and not (evidence or "").strip():
        raise AppError(
            ErrorCode.REQUIRED_FIELD_MISSING,
            "把来源标成已核实必须填写依据（验收报告编号 / 官方文档版本 / 店铺授权范围）："
            "没有依据的核实等于默认某个系统是权威",
            422,
        )
    moment = now or _now()
    row = await _find_source(
        session, system_type=system_type, shop_id=shop_id, source_kind=source_kind
    )
    if row is None:
        row = ExternalSourceRegistry(
            system_type=system_type, shop_id=shop_id, source_kind=source_kind
        )
        session.add(row)
        await session.flush()
    row.verified = bool(verified)
    row.evidence = evidence
    row.authorization_note = authorization_note
    row.verified_by = operator_id if verified else None
    row.verified_at = moment if verified else None
    await session.flush()
    return await get_source_state(
        session, system_type=system_type, shop_id=shop_id, source_kind=source_kind
    )


# ---------------------------------------------------------------- 水位与租约


async def get_or_create_cursor(
    session: AsyncSession,
    *,
    system_type: str,
    shop_id: str,
    object_type: str,
) -> ExternalSyncWatermark:
    """取（必要时建）水位行，并**锁住它**。

    `with_for_update` 不能省：没有行锁时，A 读到 idle 正准备写 running，
    B 也读到 idle，两边都会去采，水位互相踩。锁上以后 B 必须等 A 提交，
    再读到的就是 running + 有效租约 → 明确拒绝（§8.13 的并发入口）。
    SQLite 忽略 FOR UPDATE，所以真并发只在 PostgreSQL 上验证。
    """
    stmt = (
        select(ExternalSyncWatermark)
        .where(
            ExternalSyncWatermark.system_type == system_type,
            ExternalSyncWatermark.shop_id == shop_id,
            ExternalSyncWatermark.object_type == object_type,
        )
        .with_for_update()
    )
    row = (await session.execute(stmt)).scalars().first()
    if row is not None:
        return row
    row = ExternalSyncWatermark(
        system_type=system_type, shop_id=shop_id, object_type=object_type, status=CURSOR_IDLE
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError:
        # 首轮并发创建：唯一键挡住了重复行，回滚后重取对方建好的那一行。
        # 这一步只在真并发下发生，所以离线用例走不到它（也就不会碰 rollback）。
        await session.rollback()
        row = (await session.execute(stmt)).scalars().first()
        if row is None:  # pragma: no cover - 唯一键冲突后必然能查到那一行
            raise AppError(
                ErrorCode.SYSTEM_ERROR, "采集水位行创建冲突后查不到，已中止本次采集", 500
            ) from None
    return row


async def _claim_cursor(
    session: AsyncSession,
    cursor: ExternalSyncWatermark,
    *,
    now: datetime,
    lease_seconds: int = LEASE_SECONDS,
) -> None:
    """抢采集租约。别人还在跑就明确拒绝，而不是"一起跑、水位互相踩"。"""
    if (
        cursor.status == CURSOR_RUNNING
        and cursor.lease_expires_at is not None
        and cursor.lease_expires_at > now
    ):
        raise AppError(
            ErrorCode.DUPLICATE,
            f"{cursor.system_type}/{cursor.shop_id}/{cursor.object_type} 正在采集"
            f"（租约到 {cursor.lease_expires_at.isoformat()}）。"
            "同一范围同时采集会把水位推乱，请等它结束或等租约过期后重试",
            409,
        )
    if cursor.status == CURSOR_RUNNING:
        # 租约过期还在 running = 上一轮进程异常退出：接管并记一次重试。
        cursor.retry_count = (cursor.retry_count or 0) + 1
    cursor.status = CURSOR_RUNNING
    cursor.last_started_at = now
    cursor.lease_expires_at = now + timedelta(seconds=lease_seconds)
    await session.commit()


def serialize_cursor(row: ExternalSyncWatermark) -> dict:
    return {
        "id": row.id,
        "system_type": row.system_type,
        "shop_id": row.shop_id,
        "object_type": row.object_type,
        "watermark": row.watermark,
        "page_token": row.page_token,
        "page_no": row.page_no,
        # 断点还在 = 上一次没采完。这个字段是"中断后续拉不漏"的对外可见证据。
        "resumable": bool(row.page_token or row.status == CURSOR_FAILED),
        "status": row.status,
        "retry_count": row.retry_count,
        "records_collected": row.records_collected,
        "last_error": row.last_error,
        "last_batch_id": row.last_batch_id,
        "last_started_at": row.last_started_at,
        "last_finished_at": row.last_finished_at,
        "lease_expires_at": row.lease_expires_at,
    }


# ---------------------------------------------------------------- 记录落库


async def _get_record(
    session: AsyncSession,
    *,
    system_type: str,
    shop_id: str,
    object_type: str,
    dedupe_key: str,
) -> ExternalRecord | None:
    return (
        await session.execute(
            select(ExternalRecord).where(
                ExternalRecord.system_type == system_type,
                ExternalRecord.shop_id == shop_id,
                ExternalRecord.object_type == object_type,
                ExternalRecord.dedupe_key == dedupe_key,
            )
        )
    ).scalars().first()


async def _upsert_record(
    session: AsyncSession,
    *,
    system_type: str,
    shop_id: str,
    item: dict,
    batch_id: str,
    now: datetime,
) -> tuple[ExternalRecord, str]:
    """幂等落一条外部事实。返回 `(记录, "inserted" | "updated" | "duplicate")`。"""
    object_type = item["object_type"]
    digest = record_digest(item)
    row = await _get_record(
        session,
        system_type=system_type,
        shop_id=shop_id,
        object_type=object_type,
        dedupe_key=item["dedupe_key"],
    )
    if row is None:
        row = ExternalRecord(
            system_type=system_type,
            shop_id=shop_id,
            object_type=object_type,
            dedupe_key=item["dedupe_key"],
            raw_digest=digest,
            first_seen_at=now,
            last_seen_at=now,
            collected_count=1,
            last_batch_id=batch_id,
        )
        session.add(row)
        await session.flush()
        outcome = "inserted"
    elif row.raw_digest == digest:
        # 内容没变：只留"又被拉过一次"的痕迹。**不改任何业务字段**，
        # 否则每次重拉都会把"我方采集时间"之类噪音当成"对方更新了"。
        row.last_seen_at = now
        row.collected_count = (row.collected_count or 0) + 1
        row.last_batch_id = batch_id
        await session.flush()
        return row, "duplicate"
    else:
        row.last_seen_at = now
        row.collected_count = (row.collected_count or 0) + 1
        row.last_batch_id = batch_id
        outcome = "updated"

    # 内容有变化才刷新这些列（新增与更新走同一条路）。
    row.parent_key = item["parent_key"]
    row.order_key = item["order_key"]
    row.kind = item["kind"]
    row.line_key = item["line_key"]
    row.external_id = item["external_id"]
    row.external_code = item["external_code"]
    row.quantity = item["quantity"]
    row.amount = item["amount"]
    row.currency = item["currency"]
    row.occurred_at = item["occurred_at"]
    row.external_updated_at = item["external_updated_at"]
    row.payload = item["payload"]
    row.raw_digest = digest
    await session.flush()
    return row, outcome


# ---------------------------------------------------------------- 映射与待匹配


async def _find_mapping(
    session: AsyncSession,
    *,
    system_type: str,
    shop_id: str,
    object_type: str,
    external_id: str,
) -> ExternalObjectMapping | None:
    return (
        await session.execute(
            select(ExternalObjectMapping).where(
                ExternalObjectMapping.system_type == system_type,
                ExternalObjectMapping.shop_id == shop_id,
                ExternalObjectMapping.object_type == object_type,
                ExternalObjectMapping.external_id == external_id,
            )
        )
    ).scalars().first()


#: 匹配依据的可读名。写进 `match_basis` 便于事后解释"凭什么对上的是这一条"。
BASIS_LABELS = {
    "order_no": "本地订单号一致",
    "erp_order_id": "订单主表登记的外部单号一致",
    "sku_code": "SKU 编码一致",
    "tax_no": "客户税号一致",
    "customer_name_unique": "客户名称唯一命中",
    "parent_order": "父订单已匹配，事实锚定到该订单",
    "order_line_sku": "父订单已匹配且该行 SKU 唯一命中",
    "manual": "人工指定",
}


async def resolve_mapping(
    session: AsyncSession, mapping: ExternalObjectMapping
) -> tuple[int | None, str | None, str | None, str]:
    """按 (系统, 店铺, 对象类型) 的**明规则**尝试匹配本地对象。

    返回 `(internal_id, basis, note, status)`。规则刻意只用本地已有的业务键
    （订单号 / SKU 编码 / 税号 / 客户名），**不猜外部字段名**：
    适配器必须把"我方编码"填进规范化的 `external_code`，匹配才有依据。

    匹配不上不是错误：留一行 `pending`（待匹配），本地对象补齐后跑一次
    `replay_pending_mappings` 就能补上，不需要重新拉取外部数据。
    """
    code = _text(mapping.external_code)
    external_id = _text(mapping.external_id) or ""

    if mapping.object_type == OBJECT_ORDER:
        if code:
            row = (
                await session.execute(select(SalesOrder).where(SalesOrder.order_no == code))
            ).scalars().first()
            if row is not None:
                return row.id, "order_no", None, MATCH_MATCHED
        row = (
            await session.execute(
                select(SalesOrder).where(SalesOrder.erp_order_id == external_id)
            )
        ).scalars().first()
        if row is not None:
            return row.id, "erp_order_id", None, MATCH_MATCHED
        return (
            None,
            None,
            f"本地没有订单号 {code or '（对方未给）'}，"
            f"也没有登记外部单号 {external_id} 的订单",
            MATCH_PENDING,
        )

    if mapping.object_type == "sku":
        if not code:
            return None, None, "对方没有给出我方 SKU 编码，无法匹配", MATCH_PENDING
        rows = (
            await session.execute(
                select(Sku).where(Sku.sku_code == code, Sku.deleted_at.is_(None))
            )
        ).scalars().all()
        if len(rows) == 1:
            return rows[0].id, "sku_code", None, MATCH_MATCHED
        if len(rows) > 1:
            return (
                None,
                None,
                f"SKU 编码 {code} 在本地命中 {len(rows)} 条，需人工裁定",
                MATCH_CONFLICT,
            )
        return None, None, f"本地没有 SKU 编码 {code}", MATCH_PENDING

    if mapping.object_type == "customer":
        if not code:
            return None, None, "对方没有给出客户标识，无法匹配", MATCH_PENDING
        tax = (
            await session.execute(
                select(Customer).where(Customer.tax_no == code, Customer.deleted_at.is_(None))
            )
        ).scalars().first()
        if tax is not None:
            return tax.id, "tax_no", None, MATCH_MATCHED
        by_name = (
            await session.execute(
                select(Customer).where(Customer.name == code, Customer.deleted_at.is_(None))
            )
        ).scalars().all()
        if len(by_name) == 1:
            return by_name[0].id, "customer_name_unique", None, MATCH_MATCHED
        if len(by_name) > 1:
            # 同名多家：**绝不自动挑一个**（挑错就把别人的订单挂到别家客户上）。
            return (
                None,
                None,
                f"客户名称「{code}」命中 {len(by_name)} 家，需人工指定",
                MATCH_CONFLICT,
            )
        return None, None, f"本地没有税号或名称等于「{code}」的客户", MATCH_PENDING

    # 发货 / 售后及其明细：本地没有与它们一一对应的表，它们的作用是"外部履约事实"，
    # 所以锚定到**父订单**上；父订单匹配不上，它们同样只能待匹配。
    parent = None
    if mapping.parent_key:
        parent = await _find_mapping(
            session,
            system_type=mapping.system_type,
            shop_id=mapping.shop_id,
            object_type=OBJECT_ORDER,
            external_id=mapping.parent_key,
        )
    if parent is None or parent.internal_id is None:
        return (
            None,
            None,
            f"父订单 {mapping.parent_key or '（明细未给父单）'} 还没匹配到本地订单，"
            "本行先挂待匹配",
            MATCH_PENDING,
        )

    if mapping.object_type in (
        OBJECT_ORDER_ITEM,
        OBJECT_SHIPMENT_ITEM,
        OBJECT_AFTERSALE_ITEM,
    ):
        if not code:
            # 对方没给 SKU 编码：只能锚定到订单。**这是"信息不足"而不是"匹配成功"**，
            # 所以 note 里写清楚行级匹配还缺什么，别让人以为已经对到行了。
            return (
                parent.internal_id,
                "parent_order",
                "该明细行没有我方 SKU 编码，只能锚定到父订单；"
                "行级匹配待对方按官方资料给出编码字段",
                MATCH_MATCHED,
            )
        items = (
            await session.execute(
                select(SalesOrderItem)
                .join(Sku, Sku.id == SalesOrderItem.sku_id)
                .where(SalesOrderItem.order_id == parent.internal_id, Sku.sku_code == code)
            )
        ).scalars().all()
        if len(items) == 1:
            return items[0].id, "order_line_sku", None, MATCH_MATCHED
        if len(items) > 1:
            # 同一订单里同 SKU 有多行：挂哪一行会改变行级对账，必须人来定。
            return (
                None,
                None,
                f"父订单里 SKU {code} 命中 {len(items)} 行明细，行级归属需人工确认",
                MATCH_CONFLICT,
            )
        # 有编码但对不上本地明细行：要分清两种情形，它们的后续动作完全不同。
        known_sku = (
            await session.execute(
                select(Sku).where(Sku.sku_code == code, Sku.deleted_at.is_(None))
            )
        ).scalars().all()
        if not known_sku:
            # ① 本地还没有这个 SKU：**待匹配**。这正是验收要的
            #    "未知 SKU 可待匹配、补齐后可重放"：补建 SKU 后再跑一次匹配即可。
            return (
                None,
                None,
                f"本地还没有 SKU 编码 {code}：先建 SKU（或人工指定），之后重放即可匹配",
                MATCH_PENDING,
            )
        # ② SKU 有了，但本地这张订单里没有它的明细行（订单行结构与对方不一致）。
        #    这不是"待匹配"，而是"行级对账信息不足"：锚定到订单，note 里说清楚。
        return (
            parent.internal_id,
            "parent_order",
            f"本地 SKU {code} 已存在，但订单里没有它的明细行，只能锚定到订单；"
            "行级对账需先补齐本地订单明细",
            MATCH_MATCHED,
        )

    return parent.internal_id, "parent_order", None, MATCH_MATCHED


async def _ensure_mapping(
    session: AsyncSession,
    *,
    system_type: str,
    shop_id: str,
    object_type: str,
    record: ExternalRecord,
    now: datetime,
) -> ExternalObjectMapping:
    """建/更新映射行，并跑一次自动匹配。

    **已匹配的行不会被重新匹配覆盖**：自动规则以后变严变松都不该悄悄改掉
    别人手工确认过的关联（人工指定的 `matched_by` 就是证据）。
    """
    external_id = record.external_id or record.dedupe_key
    mapping = await _find_mapping(
        session,
        system_type=system_type,
        shop_id=shop_id,
        object_type=object_type,
        external_id=external_id,
    )
    if mapping is None:
        mapping = ExternalObjectMapping(
            system_type=system_type,
            shop_id=shop_id,
            object_type=object_type,
            external_id=external_id,
            first_seen_at=now,
            last_seen_at=now,
        )
        session.add(mapping)
        await session.flush()
    mapping.last_seen_at = now
    mapping.external_code = record.external_code or mapping.external_code
    # 明细行优先用 `order_key`（它才是"这行属于哪张订单"），退回父单键：
    # 拆单时父单是订单、合单时父单是发货单而订单只能从行上读出来。
    mapping.parent_key = record.order_key or record.parent_key or mapping.parent_key
    if mapping.match_status != MATCH_MATCHED:
        internal_id, basis, note, status = await resolve_mapping(session, mapping)
        mapping.internal_id = internal_id
        mapping.match_basis = basis
        mapping.match_note = note
        mapping.match_status = status
        if status == MATCH_MATCHED:
            mapping.matched_at = now
    await session.flush()
    return mapping


# ---------------------------------------------------------------- 采集主流程


async def _write_collect_log(
    session: AsyncSession,
    *,
    provider: str,
    object_type: str,
    shop_id: str,
    page_size: int,
    status: str,
    summary: dict | None = None,
    error: Exception | None = None,
) -> None:
    """给每一轮采集留一条集成日志（**原始凭据与断点都可追溯**）。

    为什么不只靠水位行：水位行是"最新状态"，会被下一轮覆盖；日志是流水，
    能回答"这一期到底采过几轮、每轮采到多少、当时断在哪一页"。
    """
    counters = {
        key: (summary or {}).get(key)
        for key in ("batch_id", "pages", "inserted", "updated", "duplicates", "pending_mappings")
    }
    session.add(
        IntegrationLog(
            integration_type="erp",
            provider=provider,
            direction="inbound",
            business_type=BUSINESS_TYPE_COLLECT,
            business_id=None,
            request_data={
                "object_type": object_type,
                "shop_id": shop_id,
                "page_size": page_size,
            },
            response_data={
                **counters,
                "processing_status": status,
                "object_type": object_type,
                "shop_id": shop_id,
            },
            status=status,
            error_message=_clip(f"{type(error).__name__}: {error}") if error is not None else None,
        )
    )
    await session.flush()


async def collect_once(
    session: AsyncSession,
    *,
    object_type: str,
    adapter: Any | None = None,
    shop_id: str = ALL_SHOPS,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_pages: int = MAX_PAGES_PER_RUN,
    lease_seconds: int = LEASE_SECONDS,
    operator_id: int | None = None,
    now: datetime | None = None,
    batch_id: str | None = None,
) -> dict:
    """只读采集一次（跨多页，直到对方说没有下一页或达到页数上限）。

    **中途失败不清断点**：`page_token` 只在整页落库成功后才推进，
    所以异常路径下它仍指向"还没落完的那一页"（§8.13 验收：分页中断后续拉不漏）。

    返回的 `status` 就是真实结论：`idle`（跑完了）/ `not_configured` /
    `not_verified` / `failed`。前两个表示**一次请求都没发出去**，
    调用方据此显示"未接通"，不要把它当成"采集成功但没数据"。
    """
    adapter = adapter or get_adapter()
    moment = now or _now()
    batch = batch_id or uuid.uuid4().hex[:32]
    system_type = getattr(adapter, "system_type", "ERP")
    shop = (shop_id or "").strip() or ALL_SHOPS

    if object_type not in COLLECT_OBJECT_TYPES:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"本轮只对 {'、'.join(COLLECT_OBJECT_TYPES)} 做只读采集，收到 {object_type}",
            422,
        )
    if page_size < 1 or page_size > 200:
        raise AppError(ErrorCode.PARAM_ERROR, "page_size 必须在 1..200 之间", 422)

    cursor = await get_or_create_cursor(
        session, system_type=system_type, shop_id=shop, object_type=object_type
    )
    await _claim_cursor(session, cursor, now=moment, lease_seconds=lease_seconds)

    source_kind = f"collect_{object_type}"
    summary: dict[str, Any] = {
        "system_type": system_type,
        "shop_id": shop,
        "object_type": object_type,
        "batch_id": batch,
        "pages": 0,
        "inserted": 0,
        "updated": 0,
        "duplicates": 0,
        "pending_mappings": 0,
        "operator_id": operator_id,
        "source": await get_source_state(
            session, system_type=system_type, shop_id=shop, source_kind=source_kind
        ),
    }

    # 断点：上一轮没采完就从这里续。
    cursor_token = cursor.page_token
    start_page_no = cursor.page_no or 1

    try:
        while summary["pages"] < max_pages:
            page: ExternalPage = await adapter.fetch_records(
                object_type=object_type,
                shop_id=shop,
                cursor=cursor_token,
                page_size=page_size,
            )
            items = list(getattr(page, "items", None) or [])
            for position, raw in enumerate(items, start=1):
                # 一页里可以顺带带回与它相关的事实（订单页带明细行、明细行带 SKU/客户身份）。
                # 为什么允许：拆单/合单的归属信息就在明细行上，强制分两次请求会让
                # "头到了、行还没到"成为常态，对账就会把差异算错。声明了**不认识**的
                # 类型才拒绝 —— 那是适配器翻译错了，不该静默当成别的对象存下来。
                declared = (
                    _text(raw.get("object_type")) if isinstance(raw, dict) else None
                )
                item_object_type = declared or object_type
                if item_object_type not in MAPPED_OBJECT_TYPES:
                    raise AppError(
                        ErrorCode.PARAM_ERROR,
                        f"采集 {object_type} 的第 {position} 条声明了不认识的对象类型 "
                        f"{item_object_type}（已定义：{'、'.join(MAPPED_OBJECT_TYPES)}）",
                        422,
                    )
                item = normalize_item(
                    raw, object_type=item_object_type, position=position
                )
                record, outcome = await _upsert_record(
                    session,
                    system_type=system_type,
                    shop_id=shop,
                    item=item,
                    batch_id=batch,
                    now=moment,
                )
                summary[
                    {"inserted": "inserted", "updated": "updated", "duplicate": "duplicates"}[
                        outcome
                    ]
                ] += 1
                mapping = await _ensure_mapping(
                    session,
                    system_type=system_type,
                    shop_id=shop,
                    object_type=item_object_type,
                    record=record,
                    now=moment,
                )
                if mapping.match_status != MATCH_MATCHED:
                    summary["pending_mappings"] += 1
            # **先落库、再推进断点**：顺序反了就会漏页。
            await session.commit()

            summary["pages"] += 1
            water = _text(getattr(page, "watermark", None))
            if water:
                cursor.watermark = water
            cursor.page_no = start_page_no + summary["pages"]
            cursor.records_collected = (cursor.records_collected or 0) + len(items)
            if getattr(page, "has_more", False) and getattr(page, "next_page_token", None):
                cursor.page_token = _text(page.next_page_token)
                cursor_token = cursor.page_token
                await session.commit()
                continue
            cursor.page_token = None
            await session.commit()
            break

        cursor.status = CURSOR_IDLE
        cursor.last_error = None
        cursor.last_finished_at = _now()
        cursor.lease_expires_at = None
        cursor.last_batch_id = batch
        await _write_collect_log(
            session,
            provider=getattr(adapter, "label", system_type),
            object_type=object_type,
            shop_id=shop,
            page_size=page_size,
            status=LOG_STATUS_SUCCESS,
            summary=summary,
        )
        await session.commit()
        summary["cursor"] = serialize_cursor(cursor)
        summary["status"] = CURSOR_IDLE
        # 页数到上限但对方还有下一页：如实说"还没采完"，断点留着下次续。
        summary["reached_page_limit"] = bool(cursor.page_token)
        summary["message"] = (
            f"采集完成：{summary['pages']} 页，新增 {summary['inserted']} 条，"
            f"更新 {summary['updated']} 条，重复（内容未变）{summary['duplicates']} 条"
            + ("；达到本轮页数上限，断点已保留，可继续采集" if summary["reached_page_limit"] else "")
        )
        return summary
    except ErpNotConfigured as error:
        await _fail_quietly(
            session, cursor, status=CURSOR_NOT_CONFIGURED, error=error,
            provider=getattr(adapter, "label", system_type), object_type=object_type,
            shop_id=shop, page_size=page_size, summary=summary,
        )
        raise
    except ErpNotVerified as error:
        await _fail_quietly(
            session, cursor, status=CURSOR_NOT_VERIFIED, error=error,
            provider=getattr(adapter, "label", system_type), object_type=object_type,
            shop_id=shop, page_size=page_size, summary=summary,
        )
        raise
    except Exception as error:  # noqa: BLE001 —— 任何异常都要留下可续拉的断点
        await _fail_quietly(
            session, cursor, status=CURSOR_FAILED, error=error,
            provider=getattr(adapter, "label", system_type), object_type=object_type,
            shop_id=shop, page_size=page_size, summary=summary,
        )
        raise


async def _fail_quietly(
    session: AsyncSession,
    cursor: ExternalSyncWatermark,
    *,
    status: str,
    error: Exception,
    provider: str,
    object_type: str,
    shop_id: str,
    page_size: int,
    summary: dict | None = None,
) -> None:
    """失败/未接通时把真实结论落库（**先落盘再抛**，路由回滚也抹不掉）。

    断点**故意不动**：`page_token` 留着，下一次采集就从这里续。

    落盘本身再失败时**不能顶替原始错误**：原始错误才是"为什么采集失败"的答案，
    而落盘失败只说明库有问题（那时调用栈已经指向库了）。
    """
    try:
        cursor.status = status
        cursor.last_error = _clip(f"{type(error).__name__}: {error}")
        cursor.last_finished_at = _now()
        cursor.lease_expires_at = None
        if status == CURSOR_FAILED:
            cursor.retry_count = (cursor.retry_count or 0) + 1
        await _write_collect_log(
            session,
            provider=provider,
            object_type=object_type,
            shop_id=shop_id,
            page_size=page_size,
            # 未配置/未验收 = 明确没发出去，用 not_sent 与真正的"失败"分开。
            status=LOG_STATUS_FAILED if status == CURSOR_FAILED else LOG_STATUS_NOT_SENT,
            summary=summary,
            error=error,
        )
        await session.commit()
    except Exception:  # noqa: BLE001 - 见 docstring：不覆盖原始异常
        pass


# ---------------------------------------------------------------- 查询与人工动作


async def list_cursors(
    session: AsyncSession, *, system_type: str | None = None
) -> list[dict]:
    stmt = select(ExternalSyncWatermark)
    if system_type:
        stmt = stmt.where(ExternalSyncWatermark.system_type == system_type)
    rows = (
        await session.execute(
            stmt.order_by(
                ExternalSyncWatermark.system_type,
                ExternalSyncWatermark.shop_id,
                ExternalSyncWatermark.object_type,
            )
        )
    ).scalars().all()
    return [serialize_cursor(row) for row in rows]


def serialize_record(row: ExternalRecord) -> dict:
    return {
        "id": row.id,
        "system_type": row.system_type,
        "shop_id": row.shop_id,
        "object_type": row.object_type,
        "dedupe_key": row.dedupe_key,
        "parent_key": row.parent_key,
        "order_key": row.order_key,
        "kind": row.kind,
        "line_key": row.line_key,
        "external_id": row.external_id,
        "external_code": row.external_code,
        "quantity": None if row.quantity is None else float(row.quantity),
        "amount": None if row.amount is None else float(row.amount),
        "currency": row.currency,
        "occurred_at": row.occurred_at,
        "external_updated_at": row.external_updated_at,
        "first_seen_at": row.first_seen_at,
        "last_seen_at": row.last_seen_at,
        "collected_count": row.collected_count,
        "last_batch_id": row.last_batch_id,
        "raw_digest": row.raw_digest,
        "payload": row.payload,
    }


def serialize_mapping(row: ExternalObjectMapping) -> dict:
    return {
        "id": row.id,
        "system_type": row.system_type,
        "shop_id": row.shop_id,
        "object_type": row.object_type,
        "external_id": row.external_id,
        "external_code": row.external_code,
        "parent_key": row.parent_key,
        "internal_id": row.internal_id,
        "match_status": row.match_status,
        "match_basis": row.match_basis,
        "match_basis_label": BASIS_LABELS.get(row.match_basis or "", row.match_basis),
        "match_note": row.match_note,
        "first_seen_at": row.first_seen_at,
        "last_seen_at": row.last_seen_at,
        "matched_at": row.matched_at,
        "matched_by": row.matched_by,
    }


def visible_record_condition(owner_ids: list[int] | None):
    """原始事实的**数据范围**条件（None = 全量，不加条件）。

    外部事实自己没有负责人，所以只能顺着映射找到本地订单来判定范围。
    口径与集成日志一致：**看不到归属就看不到** —— 未匹配的外部事实只给全量范围的人，
    否则任何有 order:view 的人都能读到别家订单的原始报文。

    明细行用 `parent_key` 回到父单（主对象的父键为空，退回自己的键），
    这样"订单可见"时它的明细也可见，不需要额外授权一次。
    """
    if owner_ids is None:
        return None
    scoped_orders = select(SalesOrder.id).where(SalesOrder.owner_id.in_(owner_ids or [0]))
    # 归属键优先取 `order_key`（明细行上才有的订单归属），退回父单键，最后退回自己。
    effective_parent = func.coalesce(
        ExternalRecord.order_key, ExternalRecord.parent_key, ExternalRecord.dedupe_key
    )
    mapping_exists = (
        select(ExternalObjectMapping.id)
        .where(
            ExternalObjectMapping.system_type == ExternalRecord.system_type,
            ExternalObjectMapping.shop_id == ExternalRecord.shop_id,
            ExternalObjectMapping.object_type == OBJECT_ORDER,
            ExternalObjectMapping.external_id == effective_parent,
            ExternalObjectMapping.internal_id.in_(scoped_orders),
        )
        .exists()
    )
    # 客户/SKU 映射没有订单归属，非全量范围的人看不到（宁可少给，不可越权给）。
    order_like = ExternalRecord.object_type.in_(
        (
            OBJECT_ORDER,
            OBJECT_ORDER_ITEM,
            "shipment",
            OBJECT_SHIPMENT_ITEM,
            "aftersale",
            OBJECT_AFTERSALE_ITEM,
        )
    )
    return order_like & mapping_exists


async def list_records(
    session: AsyncSession,
    *,
    owner_ids: list[int] | None,
    system_type: str | None = None,
    shop_id: str | None = None,
    object_type: str | None = None,
    parent_key: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    stmt: Select = select(ExternalRecord)
    scope = visible_record_condition(owner_ids)
    if scope is not None:
        stmt = stmt.where(scope)
    if system_type:
        stmt = stmt.where(ExternalRecord.system_type == system_type)
    if shop_id:
        stmt = stmt.where(ExternalRecord.shop_id == shop_id)
    if object_type:
        stmt = stmt.where(ExternalRecord.object_type == object_type)
    if parent_key:
        stmt = stmt.where(
            or_(
                ExternalRecord.parent_key == parent_key,
                ExternalRecord.dedupe_key == parent_key,
            )
        )
    total = int(
        (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    )
    rows = (
        await session.execute(
            stmt.order_by(ExternalRecord.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return [serialize_record(row) for row in rows], total


async def get_record(session: AsyncSession, record_id: int) -> ExternalRecord:
    row = await session.get(ExternalRecord, record_id)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, f"原始事实 #{record_id} 不存在", 404)
    return row


async def record_is_visible(
    session: AsyncSession, record_id: int, owner_ids: list[int] | None
) -> bool:
    """**单条**原始事实是否落在当前数据范围内（None = 全量范围，直接放行）。

    为什么要单独一个函数（2026-10-07 修）：详情接口原来用
    `list_records(page=1, page_size=1)` 取"可见的第一条"，再判断请求的那条在不在其中 ——
    用户有两条以上合法记录时，**只要请求的不是排序第一条就会被误判无权**（合法访问被 403）。
    列表过滤与详情判定必须用**同一份判据**（都走 `visible_record_condition`），
    而且详情要**针对这条记录**算，不能用"它在列表里出现过"来代替。
    """
    scope = visible_record_condition(owner_ids)
    if scope is None:
        return True
    stmt = select(ExternalRecord.id).where(ExternalRecord.id == record_id, scope)
    return (await session.execute(stmt)).first() is not None


async def get_mapping(session: AsyncSession, mapping_id: int) -> ExternalObjectMapping:
    row = await session.get(ExternalObjectMapping, mapping_id)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, f"外部对象映射 #{mapping_id} 不存在", 404)
    return row


async def list_mappings(
    session: AsyncSession,
    *,
    owner_ids: list[int] | None,
    match_status: str | None = None,
    system_type: str | None = None,
    shop_id: str | None = None,
    object_type: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    stmt: Select = select(ExternalObjectMapping)
    if owner_ids is not None:
        # 待匹配的行没有本地归属，只有全量范围的人能看/能处理（同异常队列口径）。
        scoped_orders = select(SalesOrder.id).where(SalesOrder.owner_id.in_(owner_ids or [0]))
        stmt = stmt.where(ExternalObjectMapping.internal_id.in_(scoped_orders))
    if match_status:
        stmt = stmt.where(ExternalObjectMapping.match_status == match_status)
    if system_type:
        stmt = stmt.where(ExternalObjectMapping.system_type == system_type)
    if shop_id:
        stmt = stmt.where(ExternalObjectMapping.shop_id == shop_id)
    if object_type:
        stmt = stmt.where(ExternalObjectMapping.object_type == object_type)
    total = int(
        (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    )
    rows = (
        await session.execute(
            stmt.order_by(ExternalObjectMapping.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return [serialize_mapping(row) for row in rows], total


async def replay_pending_mappings(
    session: AsyncSession,
    *,
    system_type: str | None = None,
    shop_id: str | None = None,
    object_type: str | None = None,
    limit: int = 500,
    now: datetime | None = None,
) -> dict:
    """重放待匹配行：本地对象补齐后，用**已有**的外部事实重新匹配，不必重拉。

    为什么必须有它：采集是定时的，而本地补建客户/SKU 是随时发生的。
    没有重放，补完还得等下一次采集 —— 或者更糟，有人去手工改映射表。
    """
    moment = now or _now()
    stmt = select(ExternalObjectMapping).where(
        ExternalObjectMapping.match_status.in_((MATCH_PENDING, MATCH_CONFLICT))
    )
    if system_type:
        stmt = stmt.where(ExternalObjectMapping.system_type == system_type)
    if shop_id:
        stmt = stmt.where(ExternalObjectMapping.shop_id == shop_id)
    if object_type:
        stmt = stmt.where(ExternalObjectMapping.object_type == object_type)
    rows = (
        await session.execute(stmt.order_by(ExternalObjectMapping.id.asc()).limit(limit))
    ).scalars().all()

    matched = 0
    still_pending = 0
    for row in rows:
        internal_id, basis, note, status = await resolve_mapping(session, row)
        row.internal_id = internal_id
        row.match_basis = basis
        row.match_note = note
        row.match_status = status
        if status == MATCH_MATCHED:
            row.matched_at = moment
            matched += 1
        elif status == MATCH_PENDING:
            still_pending += 1
    await session.flush()
    return {"checked": len(rows), "matched": matched, "still_pending": still_pending}


async def assign_mapping(
    session: AsyncSession,
    mapping: ExternalObjectMapping,
    *,
    internal_id: int,
    operator_id: int | None,
    note: str | None = None,
    now: datetime | None = None,
) -> dict:
    """人工把一条待匹配的外部对象指到本地对象上（补齐后可重放的人工出口）。"""
    if internal_id is None:
        raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "internal_id 必填", 422)
    moment = now or _now()
    mapping.internal_id = int(internal_id)
    mapping.match_status = MATCH_MATCHED
    mapping.match_basis = "manual"
    mapping.match_note = note or f"人工指定本地对象 #{internal_id}"
    mapping.matched_at = moment
    mapping.matched_by = operator_id
    await session.flush()
    # 父单匹配上了，它的明细行通常立刻能跟上：顺手重放一次，省一轮人工操作。
    replayed = await replay_pending_mappings(
        session,
        system_type=mapping.system_type,
        shop_id=mapping.shop_id,
        now=moment,
    )
    return {"mapping": serialize_mapping(mapping), "replayed": replayed}


async def collection_status(session: AsyncSession, *, system_type: str | None = None) -> dict:
    """采集侧的总览：水位/断点/未接通状态 + 来源核实台账 + 待匹配计数。

    这是"如实显示未接通"的对外出口：`verified=False` 的来源一律列进
    `unverified_sources`，而不是在页面上写一句静态提示。
    """
    rows = (
        await session.execute(
            select(ExternalSourceRegistry).order_by(
                ExternalSourceRegistry.system_type,
                ExternalSourceRegistry.shop_id,
                ExternalSourceRegistry.source_kind,
            )
        )
    ).scalars().all()
    sources = [
        {
            "system_type": row.system_type,
            "shop_id": row.shop_id,
            "source_kind": row.source_kind,
            "verified": bool(row.verified),
            "status": SOURCE_VERIFIED if row.verified else SOURCE_UNVERIFIED,
            "evidence": row.evidence,
            "authorization_note": row.authorization_note,
            "verified_at": row.verified_at,
        }
        for row in rows
    ]
    pending_mappings = int(
        (
            await session.execute(
                select(func.count())
                .select_from(ExternalObjectMapping)
                .where(ExternalObjectMapping.match_status == MATCH_PENDING)
            )
        ).scalar_one()
    )
    records = int(
        (await session.execute(select(func.count()).select_from(ExternalRecord))).scalar_one()
    )
    adapter = get_adapter()
    return {
        "adapter": adapter.label,
        "system_type": getattr(adapter, "system_type", "ERP"),
        "collection_capability": adapter.capability("collect").as_dict(),
        "supported_object_types": list(adapter.collection_object_types()),
        "cursors": await list_cursors(session, system_type=system_type),
        "sources": sources,
        "unverified_sources": [item for item in sources if not item["verified"]],
        "records": records,
        "pending_mappings": pending_mappings,
    }


__all__ = [
    "BASIS_LABELS",
    "DEFAULT_PAGE_SIZE",
    "LEASE_SECONDS",
    "MAX_PAGES_PER_RUN",
    "assign_mapping",
    "collect_once",
    "collection_status",
    "get_mapping",
    "get_or_create_cursor",
    "get_record",
    "get_source_state",
    "list_cursors",
    "list_mappings",
    "list_records",
    "normalize_item",
    "record_digest",
    "register_source",
    "replay_pending_mappings",
    "resolve_mapping",
    "serialize_cursor",
    "serialize_mapping",
    "serialize_record",
    "visible_record_condition",
]
