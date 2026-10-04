"""应收与回款逻辑。"""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.modules.file.model import FileRecord
from app.modules.order.model import SalesOrder
from app.modules.payment.model import PAYMENT_STATUS_LABEL, PLAN_STATUS_LABEL, PaymentRecord, ReceivablePlan
from app.modules.user.model import User

ZERO = Decimal(0)


def ensure_payment_pending(record: PaymentRecord) -> None:
    """Only pending receipts may be edited, confirmed, or rejected."""
    if record.status != "pending":
        label = PAYMENT_STATUS_LABEL.get(record.status, record.status)
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"只有待财务确认的回款可以操作（当前：{label}）",
        )


def _f(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


async def confirmed_amount(session: AsyncSession, plan_id: int) -> Decimal:
    total = (
        await session.execute(
            select(func.coalesce(func.sum(PaymentRecord.received_amount), 0)).where(
                PaymentRecord.receivable_plan_id == plan_id,
                PaymentRecord.status == "confirmed",
            )
        )
    ).scalar_one()
    return Decimal(total)


async def recalc_plan(session: AsyncSession, plan: ReceivablePlan) -> None:
    """按已确认的回款重算应收节点状态。

    规则：全额收齐 → 已回款；收了一部分 → 部分回款；一分没收到且过期 → 已逾期。
    """
    received = await confirmed_amount(session, plan.id)
    if received >= plan.amount and plan.amount > 0:
        plan.status = "paid"
    elif received > 0:
        plan.status = "partial"
    else:
        today = datetime.now(UTC).date()
        plan.status = "overdue" if plan.due_date < today else "pending"


async def serialize_plan(session: AsyncSession, plan: ReceivablePlan) -> dict:
    received = await confirmed_amount(session, plan.id)
    order = await session.get(SalesOrder, plan.order_id)
    return {
        "id": plan.id,
        "order_id": plan.order_id,
        "order_no": order.order_no if order else None,
        "customer_id": order.customer_id if order else None,
        "plan_name": plan.plan_name,
        "due_date": plan.due_date,
        "amount": _f(plan.amount),
        "received_amount": _f(received),
        "remaining_amount": _f((plan.amount or ZERO) - received),
        "currency": plan.currency,
        "status": plan.status,
        "status_label": PLAN_STATUS_LABEL.get(plan.status, plan.status),
        "remark": plan.remark,
    }


async def serialize_payment(session: AsyncSession, record: PaymentRecord) -> dict:
    plan = (
        await session.get(ReceivablePlan, record.receivable_plan_id)
        if record.receivable_plan_id
        else None
    )
    order = await session.get(SalesOrder, record.order_id)
    voucher = (
        await session.get(FileRecord, record.voucher_file_id) if record.voucher_file_id else None
    )
    confirmer = await session.get(User, record.confirmed_by) if record.confirmed_by else None
    return {
        "id": record.id,
        "order_id": record.order_id,
        "order_no": order.order_no if order else None,
        "receivable_plan_id": record.receivable_plan_id,
        "plan_name": plan.plan_name if plan else None,
        "received_date": record.received_date,
        "received_amount": _f(record.received_amount),
        "currency": record.currency,
        "payment_method": record.payment_method,
        "voucher_note": record.voucher_note,
        "voucher_file_id": record.voucher_file_id,
        "voucher_file_name": voucher.file_name if voucher else None,
        "status": record.status,
        "status_label": PAYMENT_STATUS_LABEL.get(record.status, record.status),
        "confirmed_by": record.confirmed_by,
        "confirmed_by_name": confirmer.name if confirmer else None,
        "confirmed_at": record.confirmed_at,
        "created_at": record.created_at,
    }


async def get_plan_or_404(session: AsyncSession, plan_id: int) -> ReceivablePlan:
    plan = await session.get(ReceivablePlan, plan_id)
    if plan is None:
        raise AppError(ErrorCode.NOT_FOUND, "应收节点不存在", 404)
    return plan


async def get_payment_or_404(session: AsyncSession, payment_id: int) -> PaymentRecord:
    record = await session.get(PaymentRecord, payment_id)
    if record is None:
        raise AppError(ErrorCode.NOT_FOUND, "回款记录不存在", 404)
    return record


# ---------------------------------------------------------------------------
# 数据范围
#
# 应收/回款没有自己的 owner_id —— 归属跟着订单走，所以可见性统一
# 「取订单负责人 -> ensure_in_scope」。列表用子查询把范围下推到 SQL，
# 这样分页和计数都正确（先查出来再过滤会让 total 偏大）。
# ---------------------------------------------------------------------------
async def visible_order_ids_stmt(session: AsyncSession, user):
    """当前用户可见的订单 id 子查询；`all` 权限返回 None 表示不过滤。"""
    from app.core.data_scope import scoped_owner_ids

    owner_ids = await scoped_owner_ids(session, user)
    if owner_ids is None:
        return None
    stmt = select(SalesOrder.id)
    if owner_ids:
        stmt = stmt.where(SalesOrder.owner_id.in_(owner_ids))
    else:
        # 范围内一个负责人都没有 —— 用一个恒假条件，别退化成"看全部"
        stmt = stmt.where(SalesOrder.id < 0)
    return stmt


async def assert_order_visible(session: AsyncSession, user, order_id: int) -> None:
    """校验订单在数据范围内（应收/回款的可见性就等于订单的可见性）。"""
    from app.core.data_scope import ensure_in_scope

    order = await session.get(SalesOrder, order_id)
    await ensure_in_scope(session, user, owner_id=order.owner_id if order else None, label="订单")


async def get_visible_plan(
    session: AsyncSession, user, plan_id: int
) -> ReceivablePlan:
    plan = await get_plan_or_404(session, plan_id)
    await assert_order_visible(session, user, plan.order_id)
    return plan


async def get_visible_payment(
    session: AsyncSession, user, payment_id: int, *, for_update: bool = False
) -> PaymentRecord:
    if for_update:
        record = (
            await session.execute(
                select(PaymentRecord)
                .where(PaymentRecord.id == payment_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if record is None:
            raise AppError(ErrorCode.NOT_FOUND, "回款记录不存在", 404)
    else:
        record = await get_payment_or_404(session, payment_id)
    await assert_order_visible(session, user, record.order_id)
    return record



async def order_finance_summary(session: AsyncSession, order_id: int) -> dict:
    """订单的应收/回款概览，给订单详情页顶部用。"""
    plans = (
        await session.execute(
            select(ReceivablePlan).where(ReceivablePlan.order_id == order_id)
        )
    ).scalars().all()
    order = await session.get(SalesOrder, order_id)
    received = (
        await session.execute(
            select(func.coalesce(func.sum(PaymentRecord.received_amount), 0)).where(
                PaymentRecord.order_id == order_id, PaymentRecord.status == "confirmed"
            )
        )
    ).scalar_one()
    pending_confirm = (
        await session.execute(
            select(func.coalesce(func.sum(PaymentRecord.received_amount), 0)).where(
                PaymentRecord.order_id == order_id, PaymentRecord.status == "pending"
            )
        )
    ).scalar_one()
    total = order.total_amount if order else ZERO
    return {
        "order_amount": _f(total),
        "planned_amount": _f(sum((p.amount for p in plans), ZERO)),
        "received_amount": _f(Decimal(received)),
        "pending_confirm_amount": _f(Decimal(pending_confirm)),
        "unreceived_amount": _f((total or ZERO) - Decimal(received)),
        "plan_count": len(plans),
        "overdue_count": len([p for p in plans if p.status == "overdue"]),
    }
