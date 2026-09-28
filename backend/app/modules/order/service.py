"""订单业务逻辑。"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
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

    `generate_for` 带自愈：候选号被占用就跳过、计数器从库里最大号播种，
    避免计数器与已发布号脱节时永久卡死（详见 numbering.py）。
    """
    from app.modules.settings import numbering

    return await numbering.generate_for(
        session, "order", model=SalesOrder, column=SalesOrder.order_no
    )


async def get_order_or_404(session: AsyncSession, order_id: int) -> SalesOrder:
    order = await session.get(SalesOrder, order_id)
    if order is None:
        raise AppError(ErrorCode.NOT_FOUND, "订单不存在", 404)
    return order


async def get_visible_order(session: AsyncSession, user, order_id: int) -> SalesOrder:
    """取订单并校验数据范围（列表按 owner_id 过滤，详情此前没校验）。"""
    from app.core.data_scope import ensure_in_scope

    order = await get_order_or_404(session, order_id)
    await ensure_in_scope(session, user, owner_id=order.owner_id, label="订单")
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
    if quote is None or quote.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "报价单不存在或已删除", 404)
    if version.approval_status != "approved":
        raise AppError(ErrorCode.APPROVAL_PENDING, "报价未通过审批，不能转订单", 422)
    # 已失效报价不能转单（方案 A13：有效性校验；此前的口子允许过期报价转单）
    today = datetime.now(UTC).date()
    if quote.valid_until and quote.valid_until < today:
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"报价已过有效期（{quote.valid_until}），请刷新版本后再转订单",
            422,
        )

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
    try:
        await session.flush()
    except IntegrityError:
        # 并发转单兜底：quote_version_id 唯一索引。串行重试已由上方 existing 检查
        # 返回 40903；两个请求同时点到这里若不接住会漏到全局兜底变成 500
        await session.rollback()
        raise AppError(
            ErrorCode.DUPLICATE_CONVERT,
            "该报价版本已转过订单（或正在并发转单），请刷新后重试",
            409,
        )

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
    # 订单→应收（方案 §6）：转单即生成一条全款应收计划，避免"建单靠记性建应收"；
    # 需要拆定金/尾款时在订单详情页删除后按比例重生成
    session.add(
        ReceivablePlan(
            order_id=order.id,
            plan_name="全款",
            due_date=delivery_date or (today + timedelta(days=30)),
            amount=version.total_amount.quantize(Decimal("0.01")),
            currency=version.currency,
            status="pending",
            remark="转单自动生成，可在订单详情调整或拆分",
            created_at=datetime.now(UTC),
        )
    )
    await session.flush()
    return order


async def create_order(
    session: AsyncSession,
    *,
    user_id: int,
    customer_id: int,
    items: list,
    opportunity_id: int | None = None,
    quote_id: int | None = None,
    owner_id: int | None = None,
    currency: str = "CNY",
    delivery_date: date | None = None,
    payment_terms: str | None = None,
    remark: str | None = None,
) -> SalesOrder:
    """手工建销售订单（03-API §27 POST /orders）。

    总金额由明细算出来，不接受前端传 —— 两个来源必然漂移。
    明细的 `unit_price` 直接落库（不比价、不套价格规则）：
    线下签约的成交价就是谈定的数字，系统不该替业务改。
    """
    from app.modules.product.model import Sku

    total = Decimal(0)
    rows: list[SalesOrderItem] = []
    for entry in items:
        sku = await session.get(Sku, entry.sku_id)
        if sku is None or sku.deleted_at is not None:
            raise AppError(ErrorCode.NOT_FOUND, f"SKU id={entry.sku_id} 不存在", 404)
        amount = (entry.quantity * entry.unit_price).quantize(Decimal("0.01"))
        total += amount
        rows.append(
            SalesOrderItem(
                order_id=0,  # flush 后回填
                sku_id=sku.id,
                sku_snapshot=sku.name or sku.sku_code,
                specification=entry.specification or sku.specification,
                quantity=entry.quantity,
                unit_price=entry.unit_price,
                amount=amount,
                remark=entry.remark,
            )
        )

    order = SalesOrder(
        order_no=await generate_order_no(session),
        customer_id=customer_id,
        opportunity_id=opportunity_id,
        quote_id=quote_id,
        quote_version_id=None,
        owner_id=owner_id or user_id,
        total_amount=total,
        currency=currency,
        status="pending",
        delivery_date=delivery_date,
        payment_terms=payment_terms,
        remark=remark,
        created_by=user_id,
    )
    session.add(order)
    await session.flush()
    for row in rows:
        row.order_id = order.id
        session.add(row)
    session.add(
        OrderStatusHistory(
            order_id=order.id,
            old_status=None,
            new_status="pending",
            source="WEB",
            operator_id=user_id,
            remark="手工创建订单",
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
