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
# 交期换算只有一份：`schedule.suggested_ship_date`（第九批 §9.8）。
# 订单详情、批次指标、交期分析三处共用它，避免同一个指标算出两个符号。
from app.modules.order.schedule import suggested_ship_date
# 业务时区的"今天"（第九批 §9.10）：批次逾期、报价有效性这些判断统一用它，
# 不再混用 `date.today()`（跟宿主机走）与 `now(UTC).date()`。
from app.core.timebase import today_business
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
        "delivery_kind": order.delivery_kind,
        "transit_days": order.transit_days,
        "plan_offsets": order.plan_offsets,
        # 建议发货日：**到货类交期要减掉运输天数**（第九批 §9.8 统一到
        # `schedule.suggested_ship_date` 算 —— 原来订单页、批次指标、交期分析
        # 各写一份，只有分析那处区分了交期类型，同一个指标能算出两个符号）。
        "shipment_date": suggested_ship_date(
            order.delivery_date, order.delivery_kind, order.transit_days
        ),
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
        "quote_item_id": item.quote_item_id,
        "source_snapshot": item.source_snapshot,
        "sku_code": sku_code,
        # 定制件（无 SKU）的溯源：不输出这两项，订单行在对客文件与页面上
        # 就只剩一个空 sku_id，"这是什么"说不清（字段存了却看不到）
        "inquiry_id": item.inquiry_id,
        "inquiry_no_snapshot": item.inquiry_no_snapshot,
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


