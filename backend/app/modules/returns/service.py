"""销售退货的业务实现。

把“登记”和“核销”分开：
1. 业务员/主管登记：只锁数量、算货款，不动钱；
2. 财务处理：先冲未收应收，剩余转待退款；
3. 财务确认退款：钱真的付出去后才标记；
4. 登记可取消，财务处理后可作废并冲回；已退款不能作废。

所有写入口都先锁订单行，再锁退货单/应收期，避免并发下超退或重复冲账。
"""

from datetime import UTC, datetime
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.data_scope import ensure_in_scope, scoped_owner_ids
from app.core.errors import AppError, ErrorCode
from app.core.timebase import today_business
from app.modules.customer.model import Customer
from app.modules.followup.service import record_and_notify
from app.modules.order.model import (
    OrderShipmentBatch,
    OrderShipmentBatchItem,
    SalesOrder,
    SalesOrderItem,
)
from app.modules.order.service import get_order_or_404
from app.modules.payment import service as payment_svc
from app.modules.payment.model import PaymentRecord, ReceivablePlan
from app.modules.returns.model import (
    RETURN_REASON_LABEL,
    RETURN_STATUS_LABEL,
    SalesReturn,
    SalesReturnItem,
    SalesReturnPlanAdjustment,
)
from app.modules.returns.schema import ReturnCreate
from app.modules.settings import numbering
from app.modules.user.model import User

ZERO = Decimal("0")
CENT = Decimal("0.01")
UNIT = Decimal("0.0001")
#: 参与“占用量”的退货状态。cancelled/voided 不占，允许重新登记。
ACTIVE_RETURN_STATUSES = ("pending", "completed", "refunded")
#: 已对钱做过处理的退货状态。作废时要把这些单的冲减算进“已冲”分母。
SETTLED_RETURN_STATUSES = ("completed", "refunded")


