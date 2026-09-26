"""应收与回款接口（对齐 03-API §29 / §30）。"""

from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok, page_data, paginate
from app.modules.order.model import SalesOrder
from app.modules.notification import service as notification_service
from app.modules.order.service import get_order_or_404
from app.modules.payment import service as svc
from app.modules.payment.model import PaymentRecord, ReceivablePlan
from app.modules.payment.schema import (
    PaymentAction,
    PaymentCreate,
    ReceivableCreate,
    ReceivableGenerate,
)

router = APIRouter(tags=["Payment"])


@router.get("/receivables")
async def list_receivables(
    status: str | None = None,
    order_id: int | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    _: CurrentUser = Depends(require_permission("payment:view")),
    session: AsyncSession = Depends(get_db),
):
    stmt = select(ReceivablePlan)
    if status:
        stmt = stmt.where(ReceivablePlan.status == status)
    if order_id:
        stmt = stmt.where(ReceivablePlan.order_id == order_id)
    rows, total = await paginate(session, stmt.order_by(ReceivablePlan.due_date.asc()), page, page_size)
    items = [await svc.serialize_plan(session, plan) for plan in rows]
    return ok(page_data(items, total, page, page_size))


@router.post("/orders/{order_id}/receivables")
async def create_receivable(
    order_id: int,
    payload: ReceivableCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("payment:manage")),
    session: AsyncSession = Depends(get_db),
):
    await get_order_or_404(session, order_id)
    plan = ReceivablePlan(
        order_id=order_id,
        plan_name=payload.plan_name,
        due_date=payload.due_date,
        amount=Decimal(str(payload.amount)),
        status="pending",
        remark=payload.remark,
        created_at=datetime.now(UTC),
    )
    session.add(plan)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="create",
        business_type="receivable_plan",
        business_id=plan.id,
        after=await svc.serialize_plan(session, plan),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(await svc.serialize_plan(session, plan), "应收节点已创建")


@router.post("/orders/{order_id}/receivables/generate")
async def generate_receivables(
    order_id: int,
    payload: ReceivableGenerate,
    request: Request,
    user: CurrentUser = Depends(require_permission("payment:manage")),
    session: AsyncSession = Depends(get_db),
):
    """按比例生成应收计划，例如 30% 定金 + 70% 尾款。"""
    order = await get_order_or_404(session, order_id)
    if not payload.ratios or abs(sum(payload.ratios) - 1) > 0.0001:
        raise AppError(ErrorCode.PARAM_ERROR, "比例之和必须等于 1，例如 [0.3, 0.7]")

    existing = (
        await session.execute(
            select(ReceivablePlan.id).where(ReceivablePlan.order_id == order_id)
        )
    ).first()
    if existing:
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "该订单已有应收计划，请先删除再重新生成")

    due_dates = [payload.first_due_date, payload.second_due_date or payload.first_due_date]
    names = [payload.first_name, payload.second_name]
    created = []
    for index, ratio in enumerate(payload.ratios):
        plan = ReceivablePlan(
            order_id=order_id,
            plan_name=names[index] if index < len(names) else f"第 {index + 1} 期",
            due_date=due_dates[index] if index < len(due_dates) else payload.first_due_date,
            amount=(order.total_amount * Decimal(str(ratio))).quantize(Decimal("0.01")),
            status="pending",
            created_at=datetime.now(UTC),
        )
        session.add(plan)
        await session.flush()
        created.append(plan)
    await write_audit(
        session,
        operator_id=user.id,
        action="generate",
        business_type="receivable_plan",
        business_id=order_id,
        after={"order_id": order_id, "created": len(created), "ratios": payload.ratios},
        ip=client_ip(request),
    )
    await session.commit()
    return ok([await svc.serialize_plan(session, plan) for plan in created], "应收计划已生成")


@router.patch("/receivables/{plan_id}")
async def update_receivable(
    plan_id: int,
    payload: ReceivableCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("payment:manage")),
    session: AsyncSession = Depends(get_db),
):
    plan = await svc.get_plan_or_404(session, plan_id)
    before = await svc.serialize_plan(session, plan)
    plan.plan_name = payload.plan_name
    plan.due_date = payload.due_date
    plan.amount = Decimal(str(payload.amount))
    plan.remark = payload.remark
    await svc.recalc_plan(session, plan)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="update",
        business_type="receivable_plan",
        business_id=plan.id,
        before=before,
        after=await svc.serialize_plan(session, plan),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(await svc.serialize_plan(session, plan), "已保存")


@router.delete("/receivables/{plan_id}")
async def delete_receivable(
    plan_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("payment:manage")),
    session: AsyncSession = Depends(get_db),
):
    plan = await svc.get_plan_or_404(session, plan_id)
    paid = (
        await session.execute(
            select(PaymentRecord.id).where(PaymentRecord.receivable_plan_id == plan_id)
        )
    ).first()
    if paid:
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "该应收节点已有回款记录，不能删除")
    before = await svc.serialize_plan(session, plan)
    await session.delete(plan)
    await write_audit(
        session,
        operator_id=user.id,
        action="delete",
        business_type="receivable_plan",
        business_id=plan_id,
        before=before,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(None, "已删除")


@router.post("/receivables/{plan_id}/mark-overdue")
async def mark_overdue(
    plan_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("payment:manage")),
    session: AsyncSession = Depends(get_db),
):
    plan = await svc.get_plan_or_404(session, plan_id)
    before_status = plan.status
    plan.status = "overdue"
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="mark_overdue",
        business_type="receivable_plan",
        business_id=plan.id,
        before={"status": before_status},
        after={"status": "overdue"},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(await svc.serialize_plan(session, plan), "已标记为逾期")


