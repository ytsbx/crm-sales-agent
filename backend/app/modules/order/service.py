"""订单业务逻辑。"""

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.modules.customer.model import Customer
from app.modules.integration.model import ExternalMapping
from app.modules.order.model import ORDER_STATUS_LABEL, OrderStatusHistory, SalesOrder, SalesOrderItem
from app.modules.payment.model import PaymentRecord, ReceivablePlan
from app.modules.quote.model import QuoteItem, QuoteVersion
from app.modules.user.model import User


def _f(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def serialize_order(
    order: SalesOrder,
    *,
    customer_name: str | None = None,
    owner_name: str | None = None,
    received_amount: Decimal | None = None,
    item_count: int = 0,
) -> dict:
    received = received_amount or Decimal(0)
    return {
        "id": order.id,
        "order_no": order.order_no,
        "customer_id": order.customer_id,
        "customer_name": customer_name,
        "opportunity_id": order.opportunity_id,
        "quote_id": order.quote_id,
        "quote_version_id": order.quote_version_id,
        "owner_id": order.owner_id,
        "owner_name": owner_name,
        "total_amount": _f(order.total_amount),
        "received_amount": _f(received),
        "unreceived_amount": _f((order.total_amount or Decimal(0)) - received),
        "currency": order.currency,
        "status": order.status,
        "status_label": ORDER_STATUS_LABEL.get(order.status, order.status),
        "erp_order_id": order.erp_order_id,
        "delivery_date": order.delivery_date,
        "payment_terms": order.payment_terms,
        "remark": order.remark,
        "item_count": item_count,
        "cancelled_at": order.cancelled_at,
        "created_at": order.created_at,
    }


def serialize_item(item: SalesOrderItem, sku_code: str | None = None) -> dict:
    return {
        "id": item.id,
        "order_id": item.order_id,
        "sku_id": item.sku_id,
        "sku_code": sku_code,
        "sku_snapshot": item.sku_snapshot,
        "specification": item.specification,
        "quantity": _f(item.quantity),
        "unit_price": _f(item.unit_price),
        "amount": _f(item.amount),
        "remark": item.remark,
    }


async def generate_order_no(session: AsyncSession) -> str:
    """按「编号规则」取订单号（PRD §2.6）。

    原先的 `count(*)+1` 会因删除历史单而重号（order_no 有唯一约束，
    真撞上就是 500），并发也不安全。改走行锁计数器，默认格式不变。
    """
    from app.modules.settings import numbering

    return await numbering.next_number(session, "order")


async def get_order_or_404(session: AsyncSession, order_id: int) -> SalesOrder:
    order = await session.get(SalesOrder, order_id)
    if order is None:
        raise AppError(ErrorCode.NOT_FOUND, "订单不存在", 404)
    return order


async def received_amount(session: AsyncSession, order_id: int) -> Decimal:
    """已收金额只统计财务已确认的回款。"""
    total = (
        await session.execute(
            select(func.coalesce(func.sum(PaymentRecord.received_amount), 0)).where(
                PaymentRecord.order_id == order_id,
                PaymentRecord.status == "confirmed",
            )
        )
    ).scalar_one()
    return Decimal(total)


async def create_order_from_quote(
    session: AsyncSession,
    *,
    version: QuoteVersion,
    user_id: int,
    delivery_date: date | None = None,
    remark: str | None = None,
) -> SalesOrder:
    """报价版本转销售订单：必须已通过审批；同一版本只能转一次（幂等）。"""
    existing = (
        await session.execute(
            select(SalesOrder).where(SalesOrder.quote_version_id == version.id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise AppError(
            ErrorCode.DUPLICATE_CONVERT,
            f"该报价版本已经转过订单（{existing.order_no}）",
            409,
        )

    from app.modules.quote.model import Quote

    quote = await session.get(Quote, version.quote_id)
    if quote is None:
        raise AppError(ErrorCode.NOT_FOUND, "报价单不存在", 404)
    if version.approval_status != "approved":
        raise AppError(ErrorCode.APPROVAL_PENDING, "报价未通过审批，不能转订单", 422)

    order = SalesOrder(
        order_no=await generate_order_no(session),
        customer_id=quote.customer_id,
        opportunity_id=quote.opportunity_id,
        quote_id=quote.id,
        quote_version_id=version.id,
        owner_id=quote.owner_id,
        total_amount=version.total_amount,
        currency=version.currency,
        status="pending",
        delivery_date=delivery_date,
        payment_terms=version.payment_terms,
        remark=remark or version.remark,
        created_by=user_id,
    )
    session.add(order)
    await session.flush()

    items = (
        await session.execute(
            select(QuoteItem).where(QuoteItem.quote_version_id == version.id)
        )
    ).scalars().all()
    for item in items:
        session.add(
            SalesOrderItem(
                order_id=order.id,
                sku_id=item.sku_id,
                sku_snapshot=item.sku_name_snapshot or item.sku_code_snapshot,
                specification=item.spec_snapshot,
                quantity=item.quantity,
                unit_price=item.quoted_price,
                amount=(item.quantity * item.quoted_price).quantize(Decimal("0.01")),
                remark=item.remark,
            )
        )
    session.add(
        OrderStatusHistory(
            order_id=order.id,
            old_status=None,
            new_status="pending",
            source="WEB",
            operator_id=user_id,
            remark="由报价版本转订单",
            created_at=datetime.now(UTC),
        )
    )
    await session.flush()
    return order


async def change_status(
    session: AsyncSession,
    order: SalesOrder,
    *,
    new_status: str,
    operator_id: int | None,
    source: str = "WEB",
    remark: str | None = None,
) -> None:
    if new_status not in ORDER_STATUS_LABEL:
        raise AppError(ErrorCode.PARAM_ERROR, f"未知的订单状态：{new_status}")
    if order.status == new_status:
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "订单已经是该状态")
    if order.status == "cancelled":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "已取消的订单不能再变更状态")
    session.add(
        OrderStatusHistory(
            order_id=order.id,
            old_status=order.status,
            new_status=new_status,
            source=source,
            operator_id=operator_id,
            remark=remark,
            created_at=datetime.now(UTC),
        )
    )
    order.status = new_status
    if new_status == "cancelled":
        order.cancelled_at = datetime.now(UTC)


async def record_external_order_id(
    session: AsyncSession, *, order_id: int, external_id: str, system_type: str = "MES"
) -> None:
    session.add(
        ExternalMapping(
            system_type=system_type,
            business_type="order",
            internal_id=order_id,
            external_id=external_id,
            last_sync_at=datetime.now(UTC),
        )
    )


async def order_context(session: AsyncSession, orders: list[SalesOrder]) -> dict:
    customer_ids = {o.customer_id for o in orders}
    owner_ids = {o.owner_id for o in orders if o.owner_id}
    order_ids = [o.id for o in orders]

    customers = {
        int(cid): name
        for cid, name in (
            await session.execute(select(Customer.id, Customer.name).where(Customer.id.in_(customer_ids)))
        ).all()
    } if customer_ids else {}
    owners = {
        int(uid): name
        for uid, name in (
            await session.execute(select(User.id, User.name).where(User.id.in_(owner_ids)))
        ).all()
    } if owner_ids else {}
    received: dict[int, Decimal] = {}
    counts: dict[int, int] = {}
    if order_ids:
        received = {
            int(oid): Decimal(total or 0)
            for oid, total in (
                await session.execute(
                    select(PaymentRecord.order_id, func.coalesce(func.sum(PaymentRecord.received_amount), 0))
                    .where(PaymentRecord.order_id.in_(order_ids), PaymentRecord.status == "confirmed")
                    .group_by(PaymentRecord.order_id)
                )
            ).all()
        }
        counts = {
            int(oid): int(count)
            for oid, count in (
                await session.execute(
                    select(SalesOrderItem.order_id, func.count(SalesOrderItem.id))
                    .where(SalesOrderItem.order_id.in_(order_ids))
                    .group_by(SalesOrderItem.order_id)
                )
            ).all()
        }
    return {"customers": customers, "owners": owners, "received": received, "counts": counts}


async def plan_summary(session: AsyncSession, plan: ReceivablePlan) -> dict:
    from app.modules.payment import service as payment_service

    return await payment_service.serialize_plan(session, plan)
