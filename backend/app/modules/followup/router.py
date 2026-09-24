"""跟进记录接口（对齐 03-API §24）。"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok, page_data, paginate
from app.modules.customer.model import Customer
from app.modules.followup.model import FollowUp
from app.modules.followup.schema import FollowUpCreate, FollowUpUpdate
from app.modules.lead.model import Lead
from app.modules.opportunity.model import Opportunity
from app.modules.task.model import Task
from app.modules.user.model import User

router = APIRouter(tags=["FollowUp"])


def serialize(followup: FollowUp, owner_name: str | None = None) -> dict:
    return {
        "id": followup.id,
        "customer_id": followup.customer_id,
        "contact_id": followup.contact_id,
        "lead_id": followup.lead_id,
        "opportunity_id": followup.opportunity_id,
        "quote_id": followup.quote_id,
        "order_id": followup.order_id,
        "owner_id": followup.owner_id,
        "owner_name": owner_name,
        "followup_type": followup.followup_type,
        "content": followup.content,
        "customer_feedback": followup.customer_feedback,
        "next_action": followup.next_action,
        "created_at": followup.created_at,
    }


@router.get("/followups")
async def list_followups(
    customer_id: int | None = None,
    opportunity_id: int | None = None,
    lead_id: int | None = None,
    owner_id: int | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    _: CurrentUser = Depends(require_permission("followup:view")),
    session: AsyncSession = Depends(get_db),
):
    stmt = select(FollowUp)
    if customer_id:
        stmt = stmt.where(FollowUp.customer_id == customer_id)
    if opportunity_id:
        stmt = stmt.where(FollowUp.opportunity_id == opportunity_id)
    if lead_id:
        stmt = stmt.where(FollowUp.lead_id == lead_id)
    if owner_id:
        stmt = stmt.where(FollowUp.owner_id == owner_id)
    stmt = stmt.order_by(FollowUp.id.desc())

    rows, total = await paginate(session, stmt, page, page_size)
    owner_ids = {row.owner_id for row in rows if row.owner_id}
    names: dict[int, str] = {}
    if owner_ids:
        name_rows = (
            await session.execute(select(User.id, User.name).where(User.id.in_(owner_ids)))
        ).all()
        names = {int(uid): name for uid, name in name_rows}
    items = [serialize(row, names.get(row.owner_id) if row.owner_id else None) for row in rows]
    return ok(page_data(items, total, page, page_size))


@router.post("/followups")
async def create_followup(
    payload: FollowUpCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("followup:create")),
    session: AsyncSession = Depends(get_db),
):
    if not any(
        [payload.customer_id, payload.opportunity_id, payload.lead_id, payload.quote_id, payload.order_id]
    ):
        raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "跟进记录必须关联一个业务对象")

    data = payload.model_dump(
        exclude={"create_task", "task_title", "task_due_at"}
    )
    followup = FollowUp(**data, owner_id=user.id)
    session.add(followup)
    await session.flush()

    now = datetime.now(UTC)
    # 同步「最近跟进时间」：客户、线索都要更新，供后续自动任务规则使用
    if payload.customer_id:
        customer = await session.get(Customer, payload.customer_id)
        if customer:
            customer.last_followup_at = now
    if payload.lead_id:
        lead = await session.get(Lead, payload.lead_id)
        if lead:
            lead.last_followup_at = now
            if lead.status in ("pending", "assigned"):
                lead.status = "following"

    created_task_id = None
    if payload.create_task and payload.task_due_at:
        task = Task(
            title=payload.task_title or f"跟进：{payload.content[:30]}",
            task_type="followup",
            customer_id=payload.customer_id,
            contact_id=payload.contact_id,
            lead_id=payload.lead_id,
            opportunity_id=payload.opportunity_id,
            owner_id=user.id,
            status="pending",
            due_at=payload.task_due_at,
            source="manual",
        )
        session.add(task)
        await session.flush()
        created_task_id = task.id

    await write_audit(
        session,
        operator_id=user.id,
        action="create",
        business_type="followup",
        business_id=followup.id,
        after=serialize(followup),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(
        {"followup": serialize(followup, user.name), "task_id": created_task_id},
        "跟进已记录",
    )


@router.patch("/followups/{followup_id}")
async def update_followup(
    followup_id: int,
    payload: FollowUpUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("followup:create")),
    session: AsyncSession = Depends(get_db),
):
    followup = await session.get(FollowUp, followup_id)
    if followup is None:
        raise AppError(ErrorCode.NOT_FOUND, "跟进记录不存在", 404)
    before = serialize(followup)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(followup, field, value)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="update",
        business_type="followup",
        business_id=followup.id,
        before=before,
        after=serialize(followup),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(serialize(followup), "已保存")


@router.delete("/followups/{followup_id}")
async def delete_followup(
    followup_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("followup:create")),
    session: AsyncSession = Depends(get_db),
):
    followup = await session.get(FollowUp, followup_id)
    if followup is None:
        raise AppError(ErrorCode.NOT_FOUND, "跟进记录不存在", 404)
    await session.delete(followup)
    await write_audit(
        session,
        operator_id=user.id,
        action="delete",
        business_type="followup",
        business_id=followup_id,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(None, "已删除")


@router.get("/opportunities/{opportunity_id}/followups")
async def list_opportunity_followups(
    opportunity_id: int,
    _: CurrentUser = Depends(require_permission("followup:view")),
    session: AsyncSession = Depends(get_db),
):
    rows = (
        await session.execute(
            select(FollowUp)
            .where(FollowUp.opportunity_id == opportunity_id)
            .order_by(FollowUp.id.desc())
        )
    ).scalars().all()
    return ok([serialize(row) for row in rows])


__all__ = ["Opportunity", "router"]