async def get_visible_order(
    session: AsyncSession, user, order_id: int, *, for_update: bool = False
) -> SalesOrder:
    """取订单并校验数据范围（列表按 owner_id 过滤，详情此前没校验）。

    `for_update`：写入口要串行化。取消订单与登记发货是两条会互相否定的路径
    （第一批返修 §3.5：「同一时刻不能既取消又发货成功」）——两边都先锁整单，
    否则并发下两个请求各读到"还没变"的状态、双双通过各自的检查、双双提交。
    与 sample / contract 模块的 `get_visible_or_404(..., for_update=True)` 同一写法。
    """
    from app.core.data_scope import ensure_in_scope

    if for_update:
        order = (
            await session.execute(
                select(SalesOrder).where(SalesOrder.id == order_id)
                .with_for_update().execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if order is None:
            raise AppError(ErrorCode.NOT_FOUND, "订单不存在", 404)
    else:
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
    from app.modules.quote.model import Quote
    from app.modules.quote.lifecycle import ensure_current_version

    parent = await session.get(Quote, version.quote_id)
    if parent and parent.opportunity_id:
        from app.modules.opportunity.model import Opportunity
        await session.execute(select(Opportunity).where(Opportunity.id == parent.opportunity_id).with_for_update())
    quote = (await session.execute(select(Quote).where(Quote.id == version.quote_id)
             .with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
    if quote is None or quote.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "报价单不存在或已删除", 404)
    version = (await session.execute(select(QuoteVersion).where(QuoteVersion.id == version.id)
               .with_for_update().execution_options(populate_existing=True))).scalar_one()
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

    ensure_current_version(quote, version)
    customer = await session.get(Customer, quote.customer_id)
    if customer is None or customer.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "报价关联客户不存在或已删除，不能正式下单", 404)
    if version.approval_status != "approved":
        raise AppError(ErrorCode.APPROVAL_PENDING, "报价未通过审批，不能转订单", 422)
    if quote.status != "accepted" or version.accepted_at is None or version.sent_at is None or version.declined_at is not None:
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "只有客户已接受的当前报价版本才能正式下单", 422)
    # 已失效报价不能转单（方案 A13：有效性校验；此前的口子允许过期报价转单）。
    # "今天"取**业务时区**的今天（第九批 §9.10）：按 UTC 算的话，北京时间
    # 凌晨 0—8 点会把"昨天已经过期"的报价当成还有效。
    today = today_business()
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
                # 定制件（无 SKU）：把需求编号一起带过来，订单行才能溯源；
                # sku_snapshot 取"名称或需求编号"，让对客文件上说得清这是什么
                quote_item_id=item.id,
                source_snapshot={"quote_item_id": item.id, "sku_id": item.sku_id, "inquiry_id": item.inquiry_id,
                    "name": item.sku_name_snapshot or item.sku_code_snapshot or item.inquiry_no_snapshot,
                    "spec": item.spec_snapshot, "quantity": str(item.quantity), "unit_price": str(item.quoted_price), "remark": item.remark},
                inquiry_id=item.inquiry_id,
                inquiry_no_snapshot=item.inquiry_no_snapshot,
                sku_snapshot=(
                    item.sku_name_snapshot
                    or item.sku_code_snapshot
                    or item.inquiry_no_snapshot
                ),
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
        operator_id=user_id,
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
    # 定制件没有 sku_id：优先用快照，其次需求编号，最后才退回编号占位
    return (
        item.sku_snapshot
        or item.inquiry_no_snapshot
        or (f"SKU#{item.sku_id}" if item.sku_id else f"明细#{item.id}")
    )


async def _lock_order_row(session: AsyncSession, order_id: int) -> None:
    """锁住订单行 —— 排批次 / 取消批次 / 实发共用同一把锁（第九批 §9.6）。

    **必须带 `populate_existing=True`**：本项目的 session 是
    `expire_on_commit=False`，SQLAlchemy 默认**不用查询结果覆盖已加载对象**。
    少了这一行，行锁确实锁住了库里的行，但拿回来的属性还是内存里的旧值 ——
    "已经被别人改过"判不出来，等于没锁（并发断言会"时红时绿"）。

    锁顺序固定为「先订单、后批次」，四个入口都按这个顺序拿锁，才不会互相咬住。
    """
    await session.execute(
        select(SalesOrder)
        .where(SalesOrder.id == order_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def _reload_order(session: AsyncSession, order_id: int) -> SalesOrder:
    """等锁之后**重新读**订单：等待锁之前读到的状态与余额一律不用。"""
    return (
        await session.execute(
            select(SalesOrder)
            .where(SalesOrder.id == order_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


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
        if batch_status_by_id.get(row.batch_id) == "shipped":
            # 已发货结束的批次**按实发量占用**（第九批 §9.5）。
            #
            # 原来一律按 `planned_qty` 占用：订购 10、本批计划 10、实际只发 6 时，
            # 系统仍认为 10 件全被占着 →「未发量 4、未计划量 0」，
            # 剩余 4 件永远排不进新批次；而整单完成的闸门又看"未发量 > 0"，
            # 于是这单**既完不成、也补不了**，卡成死锁。
            #
            # 释放的只是"占用额度"：每个批次明细里**原始的 planned_qty 原样保留**
            # （"原计划 10、实际发 6"是事实，不美化、不改历史）。
            entry["planned"] += _d(row.shipped_qty)
            entry["shipped"] += _d(row.shipped_qty)
        else:
            entry["planned"] += _d(row.planned_qty)
    for entry in agg.values():
        entry["ordered"] = _f(entry["ordered"])
        entry["planned"] = _f(entry["planned"])
        entry["shipped"] = _f(entry["shipped"])
        entry["remaining"] = _f(max(_d(entry["ordered"]) - _d(entry["shipped"]), Decimal(0)))
        entry["unplanned"] = _f(max(_d(entry["ordered"]) - _d(entry["planned"]), Decimal(0)))

    batch_by_id = {batch.id: batch for batch in batches}
    # "今天"取业务时区（第九批 §9.10）：下面算"未发批次已经逾期几天"要用它，
    # 按 UTC 算会在北京时间凌晨差一天。
    _today = today_business()
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
        # 批次偏差（场景13「受影响节点、批次数量、实际状态和未发量」）：
        # 已发的按"实际 − 计划"算实际偏差；未发的按"今天 − 计划"算已逾期天数。
        # 判据落在这里而不是前端：同一套偏差要同时供页面、风险单和提醒使用，
        # 三处各算一遍必然漂移。
        if batch.planned_date is None:
            deviation_days = None
        elif batch.status == "shipped" and batch.actual_ship_date is not None:
            deviation_days = (batch.actual_ship_date - batch.planned_date).days
        else:
            deviation_days = (_today - batch.planned_date).days
        serialized_batches.append({
            "id": batch.id,
            "batch_no": batch.batch_no,
            "status": batch.status,
            "status_label": SHIPMENT_STATUS_LABEL.get(batch.status, batch.status),
            "planned_date": batch.planned_date,
            "actual_ship_date": batch.actual_ship_date,
            "logistics_company": batch.logistics_company,
            "tracking_no": batch.tracking_no,
            "deviation_days": deviation_days,
            #: 已发且晚于计划 = 真的晚；未发且已过期 = 已经拖了几天
            "late": bool(deviation_days is not None and deviation_days > 0),
            "overdue_reason": batch.overdue_reason,
            "remark": batch.remark,
            "items": rows,
        })

    # 整单是否发完：**逐明细按数量判**（不是数"还有几个批次没发"）。
    # §9.8 复审的坑：订购 10、已发 6、剩 4 件**根本没排新批次**时
    # `pending_batch_count` 是 0 —— 拿它当"发完了"会提前下最终结论。
    settled = bool(agg) and all(
        _d(v["ordered"]) - _d(v["shipped"]) <= 0 for v in agg.values()
    )

    # 「最后一批 vs 交期」的两个输入（第九批 §9.8）：
    # 建议发货日 = 客户交期换算（到货类减运输天数）；发货日取**实际最晚**那个。
    suggested_ship = suggested_ship_date(
        order.delivery_date, order.delivery_kind, order.transit_days
    )
    shipped_dates = [
        batch["actual_ship_date"]
        for batch in serialized_batches
        if batch["actual_ship_date"] is not None
    ]
    last_shipped_on = max(shipped_dates) if shipped_dates else None
    pending_batch_count = sum(
        1 for batch in serialized_batches if batch["status"] != "shipped"
    )

    return {
        "items": list(agg.values()),
        "batches": serialized_batches,
        "batch_by_id": batch_by_id,
        "summary": {
            "ordered": _f(sum(_d(v["ordered"]) for v in agg.values())),
            "planned": _f(sum(_d(v["planned"]) for v in agg.values())),
            "shipped": _f(sum(_d(v["shipped"]) for v in agg.values())),
            "remaining": _f(sum(_d(v["remaining"]) for v in agg.values())),
            "all_shipped": settled,
            # 分批口径的汇总：场景13 要能回答"是不是分批拖了交期"
            "batch_count": len(serialized_batches),
            "late_batch_count": sum(1 for b in serialized_batches if b["late"]),
            "max_deviation_days": max(
                (b["deviation_days"] for b in serialized_batches
                 if b["deviation_days"] is not None),
                default=None,
            ),
            # 最后一批（按**实际最晚发货日**）相对**建议发货日**晚了几天
            # —— 分批单最关心的那个数（第九批 §9.8）。两处修正：
            #
            # ① 原来拿"实际发货日 − 客户交期"直接相减，而客户交期可能是**到货日**
            #    —— 两个含义不同的日期相减是错的。客户要 10-20 到货、运输 7 天
            #    → 应 10-13 发货；实际 10-17 发，应体现"晚 4 天"，
            #    原来算出 -3（看着像提前发了）。现在与交期分析共用同一份换算。
            # ② "最后一批"按实际最晚发货日取（业务 2026-10-07 拍板）：
            #    编号大不等于发得晚，第 2 批晚于第 3 批发出时按编号会得出反的结论。
            # ③ **只有整单发完才给这个"最终结论"**（§9.8 复审）：订购 10、已发 6、
            #    剩 4 件而后面又没排新批次时，原来照样算出 "-2 天"，读起来像
            #    "这单晚了 2 天" —— 其实它根本没发完。未发完保持空值，
            #    进度由 `all_shipped` / `remaining` 说明（前端据此显示"未完成 · 还剩 N"）。
            "last_batch_vs_delivery_days": (
                (last_shipped_on - suggested_ship).days
                if settled and last_shipped_on and suggested_ship
                else None
            ),
            #: 这个偏差比的是**发货**，不是到货。系统里没有"实际到货日"这个
            #: 事实字段，所以不能声称知道实际到货延迟 —— 名字和这里都写清楚。
            "last_batch_vs_delivery_basis": "shipping",
            #: 客户交期换算出来的建议发货日（前端可拿它解释"还剩几天"）
            "suggested_ship_date": suggested_ship,
            #: 还没发完的批次数 —— 它们不参与上面的偏差，单独标出来
            "pending_batch_count": pending_batch_count,
        },
    }


async def create_shipment_batch(
    session: AsyncSession,
    order: SalesOrder,
    *,
    payload,  # ShipmentBatchCreate
    user_id: int,
) -> OrderShipmentBatch:
    """建批次。计划量不得超过该明细未计划量（订购 − 已计划），防重复排产。

    第九批 §9.6：这里原来**没有锁**（而发货登记有）。两个并发请求能同时读到
    "订购 10、未计划 10"，各排 8 件 → 总计划 16，两个批次的 `batch_no` 还都是 1。
    现在与 `ship_shipment_batch` 统一口径：**先锁订单行、刷新已加载对象，
    等锁之后再读余额与订单状态**（等待锁之前读到的值一律不用）。
    """
    await _lock_order_row(session, order.id)
    order = await _reload_order(session, order.id)
    if order.status == "cancelled":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "已取消的订单不能再排发货批次")

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

    # 先把整张计划**全部校验完**再落库（第九批 §9.7）：中途报错时不留半张批次。
    for row in payload.items:
        if row.order_item_id not in items:
            raise AppError(ErrorCode.NOT_FOUND, f"订单明细 {row.order_item_id} 不存在", 404)
        if _d(row.planned_qty) > unplanned.get(row.order_item_id, Decimal(0)):
            raise AppError(
                ErrorCode.PARAM_ERROR,
                f"{_sku_label(items[row.order_item_id])}：计划发货 "
                f"{row.planned_qty} 超过还可安排的 "
                f"{unplanned.get(row.order_item_id, Decimal(0))}",
            )

    # 取号看**全部批次（含已取消）**（第九批 §9.6）：只看未取消批次时，
    # 取消掉编号最大的那一批之后，新批次会复用该编号，与它对应的动态跟单节点
    # （按 `batch_no` 命名）错配。
    max_batch_no = (
        await session.execute(
            select(func.max(OrderShipmentBatch.batch_no)).where(
                OrderShipmentBatch.order_id == order.id
            )
        )
    ).scalar_one() or 0
    batch_no = int(max_batch_no) + 1

    batch = OrderShipmentBatch(
        order_id=order.id,
        batch_no=batch_no,
        planned_date=payload.planned_date,
        overdue_reason=payload.overdue_reason,
        remark=payload.remark,
        created_by=user_id,
        created_at=datetime.now(UTC),
    )
    try:
        # SAVEPOINT 包住插入：`(order_id, batch_no)` 的唯一约束是**兜底**，
        # 极端并发下撞上时也要给出可理解的业务错误，而不是一个 500。
        async with session.begin_nested():
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
    except IntegrityError as error:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            "该订单的批次号刚刚被占用（可能有人同时在排批次），请刷新后重试",
            409,
        ) from error
    # 第 2 批起按批次动态生成跟单节点（口径 2026-10-04）：首批对应「首批发货」，
    # 后续每批一个独立节点，"分批导致的延期"才统计得出来。
    from app.modules.order import milestones as milestones_svc

    await milestones_svc.ensure_batch_node(
        session, order.id, batch_no, payload.planned_date, created_by=user_id
    )
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
    # 统一锁顺序：**先锁订单、再锁批次**（与排批次 / 取消批次共用同一把锁），
    # 等待锁之后重新读数量余额与订单状态。
    await _lock_order_row(session, order.id)
    order = await _reload_order(session, order.id)
    batch = (await session.execute(
        select(OrderShipmentBatch).where(OrderShipmentBatch.id == batch.id)
        .with_for_update().execution_options(populate_existing=True)
    )).scalar_one()
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
    # 第九批 §9.7：报上来的明细必须**属于本批次**。
    # 原来循环遍历的是本批自己的明细、用 `ship_inputs.get(id, 计划量)` 取值 ——
    # 传一个不属于本批的明细号时它匹配不到，就被**静默丢弃**，本批明细反而按
    # 计划量全发（"给错误明细登记发 1 件"，结果把正确明细的计划 10 件全发出去）。
    if ship_inputs is not None:
        unknown = sorted(set(ship_inputs) - {row.order_item_id for row in batch_items})
        if unknown:
            raise AppError(
                ErrorCode.PARAM_ERROR,
                f"明细 {unknown[0]} 不属于本批次，请刷新页面后重新选择",
                422,
            )

    # **先把整批算完并校验，再落数量**（第九批 §9.7）：原来是边算边写，
    # 中途报错时前面的明细已经改过 `shipped_qty`，留下一半的脏数量。
    resolved: dict[int, Decimal] = {}
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
        resolved[row.order_item_id] = qty

    for row in batch_items:
        row.shipped_qty = resolved[row.order_item_id]

    batch.status = "shipped"
    batch.actual_ship_date = payload.actual_ship_date or today_business()
    batch.logistics_company = payload.logistics_company
    batch.tracking_no = payload.tracking_no
    # 逾期原因是"归因"，不是装饰：晚发了就把为什么晚记在这一批上，
    # 否则事后只能看到"晚了 5 天"，说不出是不是因为分批
    if payload.overdue_reason:
        batch.overdue_reason = payload.overdue_reason
    if payload.remark:
        batch.remark = payload.remark
    await session.flush()

    # 动态批次节点：实发时把实际日登记上（首批仍是人工登记，不动固定节点）
    from app.modules.order import milestones as milestones_svc

    await milestones_svc.mark_batch_shipped(
        session, order.id, batch.batch_no, batch.actual_ship_date
    )

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

    from app.modules.followup.service import record_and_notify

    quantities = "；".join(
        f"{row.sku_snapshot or row.order_item_id}：{row.shipped_qty}" for row in batch_items
    )
    await record_and_notify(
        session, customer_id=order.customer_id, operator_id=operator_id, owner_id=order.owner_id,
        title="登记实际发货",
        content=f"订单 {order.order_no} 第 {batch.batch_no} 批已实发，"
                f"实发日期 {batch.actual_ship_date}；本批数量：{quantities}",
        business_type="order", business_id=order.id, order_id=order.id,
        event_key=f"order:batch_ship:{batch.id}",
    )


async def cancel_shipment_batch(
    session: AsyncSession, batch: OrderShipmentBatch
) -> None:
    """取消一个还没发货的批次。

    第九批 §9.6：这里原来**没有加锁**，和排批次 / 实发并发时会读到过期状态。
    现在按统一顺序拿锁（先订单、后批次），再判状态。
    """
    await _lock_order_row(session, batch.order_id)
    batch = (
        await session.execute(
            select(OrderShipmentBatch)
            .where(OrderShipmentBatch.id == batch.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    if batch.status == "shipped":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "已发货的批次不能取消，请走退换流程")
    if batch.status == "cancelled":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "该批次已取消")
    batch.status = "cancelled"
    # 批次取消 → 撤掉它的动态节点，否则会凭空冒出个逾期的"第 N 批发货"
    from app.modules.order import milestones as milestones_svc

    await milestones_svc.drop_batch_node(session, batch.order_id, batch.batch_no)
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
