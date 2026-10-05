"""ERP/MES 集成业务逻辑（API §28）。

职责边界（总设计文档 §2.2）：CRM 只同步与展示订单履约关键状态，
**不复制整套 ERP/MES 能力**。所以这里只做四件事：
  1. 把销售订单推过去（幂等）；
  2. 拉回履约状态并写进 `order_status_history`（source=ERP）；
  3. 维护 `external_mappings` 的外部单号映射；
  4. 收发件时的集成日志留痕。

幂等的两层保证（02-ER §21）：
  - 先查 `sales_orders.erp_order_id` 与 `external_mappings`，已有外部单号直接返回；
  - 请求体带 `idempotency_key`（CRM 订单号），对方据此去重。
"""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.modules.customer.model import Customer
from app.modules.erp import adapter as erp_adapter
from app.modules.erp.adapter import ErpError, ErpNotConfigured, get_adapter
from app.modules.integration.model import ExternalMapping, IntegrationLog
from app.modules.order.model import ORDER_STATUS_LABEL, OrderStatusHistory, SalesOrder, SalesOrderItem
from app.core.data_scope import scoped_owner_ids
from app.core.deps import CurrentUser
from app.modules.product.model import Sku


def serialize_log(row: IntegrationLog) -> dict:
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


async def push_order(
    session: AsyncSession, *, order: SalesOrder, operator_id: int | None
) -> dict:
    """推送订单到 ERP/MES。已推送过就直接返回，不重复建单。"""
    adapter = get_adapter()

    # 幂等第一层：CRM 侧已有外部单号
    if order.erp_order_id:
        existing = (
            await session.execute(
                select(ExternalMapping).where(
                    ExternalMapping.system_type == adapter.system_type,
                    ExternalMapping.business_type == "order",
                    ExternalMapping.internal_id == order.id,
                )
            )
        ).scalars().first()
        return {
            "pushed": True,
            "already_synced": True,
            "erp_order_id": order.erp_order_id,
            "mapping": serialize_mapping(existing) if existing else None,
            "message": "该订单已经推送过，未重复建单",
        }

    payload = await build_order_payload(session, order)
    log = IntegrationLog(
        integration_type="erp",
        provider=adapter.label,
        direction="outbound",
        business_type="order",
        business_id=order.id,
        request_data=payload,
        status="pending",
    )
    session.add(log)
    await session.flush()

    try:
        result = await adapter.push_order(payload)
    except ErpNotConfigured:
        # 未配置：日志标 skipped，订单**不**标记已推送。
        # 注意这里要 commit：接口层捕获异常后会回滚会话，
        # 不先落盘的话这条"为什么没推成功"的记录会一起被回滚掉 ——
        # 而失败场景恰恰是最需要留下痕迹的。
        log.status = "skipped"
        log.error_message = "ERP/MES 未配置，未实际推送"
        await session.commit()
        raise
    except ErpError as error:
        log.status = "failed"
        log.error_message = str(error)
        await session.commit()
        raise

    external_id = str(result.get("external_id") or "")
    external_code = str(result.get("external_code") or payload["so_id"])
    log.status = "success"
    log.response_data = result.get("raw") or result

    order.erp_order_id = external_id or external_code
    mapping = (
        await session.execute(
            select(ExternalMapping).where(
                ExternalMapping.system_type == adapter.system_type,
                ExternalMapping.business_type == "order",
                ExternalMapping.internal_id == order.id,
            )
        )
    ).scalars().first()
    now = datetime.now(UTC)
    if mapping is None:
        mapping = ExternalMapping(
            system_type=adapter.system_type,
            business_type="order",
            internal_id=order.id,
        )
        session.add(mapping)
    mapping.external_id = external_id or None
    mapping.external_code = external_code or None
    mapping.last_sync_at = now

    session.add(
        OrderStatusHistory(
            order_id=order.id,
            old_status=order.status,
            new_status=order.status,
            source="ERP",
            operator_id=operator_id,
            remark=f"已推送 {adapter.label}，外部单号 {order.erp_order_id}",
            created_at=now,
        )
    )
    return {
        "pushed": True,
        "already_synced": False,
        "erp_order_id": order.erp_order_id,
        "mapping": serialize_mapping(mapping),
        "message": f"已推送到{adapter.label}",
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
        log.status = "skipped"
        log.error_message = "ERP/MES 未配置，未实际拉取"
        await session.commit()  # 同 push_order：失败痕迹必须先落盘再抛
        raise
    except ErpError as error:
        log.status = "failed"
        log.error_message = str(error)
        await session.commit()
        raise

    log.status = "success"
    log.response_data = result.get("raw") or result

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


async def apply_status_webhook(
    session: AsyncSession,
    *,
    order_no: str | None,
    erp_order_id: str | None,
    raw_status: str,
    remark: str | None = None,
) -> dict:
    """处理对方推送过来的状态变更（API §28 的两个 webhook 共用）。

    按 CRM 订单号或外部单号定位订单；找不到就记一条 inbound 失败日志，
    返回处理结果而不是抛异常——对方系统不该因为我们没这个单就反复重试。
    """
    adapter = get_adapter()
    stmt = select(SalesOrder)
    if order_no:
        stmt = stmt.where(SalesOrder.order_no == order_no)
    elif erp_order_id:
        stmt = stmt.where(SalesOrder.erp_order_id == erp_order_id)
    else:
        raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "order_no 或 erp_order_id 必填", 422)

    order = (await session.execute(stmt)).scalars().first()
    if order is None:
        session.add(
            IntegrationLog(
                integration_type="erp",
                provider=adapter.label,
                direction="inbound",
                business_type="order",
                status="failed",
                request_data={"order_no": order_no, "erp_order_id": erp_order_id},
                error_message="CRM 里找不到对应的销售订单",
            )
        )
        await session.commit()
        return {"matched": False, "changed": False, "message": "CRM 里找不到对应的销售订单"}

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
            session.add(
                IntegrationLog(
                    integration_type="erp",
                    provider=adapter.label,
                    direction="inbound",
                    business_type="order",
                    business_id=order.id,
                    status="failed",
                    request_data={"raw_status": raw_status, "order_no": order.order_no},
                    response_data={"mapped_status": new_status, "changed": False},
                    error_message=exc.message,
                )
            )
            await session.commit()
            return {
                "matched": True,
                "order_id": order.id,
                "order_no": order.order_no,
                "status": order.status,
                "changed": False,
                "raw_status": raw_status,
                "mapped": True,
                "message": exc.message,
            }
        # The shared service already adds the corresponding history row.
        changed = True

    session.add(
        IntegrationLog(
            integration_type="erp",
            provider=adapter.label,
            direction="inbound",
            business_type="order",
            business_id=order.id,
            status="success",
            request_data={"raw_status": raw_status, "order_no": order.order_no},
            response_data={"mapped_status": new_status, "changed": changed},
        )
    )
    await session.commit()
    return {
        "matched": True,
        "order_id": order.id,
        "order_no": order.order_no,
        "status": order.status,
        "changed": changed,
        "raw_status": raw_status,
        "mapped": bool(new_status),
    }


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