def _money(value: Decimal | int | None) -> Decimal:
    return (Decimal(value or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


def _quantity(value: Decimal | int | None) -> Decimal:
    return Decimal(value or 0)


async def generate_return_no(session: AsyncSession) -> str:
    return await numbering.generate_for(
        session, "return", model=SalesReturn, column=SalesReturn.return_no
    )


async def get_visible_return(
    session: AsyncSession, user, return_id: int, *, for_update: bool = False
) -> SalesReturn:
    stmt = select(SalesReturn).where(SalesReturn.id == return_id)
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "退货单不存在", 404)
    order = await get_order_or_404(session, row.order_id)
    await ensure_in_scope(session, user, owner_id=order.owner_id, label="订单")
    return row


async def _active_quantities(
    session: AsyncSession, batch_item_ids: list[int]
) -> dict[int, Decimal]:
    if not batch_item_ids:
        return {}
    rows = (
        await session.execute(
            select(
                SalesReturnItem.shipment_batch_item_id,
                func.coalesce(func.sum(SalesReturnItem.quantity), 0),
            )
            .join(SalesReturn, SalesReturn.id == SalesReturnItem.return_id)
            .where(
                SalesReturnItem.shipment_batch_item_id.in_(batch_item_ids),
                SalesReturn.status.in_(ACTIVE_RETURN_STATUSES),
            )
            .group_by(SalesReturnItem.shipment_batch_item_id)
        )
    ).all()
    return {int(bid): _quantity(qty) for bid, qty in rows}


async def _active_item_amounts(
    session: AsyncSession, order_item_ids: list[int]
) -> dict[int, Decimal]:
    if not order_item_ids:
        return {}
    rows = (
        await session.execute(
            select(
                SalesReturnItem.order_item_id,
                func.coalesce(func.sum(SalesReturnItem.amount), 0),
            )
            .join(SalesReturn, SalesReturn.id == SalesReturnItem.return_id)
            .where(
                SalesReturnItem.order_item_id.in_(order_item_ids),
                SalesReturn.status.in_(ACTIVE_RETURN_STATUSES),
            )
            .group_by(SalesReturnItem.order_item_id)
        )
    ).all()
    return {int(oid): _money(amount) for oid, amount in rows}


async def returnable_overview(session: AsyncSession, order_id: int) -> dict:
    """订单还能退什么：只列已实发批次，数量扣掉已登记（未取消/未作废）的退货。"""
    rows = (
        await session.execute(
            select(
                OrderShipmentBatch.id,
                OrderShipmentBatch.batch_no,
                OrderShipmentBatch.actual_ship_date,
                OrderShipmentBatch.status,
                OrderShipmentBatchItem.id,
                OrderShipmentBatchItem.order_item_id,
                OrderShipmentBatchItem.sku_snapshot,
                OrderShipmentBatchItem.shipped_qty,
                SalesOrderItem.specification,
                SalesOrderItem.quantity,
                SalesOrderItem.amount,
                SalesOrderItem.unit_price,
            )
            .join(
                OrderShipmentBatchItem,
                OrderShipmentBatchItem.batch_id == OrderShipmentBatch.id,
            )
            .join(
                SalesOrderItem,
                SalesOrderItem.id == OrderShipmentBatchItem.order_item_id,
            )
            .where(
                OrderShipmentBatch.order_id == order_id,
                OrderShipmentBatch.status == "shipped",
            )
            .order_by(OrderShipmentBatch.batch_no.asc(), OrderShipmentBatchItem.id.asc())
        )
    ).all()
    active_qty = await _active_quantities(session, [int(row[4]) for row in rows])
    batches: dict[int, dict] = {}
    total_shipped = total_returned = ZERO
    for row in rows:
        (
            batch_id,
            batch_no,
            actual_date,
            _batch_status,
            batch_item_id,
            order_item_id,
            sku,
            shipped_qty,
            specification,
            order_qty,
            order_amount,
            order_unit_price,
        ) = row
        shipped = _quantity(shipped_qty)
        returned = active_qty.get(int(batch_item_id), ZERO)
        remaining = max(shipped - returned, ZERO)
        unit_price = (
            _money(order_amount) / _quantity(order_qty)
            if _quantity(order_qty) > 0
            else Decimal(order_unit_price or 0)
        )
        batch = batches.setdefault(
            int(batch_id),
            {
                "batch_id": int(batch_id),
                "batch_no": int(batch_no),
                "actual_ship_date": actual_date.isoformat() if actual_date else None,
                "items": [],
            },
        )
        batch["items"].append(
            {
                "shipment_batch_item_id": int(batch_item_id),
                "order_item_id": int(order_item_id),
                "sku": sku,
                "specification": specification,
                "shipped_quantity": float(shipped),
                "shipped_quantity_exact": format(shipped, "f"),
                "returned_quantity": float(returned),
                "returned_quantity_exact": format(returned, "f"),
                "remaining_quantity": float(remaining),
                "remaining_quantity_exact": format(remaining, "f"),
                "unit_price": float(_money(unit_price)),
                "unit_price_exact": format(unit_price, ".4f"),
            }
        )
        total_shipped += shipped
        total_returned += returned
    return {
        "order_id": order_id,
        "batches": list(batches.values()),
        "summary": {
            "shipped_quantity": float(total_shipped),
            "shipped_quantity_exact": format(total_shipped, "f"),
            "returned_quantity": float(total_returned),
            "returned_quantity_exact": format(total_returned, "f"),
            "remaining_quantity": float(max(total_shipped - total_returned, ZERO)),
            "remaining_quantity_exact": format(max(total_shipped - total_returned, ZERO), "f"),
        },
    }


async def create_return(
    session: AsyncSession,
    user,
    order: SalesOrder,
    payload: ReturnCreate,
    *,
    ip: str | None = None,
) -> SalesReturn:
    if (order.currency or "CNY") != "CNY":
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            "本项目只支持人民币订单，外币订单不能登记退货",
            422,
        )
    ids = [item.shipment_batch_item_id for item in payload.items]
    rows = (
        await session.execute(
            select(
                OrderShipmentBatchItem,
                OrderShipmentBatch,
                SalesOrderItem,
            )
            .join(
                OrderShipmentBatch,
                OrderShipmentBatch.id == OrderShipmentBatchItem.batch_id,
            )
            .join(
                SalesOrderItem,
                SalesOrderItem.id == OrderShipmentBatchItem.order_item_id,
            )
            .where(
                OrderShipmentBatchItem.id.in_(ids),
                OrderShipmentBatch.order_id == order.id,
                OrderShipmentBatch.status == "shipped",
                SalesOrderItem.order_id == order.id,
            )
        )
    ).all()
    found = {int(batch_item.id): (batch_item, batch, order_item) for batch_item, batch, order_item in rows}
    missing = [bid for bid in ids if bid not in found]
    if missing:
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"有 {len(missing)} 条发货明细不可退：只能退已实发的批次，"
            "未发货/已取消批次不能登记退货",
            422,
        )

    active_qty = await _active_quantities(session, ids)
    active_amount = await _active_item_amounts(
        session, [int(batch_item.order_item_id) for batch_item, _, _ in rows]
    )
    result_items: list[SalesReturnItem] = []
    goods_amount = ZERO
    for request_item in payload.items:
        batch_item, batch, order_item = found[request_item.shipment_batch_item_id]
        shipped = _quantity(batch_item.shipped_qty)
        already_qty = active_qty.get(int(batch_item.id), ZERO)
        remaining = max(shipped - already_qty, ZERO)
        qty = _quantity(request_item.quantity)
        if qty > remaining + UNIT:
            raise AppError(
                ErrorCode.STATUS_NOT_ALLOWED,
                f"{batch_item.sku_snapshot or batch_item.order_item_id} 第 {batch.batch_no} 批"
                f"已发 {shipped}、已退 {already_qty}，最多还能退 {remaining}，本次填了 {qty}",
                422,
            )
        order_qty = _quantity(order_item.quantity)
        if order_qty <= 0:
            raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "订单明细数量为 0，无法计算退货金额", 422)
        already_amount = active_amount.get(int(order_item.id), ZERO)
        if qty >= remaining - UNIT:
            # 最后一批按“订单明细剩余金额”倒算，避免多行单价四舍五入后差几分钱。
            amount = _money((order_item.amount or ZERO) - already_amount)
        else:
            unit_price = (Decimal(order_item.amount or 0) / order_qty).quantize(
                UNIT, rounding=ROUND_HALF_UP
            )
            amount = _money(qty * unit_price)
        if amount <= 0:
            raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "退货金额必须大于 0", 422)
        result_items.append(
            SalesReturnItem(
                order_item_id=int(order_item.id),
                shipment_batch_item_id=int(batch_item.id),
                batch_id=int(batch.id),
                sku_snapshot=batch_item.sku_snapshot,
                specification_snapshot=order_item.specification,
                quantity=qty,
                unit_price=(amount / qty).quantize(UNIT, rounding=ROUND_HALF_UP),
                amount=amount,
            )
        )
        goods_amount += amount

    return_row = SalesReturn(
        return_no=await generate_return_no(session),
        order_id=order.id,
        status="pending",
        reason_code=payload.reason_code,
        reason=payload.reason.strip(),
        return_date=payload.return_date or today_business(),
        currency="CNY",
        goods_amount=_money(goods_amount),
        freight_refund_amount=ZERO,
        total_amount=_money(goods_amount),
        offset_amount=ZERO,
        refund_amount=ZERO,
        created_by=user.id,
        remark=(payload.remark or "").strip() or None,
    )
    session.add(return_row)
    await session.flush()
    for item in result_items:
        item.return_id = return_row.id
        session.add(item)
    await session.flush()

    await write_audit(
        session,
        operator_id=user.id,
        action="create_return",
        business_type="sales_return",
        business_id=return_row.id,
        after={
            "return_no": return_row.return_no,
            "order_no": order.order_no,
            "goods_amount": format(return_row.goods_amount, ".2f"),
            "items": [
                {
                    "shipment_batch_item_id": item.shipment_batch_item_id,
                    "quantity": format(item.quantity, "f"),
                    "amount": format(item.amount, ".2f"),
                }
                for item in result_items
            ],
        },
        ip=ip,
    )
    await record_and_notify(
        session,
        customer_id=order.customer_id,
        owner_id=order.owner_id,
        operator_id=user.id,
        title="登记销售退货",
        content=(
            f"订单 {order.order_no} 登记退货 {return_row.return_no}，"
            f"货款 {format(return_row.goods_amount, '.2f')} 元，原因：{payload.reason.strip()}；"
            "待财务处理。"
        ),
        business_type="sales_return",
        business_id=return_row.id,
        order_id=order.id,
        event_key=f"order:return:{return_row.id}",
    )
    await session.flush()
    return return_row


