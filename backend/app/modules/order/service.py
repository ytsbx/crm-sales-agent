"""订单业务逻辑。"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.modules.customer.model import Customer
from app.modules.integration.model import ExternalMapping
from app.modules.order.model import (
    ORDER_STATUS_LABEL,
    SHIPMENT_STATUS_LABEL,
    OrderShipmentBatch,
    OrderShipmentBatchItem,
    OrderStatusHistory,
    SalesOrder,
    SalesOrderItem,
)
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
    sales_owner_name: str | None = None,
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
        # 签单归属（文档 :61）：与当前负责人不同时，界面要说明"业绩算谁"
        "sales_owner_id": order.sales_owner_id,
        "sales_owner_name": sales_owner_name,
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

    # 交期回退（模块⑤）：请求没带交期时，取该商机需求明细里最早的非空交期——
    # 销售在需求明细里填过一遍的交期，不该让跟单再手填一遍；
    # 同时这也是应收到期日和跟单里程碑倒推的起点（此前没交期就瞎猜 30 天）
    effective_delivery = delivery_date
    if effective_delivery is None and quote.opportunity_id:
        from app.modules.opportunity.model import OpportunityItem

        effective_delivery = (
            await session.execute(
                select(OpportunityItem.delivery_date)
                .where(
                    OpportunityItem.opportunity_id == quote.opportunity_id,
                    OpportunityItem.delivery_date.is_not(None),
                )
                .order_by(OpportunityItem.delivery_date.asc())
                .limit(1)
            )
        ).scalar_one_or_none()

    order = SalesOrder(
        order_no=await generate_order_no(session),
        customer_id=quote.customer_id,
        opportunity_id=quote.opportunity_id,
        quote_id=quote.id,
        quote_version_id=version.id,
        owner_id=quote.owner_id,
        # 签单归属此刻写死（文档 :61）：往后交接或手工改负责人，业绩都算这一个人
        sales_owner_id=quote.owner_id,
        total_amount=version.total_amount,
        currency=version.currency,
        status="pending",
        delivery_date=effective_delivery,
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
            due_date=effective_delivery or (today + timedelta(days=30)),
            amount=version.total_amount.quantize(Decimal("0.01")),
            currency=version.currency,
            status="pending",
            remark="转单自动生成，可在订单详情调整或拆分",
            created_at=datetime.now(UTC),
        )
    )
    # 领导六阶段口径"过程记录"：转单即下单事实，自动留痕 + 推业务主管
    # （confirm-win 与 convert-to-order 两条路都汇到这里）
    from app.modules.followup import service as followup_service

    await followup_service.record_and_notify(
        session,
        customer_id=quote.customer_id,
        owner_id=order.owner_id,
        title=f"订单已创建 {order.order_no}",
        content=f"{order.order_no} 金额 ¥{float(version.total_amount):,.2f}（由报价转单）",
        business_type="order",
        business_id=order.id,
        order_id=order.id,
        quote_id=quote.id,
        exclude_user_id=user_id,
        event_key=f"order:create:{order.id}",
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
        # 签单归属此刻写死（文档 :61）：往后交接或手工改负责人，业绩都算这一个人
        sales_owner_id=owner_id or user_id,
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
    if new_status == "completed":
        # 发货批次闸门（§3.5/场景13）：建了批次的订单，未发完不许整单完成——
        # "首批发货不能把整单标为完成"。没建批次的订单沿用老行为（历史口径）。
        overview = await order_shipments(session, order)
        if overview["batches"]:
            short = [row for row in overview["items"] if Decimal(str(row["remaining"])) > 0]
            if short:
                detail = "；".join(
                    f"{row['sku']} 还差 {row['remaining']}" for row in short[:5]
                )
                raise AppError(
                    ErrorCode.STATUS_NOT_ALLOWED,
                    f"整单还有未发量，不能标记完成（{detail}）——"
                    "请先登记发货批次或调整批次计划",
                )
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


# ---------------------------------------------------------------- 发货批次（§3.5/场景13）


def _d(value) -> Decimal:
    return Decimal(str(value or 0))


def _sku_label(item: SalesOrderItem) -> str:
    return item.sku_snapshot or f"SKU#{item.sku_id}"


async def order_shipments(session: AsyncSession, order: SalesOrder) -> dict:
    """批次列表 + 按订单明细的计划/实发/未发量（场景13 的"未发量均正确"）。"""
    items = (
        await session.execute(
            select(SalesOrderItem).where(SalesOrderItem.order_id == order.id)
        )
    ).scalars().all()
    batches = (
        await session.execute(
            select(OrderShipmentBatch)
            .where(
                OrderShipmentBatch.order_id == order.id,
                OrderShipmentBatch.status != "cancelled",
            )
            .order_by(OrderShipmentBatch.batch_no)
        )
    ).scalars().all()
    batch_ids = [batch.id for batch in batches]
    batch_items = (
        await session.execute(
            select(OrderShipmentBatchItem).where(
                OrderShipmentBatchItem.batch_id.in_(batch_ids)
            )
        )
    ).scalars().all() if batch_ids else []

    # 聚合：ordered 固定；planned/shipped 跨批次累加（已取消批次不计）
    agg: dict[int, dict] = {
        item.id: {
            "order_item_id": item.id,
            "sku": _sku_label(item),
            "specification": item.specification,
            "ordered": _d(item.quantity),
            "planned": Decimal(0),
            "shipped": Decimal(0),
        }
        for item in items
    }
    batch_status_by_id = {batch.id: batch.status for batch in batches}
    for row in batch_items:
        entry = agg.get(row.order_item_id)
        if entry is None:
            continue
        entry["planned"] += _d(row.planned_qty)
        if batch_status_by_id.get(row.batch_id) == "shipped":
            entry["shipped"] += _d(row.shipped_qty)
    for entry in agg.values():
        entry["ordered"] = _f(entry["ordered"])
        entry["planned"] = _f(entry["planned"])
        entry["shipped"] = _f(entry["shipped"])
        entry["remaining"] = _f(max(_d(entry["ordered"]) - _d(entry["shipped"]), Decimal(0)))
        entry["unplanned"] = _f(max(_d(entry["ordered"]) - _d(entry["planned"]), Decimal(0)))

    batch_by_id = {batch.id: batch for batch in batches}
    serialized_batches = []
    for batch in batches:
        rows = [
            {
                "order_item_id": row.order_item_id,
                "sku": (agg[row.order_item_id]["sku"] if row.order_item_id in agg else row.sku_snapshot),
                "planned_qty": _f(row.planned_qty),
                "shipped_qty": _f(row.shipped_qty),
            }
            for row in batch_items
            if row.batch_id == batch.id
        ]
        serialized_batches.append({
            "id": batch.id,
            "batch_no": batch.batch_no,
            "status": batch.status,
            "status_label": SHIPMENT_STATUS_LABEL.get(batch.status, batch.status),
            "planned_date": batch.planned_date,
            "actual_ship_date": batch.actual_ship_date,
            "logistics_company": batch.logistics_company,
            "tracking_no": batch.tracking_no,
            "remark": batch.remark,
            "items": rows,
        })

    return {
        "items": list(agg.values()),
        "batches": serialized_batches,
        "batch_by_id": batch_by_id,
        "summary": {
            "ordered": _f(sum(_d(v["ordered"]) for v in agg.values())),
            "planned": _f(sum(_d(v["planned"]) for v in agg.values())),
            "shipped": _f(sum(_d(v["shipped"]) for v in agg.values())),
            "remaining": _f(sum(_d(v["remaining"]) for v in agg.values())),
            "all_shipped": bool(agg) and all(
                _d(v["ordered"]) - _d(v["shipped"]) <= 0 for v in agg.values()
            ),
        },
    }


async def create_shipment_batch(
    session: AsyncSession,
    order: SalesOrder,
    *,
    payload,  # ShipmentBatchCreate
    user_id: int,
) -> OrderShipmentBatch:
    """建批次。计划量不得超过该明细未计划量（订购 − 已计划），防重复排产。"""
    items = {
        item.id: item
        for item in (
            await session.execute(
                select(SalesOrderItem).where(SalesOrderItem.order_id == order.id)
            )
        ).scalars().all()
    }
    overview = await order_shipments(session, order)
    unplanned = {row["order_item_id"]: _d(row["unplanned"]) for row in overview["items"]}

    for row in payload.items:
        if row.order_item_id not in items:
            raise AppError(ErrorCode.NOT_FOUND, f"订单明细 {row.order_item_id} 不存在", 404)
        if _d(row.planned_qty) > unplanned.get(row.order_item_id, Decimal(0)):
            raise AppError(
                ErrorCode.PARAM_ERROR,
                f"{_sku_label(items[row.order_item_id])}：计划发货 "
                f"{row.planned_qty} 超过未计划量 "
                f"{unplanned.get(row.order_item_id, Decimal(0))}",
            )

    batch_no = (
        max((batch.batch_no for batch in overview["batch_by_id"].values()), default=0) + 1
    )
    batch = OrderShipmentBatch(
        order_id=order.id,
        batch_no=batch_no,
        planned_date=payload.planned_date,
        remark=payload.remark,
        created_by=user_id,
        created_at=datetime.now(UTC),
    )
    session.add(batch)
    await session.flush()
    for row in payload.items:
        item = items[row.order_item_id]
        session.add(
            OrderShipmentBatchItem(
                batch_id=batch.id,
                order_item_id=row.order_item_id,
                sku_snapshot=_sku_label(item),
                planned_qty=_d(row.planned_qty),
                shipped_qty=Decimal(0),
            )
        )
    await session.flush()
    return batch


async def ship_shipment_batch(
    session: AsyncSession,
    order: SalesOrder,
    batch: OrderShipmentBatch,
    *,
    payload,  # ShipmentBatchShip
    operator_id: int | None,
) -> None:
    """登记实发：批次置 shipped、写实际日期与物流，推进订单状态到"已发货"。"""
    if batch.order_id != order.id:
        raise AppError(ErrorCode.NOT_FOUND, "批次不属于该订单", 404)
    if batch.status == "shipped":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "该批次已登记发货")
    if batch.status == "cancelled":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "该批次已取消")

    batch_items = (
        await session.execute(
            select(OrderShipmentBatchItem).where(
                OrderShipmentBatchItem.batch_id == batch.id
            )
        )
    ).scalars().all()
    if not batch_items:
        raise AppError(ErrorCode.PARAM_ERROR, "批次没有明细，先补计划量")

    overview = await order_shipments(session, order)
    shipped_before = {
        row["order_item_id"]: _d(row["shipped"]) for row in overview["items"]
    }
    ordered = {
        row["order_item_id"]: _d(row["ordered"]) for row in overview["items"]
    }
    ship_inputs = (
        {row.order_item_id: _d(row.shipped_qty) for row in payload.items}
        if payload.items is not None
        else None
    )
    for row in batch_items:
        qty = (
            ship_inputs.get(row.order_item_id, _d(row.planned_qty))
            if ship_inputs is not None
            else _d(row.planned_qty)
        )
        if qty < 0:
            raise AppError(ErrorCode.PARAM_ERROR, "实发量不能为负")
        total = shipped_before.get(row.order_item_id, Decimal(0)) + qty
        if total > ordered.get(row.order_item_id, Decimal(0)):
            raise AppError(
                ErrorCode.PARAM_ERROR,
                f"{row.sku_snapshot or row.order_item_id}：累计实发 {total} "
                f"超过订购量 {ordered.get(row.order_item_id, Decimal(0))}",
            )
        row.shipped_qty = qty

    batch.status = "shipped"
    batch.actual_ship_date = payload.actual_ship_date or datetime.now(UTC).date()
    batch.logistics_company = payload.logistics_company
    batch.tracking_no = payload.tracking_no
    if payload.remark:
        batch.remark = payload.remark
    await session.flush()

    # 首批/任一批实发只推进到"已发货"；completed 由 change_status 的未发量闸门把关
    if order.status in ("pending", "in_production"):
        await change_status(
            session,
            order,
            new_status="shipped",
            operator_id=operator_id,
            source="WEB",
            remark=f"第 {batch.batch_no} 批发货（{batch.tracking_no or '无单号'}）",
        )

    # §2.3 业务进展时钟：发货算客户活跃
    from app.modules.customer import service as customer_service

    await customer_service.touch_progress(session, order.customer_id)


async def cancel_shipment_batch(
    session: AsyncSession, batch: OrderShipmentBatch
) -> None:
    if batch.status == "shipped":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "已发货的批次不能取消，请走退换流程")
    batch.status = "cancelled"
    await session.flush()


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
    # 当前负责人与签单归属都要解析姓名：两者不同时界面要说明"业绩算谁"
    owner_ids = {o.owner_id for o in orders if o.owner_id}
    owner_ids |= {o.sales_owner_id for o in orders if o.sales_owner_id}
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