async def readiness(session: AsyncSession) -> dict:
    """ERP 就绪度：配置缺什么 + 已推送/未推送订单数。"""
    adapter = get_adapter()
    ready, missing = adapter.readiness()
    pushed = (
        await session.execute(
            select(func.count())
            .select_from(SalesOrder)
            .where(SalesOrder.erp_order_id.isnot(None))
        )
    ).scalar_one()
    total = (
        await session.execute(select(func.count()).select_from(SalesOrder))
    ).scalar_one()
    mappings = (
        await session.execute(
            select(func.count())
            .select_from(ExternalMapping)
            .where(ExternalMapping.business_type == "order")
        )
    ).scalar_one()
    logs = (
        await session.execute(
            select(func.count())
            .select_from(IntegrationLog)
            .where(IntegrationLog.integration_type == "erp")
        )
    ).scalar_one()
    from app.core.config import settings

    return {
        "provider": settings.erp_provider or None,
        "adapter": adapter.label,
        "ready": ready,
        "missing": missing,
        "configured": {
            "base_url": bool(settings.erp_base_url),
            "app_key": bool(settings.erp_app_key),
            "app_secret": bool(settings.erp_app_secret),
        },
        "counts": {
            "orders": int(total),
            "pushed": int(pushed),
            "not_pushed": int(total) - int(pushed),
            "mappings": int(mappings),
            "logs": int(logs),
        },
    }


__all__ = [
    "ErpError",
    "ErpNotConfigured",
    "erp_adapter",
    "apply_status_webhook",
    "build_order_payload",
    "push_order",
    "readiness",
    "refresh_status",
    "serialize_log",
    "serialize_mapping",
    "sync_logs",
]