async def cancel_return(
    session: AsyncSession,
    user,
    row: SalesReturn,
    reason: str,
    *,
    ip: str | None = None,
) -> SalesReturn:
    if row.status != "pending":
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"「{RETURN_STATUS_LABEL.get(row.status, row.status)}」的退货单不能取消；"
            "财务已处理的请走「作废退货」",
            422,
        )
    before = row.status
    row.status = "cancelled"
    row.cancelled_by = user.id
    row.cancelled_at = datetime.now(UTC)
    row.cancel_reason = reason.strip()
    await write_audit(
        session,
        operator_id=user.id,
        action="cancel_return",
        business_type="sales_return",
        business_id=row.id,
        before={"status": before},
        after={"status": row.status, "reason": row.cancel_reason},
        ip=ip,
    )
    await session.flush()
    return row


async def settle_return(
    session: AsyncSession,
    user,
    row: SalesReturn,
    *,
    freight_refund_amount: Decimal,
    settlement_note: str | None,
    ip: str | None = None,
) -> SalesReturn:
    if row.status != "pending":
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"「{RETURN_STATUS_LABEL.get(row.status, row.status)}」的退货单不能重复处理",
            422,
        )
    if row.currency != "CNY":
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED, "本项目只支持人民币退货", 422
        )
    order = await get_order_or_404(session, row.order_id)
    goods_total = _money(row.goods_amount)
    freight_total = _money(freight_refund_amount)
    total = _money(goods_total + freight_total)
    plans = (
        await session.execute(
            select(ReceivablePlan)
            .where(
                ReceivablePlan.order_id == order.id,
                ReceivablePlan.status != "cancelled",
            )
            .order_by(ReceivablePlan.due_date.desc(), ReceivablePlan.id.desc())
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalars().all()
    received = _money(
        (
            await session.execute(
                select(func.coalesce(func.sum(PaymentRecord.received_amount), 0)).where(
                    PaymentRecord.order_id == order.id,
                    PaymentRecord.status == "confirmed",
                )
            )
        ).scalar_one()
    )
    previous_offset = _money(
        (
            await session.execute(
                select(func.coalesce(func.sum(SalesReturn.offset_amount), 0)).where(
                    SalesReturn.order_id == order.id,
                    SalesReturn.id != row.id,
                    SalesReturn.status.in_(SETTLED_RETURN_STATUSES),
                )
            )
        ).scalar_one()
    )
    previous_refund = _money(
        (
            await session.execute(
                select(func.coalesce(func.sum(SalesReturn.refund_amount), 0)).where(
                    SalesReturn.order_id == order.id,
                    SalesReturn.id != row.id,
                    SalesReturn.status.in_(SETTLED_RETURN_STATUSES),
                )
            )
        ).scalar_one()
    )

    if order.status == "cancelled":
        # 订单已经取消：货款退款只可能来自确实收到的钱，没有应收可冲。
        # 运费是额外的公司成本，不占用“已收货款”的退款额度。
        offset = ZERO
        goods_refund = min(goods_total, max(received - previous_refund, ZERO))
        refund = _money(goods_refund + freight_total)
        if goods_refund < goods_total:
            settlement_note = (
                (settlement_note + "；") if settlement_note else ""
            ) + "订单已取消，货款差额仅登记不退款"
        plans = []
    else:
        if not plans:
            raise AppError(
                ErrorCode.STATUS_NOT_ALLOWED,
                "该订单还没有应收计划，请财务先生成应收计划，再处理退货",
                422,
            )
        plan_sum = _money(sum((plan.amount or ZERO) for plan in plans))
        expected = _money((order.total_amount or ZERO) - previous_offset)
        if abs(plan_sum - expected) > CENT:
            raise AppError(
                ErrorCode.STATUS_NOT_ALLOWED,
                f"应收计划合计 {plan_sum} 与订单净额 {expected} 不一致，"
                "请先在「应收计划」核对/修正，再处理退货",
                422,
            )
        paid = await payment_svc.confirmed_amounts(session, [plan.id for plan in plans])
        available = _money(
            sum((max(Decimal(plan.amount or 0) - paid.get(plan.id, ZERO), ZERO) for plan in plans), ZERO)
        )
        # 运费不参与冲应收：应收是货款，退运费是额外的公司成本。
        offset = min(goods_total, available)
        goods_refund = _money(goods_total - offset)
        if goods_refund > max(received - previous_refund, ZERO) + CENT:
            raise AppError(
                ErrorCode.STATUS_NOT_ALLOWED,
                f"应退货款 {goods_refund} 超过实际已收可退金额 "
                f"{(received - previous_refund):.2f}，请先核对回款",
                422,
            )
        refund = _money(goods_refund + freight_total)

    # 先冲减未收应收：从到期日最晚的一期往前冲，尽量保留近期该收的钱。
    adjustments: list[SalesReturnPlanAdjustment] = []
    remaining_offset = offset
    if remaining_offset > 0:
        paid = await payment_svc.confirmed_amounts(session, [plan.id for plan in plans])
        for plan in plans:
            if remaining_offset <= 0:
                break
            reducible = max(_money(plan.amount or 0) - paid.get(plan.id, ZERO), ZERO)
            take = min(remaining_offset, reducible)
            if take <= 0:
                continue
            before_amount = _money(plan.amount or 0)
            before_status = plan.status
            after_amount = _money(before_amount - take)
            plan.amount = after_amount
            if after_amount == 0 and paid.get(plan.id, ZERO) == 0:
                plan.status = "cancelled"
            else:
                await payment_svc.recalc_plan(session, plan)
            adjustments.append(
                SalesReturnPlanAdjustment(
                    return_id=row.id,
                    plan_id=plan.id,
                    amount_before=before_amount,
                    applied_amount=take,
                    amount_after=after_amount,
                    status_before=before_status,
                )
            )
            remaining_offset = _money(remaining_offset - take)
        if remaining_offset > 0:
            raise AppError(
                ErrorCode.STATUS_NOT_ALLOWED,
                "应收计划可冲金额不足，请先核对计划金额与已确认回款",
                422,
            )

    row.freight_refund_amount = _money(freight_refund_amount)
    row.total_amount = total
    row.offset_amount = _money(offset)
    row.refund_amount = _money(refund)
    row.status = "completed"
    row.settled_by = user.id
    row.settled_at = datetime.now(UTC)
    row.settlement_note = (settlement_note or "").strip() or None
    for adjustment in adjustments:
        session.add(adjustment)
    await session.flush()

    from app.modules.order import milestones as milestones_svc

    await milestones_svc.sync_payment_milestones(session, order.id, operator_id=user.id)
    await write_audit(
        session,
        operator_id=user.id,
        action="settle_return",
        business_type="sales_return",
        business_id=row.id,
        before={"status": "pending"},
        after={
            "status": row.status,
            "total_amount": format(row.total_amount, ".2f"),
            "offset_amount": format(row.offset_amount, ".2f"),
            "refund_amount": format(row.refund_amount, ".2f"),
            "freight_refund_amount": format(row.freight_refund_amount, ".2f"),
            "adjustments": [
                {
                    "plan_id": adjustment.plan_id,
                    "amount_before": format(adjustment.amount_before, ".2f"),
                    "applied_amount": format(adjustment.applied_amount, ".2f"),
                    "amount_after": format(adjustment.amount_after, ".2f"),
                }
                for adjustment in adjustments
            ],
            "note": row.settlement_note,
        },
        ip=ip,
    )
    await record_and_notify(
        session,
        customer_id=order.customer_id,
        owner_id=order.owner_id,
        operator_id=user.id,
        title="财务已处理销售退货",
        content=(
            f"订单 {order.order_no} 退货 {row.return_no} 已处理："
            f"冲应收 {format(row.offset_amount, '.2f')} 元，"
            f"应退 {format(row.refund_amount, '.2f')} 元"
            + ("，请财务实际退款后登记流水。" if row.refund_amount > 0 else "。")
        ),
        business_type="sales_return",
        business_id=row.id,
        order_id=order.id,
        event_key=f"order:return:settle:{row.id}",
    )
    await session.flush()
    return row


async def refund_return(
    session: AsyncSession,
    user,
    row: SalesReturn,
    reference: str,
    *,
    ip: str | None = None,
) -> SalesReturn:
    if row.status != "completed":
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"「{RETURN_STATUS_LABEL.get(row.status, row.status)}」的退货单不能登记退款",
            422,
        )
    if row.refund_amount <= 0:
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED, "这张退货单没有应退金额，不需要登记退款", 422
        )
    row.status = "refunded"
    row.refunded_by = user.id
    row.refunded_at = datetime.now(UTC)
    row.refund_reference = reference.strip()
    await write_audit(
        session,
        operator_id=user.id,
        action="refund_return",
        business_type="sales_return",
        business_id=row.id,
        before={"status": "completed", "refund_amount": format(row.refund_amount, ".2f")},
        after={"status": row.status, "refund_reference": row.refund_reference},
        ip=ip,
    )
    await session.flush()
    return row