@router.get("/payments")
async def list_payments(
    status: str | None = None,
    order_id: int | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    _: CurrentUser = Depends(require_permission("payment:view")),
    session: AsyncSession = Depends(get_db),
):
    stmt = select(PaymentRecord)
    if status:
        stmt = stmt.where(PaymentRecord.status == status)
    if order_id:
        stmt = stmt.where(PaymentRecord.order_id == order_id)
    rows, total = await paginate(session, stmt.order_by(PaymentRecord.id.desc()), page, page_size)
    items = [await svc.serialize_payment(session, row) for row in rows]
    return ok(page_data(items, total, page, page_size))


@router.post("/payments")
async def create_payment(
    payload: PaymentCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("payment:manage", "order:manage")),
    session: AsyncSession = Depends(get_db),
):
    plan = None
    order_id = None
    if payload.receivable_plan_id:
        plan = await svc.get_plan_or_404(session, payload.receivable_plan_id)
        order_id = plan.order_id
    else:
        raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "请指定这笔回款对应的应收节点")

    record = PaymentRecord(
        receivable_plan_id=plan.id,
        order_id=order_id,
        received_date=payload.received_date,
        received_amount=Decimal(str(payload.received_amount)),
        payment_method=payload.payment_method,
        voucher_note=payload.voucher_note,
        status="pending",
        created_by=user.id,
        created_at=datetime.now(UTC),
    )
    session.add(record)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="create",
        business_type="payment",
        business_id=record.id,
        after=await svc.serialize_payment(session, record),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(await svc.serialize_payment(session, record), "回款已登记，等待财务确认")


@router.post("/payments/{payment_id}/confirm")
async def confirm_payment(
    payment_id: int,
    payload: PaymentAction,
    request: Request,
    user: CurrentUser = Depends(require_permission("payment:manage")),
    session: AsyncSession = Depends(get_db),
):
    record = await svc.get_payment_or_404(session, payment_id)
    if record.status == "confirmed":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "该回款已确认")
    record.status = "confirmed"
    record.confirmed_by = user.id
    record.confirmed_at = datetime.now(UTC)
    if record.receivable_plan_id:
        plan = await svc.get_plan_or_404(session, record.receivable_plan_id)
        await svc.recalc_plan(session, plan)
    order = await session.get(SalesOrder, record.order_id)
    if order and order.owner_id:
        await notification_service.notify(
            session,
            user_id=order.owner_id,
            type_="payment",
            title="回款已确认",
            content=f"订单 {order.order_no} 收到 ¥{float(record.received_amount):,.2f}",
            business_type="order",
            business_id=order.id,
        )
    await write_audit(
        session,
        operator_id=user.id,
        action="confirm",
        business_type="payment",
        business_id=record.id,
        after={"comment": payload.comment},
        ip=client_ip(request),
    )
    # 先序列化取值，再 commit：commit 之后会话里的 ORM 对象会过期，
    # 那时再取属性可能触发额外的同步 IO（异步会话里会报 MissingGreenlet）。
    serialized = await svc.serialize_payment(session, record)
    await session.commit()
    await notification_service.dispatch_pending(session)
    return ok(serialized, "财务已确认回款")


@router.post("/payments/{payment_id}/reject")
async def reject_payment(
    payment_id: int,
    payload: PaymentAction,
    request: Request,
    user: CurrentUser = Depends(require_permission("payment:manage")),
    session: AsyncSession = Depends(get_db),
):
    record = await svc.get_payment_or_404(session, payment_id)
    if record.status == "confirmed":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "已确认的回款不能驳回")
    before_status = record.status
    record.status = "rejected"
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="reject",
        business_type="payment",
        business_id=record.id,
        before={"status": before_status},
        after={"status": "rejected", "comment": payload.comment},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(await svc.serialize_payment(session, record), "已驳回该回款")


@router.get("/receivables/{plan_id}/payments")
async def plan_payments(
    plan_id: int,
    _: CurrentUser = Depends(require_permission("payment:view")),
    session: AsyncSession = Depends(get_db),
):
    rows = (
        await session.execute(
            select(PaymentRecord)
            .where(PaymentRecord.receivable_plan_id == plan_id)
            .order_by(PaymentRecord.received_date.asc())
        )
    ).scalars().all()
    return ok([await svc.serialize_payment(session, row) for row in rows])


@router.get("/orders/{order_id}/finance-summary")
async def finance_summary(
    order_id: int,
    _: CurrentUser = Depends(require_permission("payment:view")),
    session: AsyncSession = Depends(get_db),
):
    await get_order_or_404(session, order_id)
    return ok(await svc.order_finance_summary(session, order_id))