async def void_return(
    session: AsyncSession,
    user,
    row: SalesReturn,
    reason: str,
    *,
    ip: str | None = None,
) -> SalesReturn:
    if row.status != "completed":
        if row.status == "refunded":
            raise AppError(
                ErrorCode.STATUS_NOT_ALLOWED,
                "这笔退货已经实际退款，不能直接作废；请走线下冲回并保留退款凭证",
                422,
            )
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"「{RETURN_STATUS_LABEL.get(row.status, row.status)}」的退货单不能作废",
            422,
        )
    order = await get_order_or_404(session, row.order_id)
    adjustments = (
        await session.execute(
            select(SalesReturnPlanAdjustment)
            .where(SalesReturnPlanAdjustment.return_id == row.id)
            .order_by(SalesReturnPlanAdjustment.plan_id.asc())
            .with_for_update()
        )
    ).scalars().all()
    for adjustment in adjustments:
        plan = (
            await session.execute(
                select(ReceivablePlan)
                .where(ReceivablePlan.id == adjustment.plan_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if plan is None:
            raise AppError(ErrorCode.NOT_FOUND, "退货关联的应收期已不存在，不能作废", 409)
        # 这一期可能被多张退货单先后冲过：不能只拿本单的 amount_after 对比，
        # 否则“后一张退货”会被误判成“手工改过”，前面的退货永远作废不了。
        # 用这一期的原始金额 - 所有仍生效退货的冲减额，得到“现在应该是多少”。
        plan_adjustments = (
            await session.execute(
                select(SalesReturnPlanAdjustment)
                .where(SalesReturnPlanAdjustment.plan_id == adjustment.plan_id)
                .order_by(SalesReturnPlanAdjustment.id.asc())
                .with_for_update()
            )
        ).scalars().all()
        if not plan_adjustments:
            raise AppError(ErrorCode.NOT_FOUND, "退货关联的应收调整不存在，不能作废", 409)
        original_amount = _money(plan_adjustments[0].amount_before)
        active_applied = _money(
            (
                await session.execute(
                    select(
                        func.coalesce(
                            func.sum(SalesReturnPlanAdjustment.applied_amount), 0
                        )
                    )
                    .join(
                        SalesReturn,
                        SalesReturn.id == SalesReturnPlanAdjustment.return_id,
                    )
                    .where(
                        SalesReturnPlanAdjustment.plan_id == adjustment.plan_id,
                        SalesReturn.status.in_(SETTLED_RETURN_STATUSES),
                    )
                )
            ).scalar_one()
        )
        expected_amount = _money(original_amount - active_applied)
        if _money(plan.amount or 0) != expected_amount:
            raise AppError(
                ErrorCode.STATUS_NOT_ALLOWED,
                f"第 {adjustment.plan_id} 期应收和退货冲减台账对不上"
                f"（现在 {plan.amount}，按台账应为 {expected_amount}），"
                "可能被手工改过或还有别的退货未处理；请先核对，再走线下冲回",
                409,
            )
        # 把本单的冲减加回去；如果后面还有别的退货，它们冲减的部分保留。
        restored_amount = _money((plan.amount or ZERO) + adjustment.applied_amount)
        plan.amount = restored_amount
        plan.status = adjustment.status_before
        if plan.status == "cancelled" and restored_amount > 0:
            plan.status = "pending"
        if plan.status != "cancelled":
            await payment_svc.recalc_plan(session, plan)
    row.status = "voided"
    row.voided_by = user.id
    row.voided_at = datetime.now(UTC)
    row.void_reason = reason.strip()
    await session.flush()
    from app.modules.order import milestones as milestones_svc

    await milestones_svc.sync_payment_milestones(session, order.id, operator_id=user.id)
    await write_audit(
        session,
        operator_id=user.id,
        action="void_return",
        business_type="sales_return",
        business_id=row.id,
        before={"status": "completed", "offset_amount": format(row.offset_amount, ".2f")},
        after={
            "status": row.status,
            "reason": row.void_reason,
            "restored_plans": [
                {
                    "plan_id": adjustment.plan_id,
                    "amount_before": format(adjustment.amount_before, ".2f"),
                    "amount_after": format(adjustment.amount_after, ".2f"),
                }
                for adjustment in adjustments
            ],
        },
        ip=ip,
    )
    await record_and_notify(
        session,
        customer_id=order.customer_id,
        owner_id=order.owner_id,
        operator_id=user.id,
        title="销售退货已作废",
        content=(
            f"订单 {order.order_no} 退货 {row.return_no} 已作废，"
            f"原因：{row.void_reason}；原冲减的应收已恢复。"
        ),
        business_type="sales_return",
        business_id=row.id,
        order_id=order.id,
        event_key=f"order:return:void:{row.id}",
    )
    await session.flush()
    return row


async def _user_names(session: AsyncSession, ids: list[int]) -> dict[int, str]:
    ids = [i for i in ids if i]
    if not ids:
        return {}
    rows = (await session.execute(select(User.id, User.name).where(User.id.in_(ids)))).all()
    return {int(uid): name for uid, name in rows}


async def serialize_return(session: AsyncSession, row: SalesReturn) -> dict:
    order = await get_order_or_404(session, row.order_id)
    customer = await session.get(Customer, order.customer_id)
    items = (
        await session.execute(
            select(SalesReturnItem)
            .where(SalesReturnItem.return_id == row.id)
            .order_by(SalesReturnItem.id.asc())
        )
    ).scalars().all()
    names = await _user_names(
        session, [row.created_by, row.settled_by, row.refunded_by, row.voided_by, row.cancelled_by]
    )
    return {
        "id": row.id,
        "return_no": row.return_no,
        "order_id": row.order_id,
        "order_no": order.order_no,
        "customer_id": order.customer_id,
        "customer_name": customer.name if customer else None,
        "status": row.status,
        "status_label": RETURN_STATUS_LABEL.get(row.status, row.status),
        "reason_code": row.reason_code,
        "reason_label": RETURN_REASON_LABEL.get(row.reason_code, row.reason_code),
        "reason": row.reason,
        "return_date": row.return_date.isoformat(),
        "currency": row.currency,
        "goods_amount": float(row.goods_amount or 0),
        "goods_amount_exact": format(row.goods_amount or 0, ".2f"),
        "freight_refund_amount": float(row.freight_refund_amount or 0),
        "freight_refund_amount_exact": format(row.freight_refund_amount or 0, ".2f"),
        "total_amount": float(row.total_amount or 0),
        "total_amount_exact": format(row.total_amount or 0, ".2f"),
        "offset_amount": float(row.offset_amount or 0),
        "offset_amount_exact": format(row.offset_amount or 0, ".2f"),
        "refund_amount": float(row.refund_amount or 0),
        "refund_amount_exact": format(row.refund_amount or 0, ".2f"),
        "refund_reference": row.refund_reference,
        "settlement_note": row.settlement_note,
        "remark": row.remark,
        "created_by": row.created_by,
        "created_by_name": names.get(row.created_by or 0),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "settled_by_name": names.get(row.settled_by or 0),
        "settled_at": row.settled_at.isoformat() if row.settled_at else None,
        "refunded_by_name": names.get(row.refunded_by or 0),
        "refunded_at": row.refunded_at.isoformat() if row.refunded_at else None,
        "cancel_reason": row.cancel_reason,
        "void_reason": row.void_reason,
        "items": [
            {
                "id": item.id,
                "shipment_batch_item_id": item.shipment_batch_item_id,
                "order_item_id": item.order_item_id,
                "batch_id": item.batch_id,
                "sku": item.sku_snapshot,
                "specification": item.specification_snapshot,
                "quantity": float(item.quantity or 0),
                "quantity_exact": format(item.quantity or 0, "f"),
                "unit_price": float(item.unit_price or 0),
                "unit_price_exact": format(item.unit_price or 0, ".4f"),
                "amount": float(item.amount or 0),
                "amount_exact": format(item.amount or 0, ".2f"),
            }
            for item in items
        ],
    }


async def list_returns_for_order(session: AsyncSession, order_id: int) -> list[dict]:
    rows = (
        await session.execute(
            select(SalesReturn)
            .where(SalesReturn.order_id == order_id)
            .order_by(SalesReturn.id.desc())
        )
    ).scalars().all()
    return [await serialize_return(session, row) for row in rows]


async def list_returns(
    session: AsyncSession, user, *, status: str | None = None, limit: int = 100
) -> list[dict]:
    stmt = select(SalesReturn, SalesOrder).join(
        SalesOrder, SalesOrder.id == SalesReturn.order_id
    )
    if status:
        stmt = stmt.where(SalesReturn.status == status)
    owners = await scoped_owner_ids(session, user)
    if owners is not None:
        stmt = stmt.where(SalesOrder.owner_id.in_(owners))
    rows = (
        await session.execute(
            stmt.order_by(SalesReturn.id.desc()).limit(min(max(limit, 1), 200))
        )
    ).all()
    return [await serialize_return(session, row[0]) for row in rows]
