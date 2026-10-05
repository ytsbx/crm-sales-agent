"""跟进记录接口（对齐 03-API §24）。"""


from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.data_scope import scoped_owner_ids
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok, page_data, paginate
from app.modules.customer import service as customer_service
from app.modules.customer.model import Customer
from app.modules.followup.model import FollowUp
from app.modules.followup.mutations import serialize, notify_manual_followup, create_manual_followup
from app.modules.notification import service as notification_service
from app.modules.followup.visibility import get_visible_followup, system_source_filter
from app.modules.followup.schema import (
    FollowUpCreate,
    FollowUpNextTask,
    FollowUpUpdate,
    validate_plan,
)
from app.modules.lead.model import Lead
from app.modules.opportunity.model import Opportunity
from app.modules.order.model import SalesOrder
from app.modules.quote.model import Quote
from app.modules.task.model import Task
from app.modules.user.model import User

router = APIRouter(tags=["FollowUp"])


async def _visible_followup(
    session: AsyncSession, user: CurrentUser, followup_id: int
) -> FollowUp:
    """取跟进记录并校验与附件共用的数据范围。"""
    return await get_visible_followup(session, user, followup_id)


@router.get("/followups")
async def list_followups(
    customer_id: int | None = None,
    opportunity_id: int | None = None,
    lead_id: int | None = None,
    owner_id: int | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user: CurrentUser = Depends(require_permission("followup:view")),
    session: AsyncSession = Depends(get_db),
):
    stmt = select(FollowUp).where(await system_source_filter(session, user))
    if customer_id:
        stmt = stmt.where(FollowUp.customer_id == customer_id)
    if opportunity_id:
        stmt = stmt.where(FollowUp.opportunity_id == opportunity_id)
    if lead_id:
        stmt = stmt.where(FollowUp.lead_id == lead_id)
    if owner_id:
        stmt = stmt.where(FollowUp.owner_id == owner_id)
    stmt = stmt.order_by(FollowUp.id.desc())

    # 数据范围：列表页此前**只看查询参数、从不撒网** —— 任何有 followup:view
    # 的业务员加个 page_size=200 就能读全公司跟进内容（详情/删除/商机跟进列表
    # 都补了校验，唯独最容易的这条主列表漏了）。这里与 _visible_followup 同一口径：
    # 客户/线索允许无主（公海），商机/报价/订单必须有主且在范围内。
    visible_owner_ids = await scoped_owner_ids(session, user)
    if visible_owner_ids is not None:
        customer_ids = select(Customer.id).where(
            or_(
                Customer.owner_id.in_(visible_owner_ids),
                Customer.owner_id.is_(None),
            )
        )
        lead_ids = select(Lead.id).where(
            or_(Lead.owner_id.in_(visible_owner_ids), Lead.owner_id.is_(None))
        )
        opportunity_ids = select(Opportunity.id).where(
            Opportunity.owner_id.in_(visible_owner_ids)
        )
        quote_ids = select(Quote.id).where(Quote.owner_id.in_(visible_owner_ids))
        order_ids = select(SalesOrder.id).where(SalesOrder.owner_id.in_(visible_owner_ids))
        stmt = stmt.where(
            or_(
                FollowUp.customer_id.in_(customer_ids),
                FollowUp.lead_id.in_(lead_ids),
                FollowUp.opportunity_id.in_(opportunity_ids),
                FollowUp.quote_id.in_(quote_ids),
                FollowUp.order_id.in_(order_ids),
            )
        )

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
    followup, replayed = await create_manual_followup(session, user, payload, ip=client_ip(request))
    await session.commit()
    await notification_service.dispatch_pending(session)
    return ok({"followup": serialize(followup, user.name), "task_id": followup.next_task_id,
               "replayed": replayed}, "跟进已记录")


@router.patch("/followups/{followup_id}")
async def update_followup(
    followup_id: int,
    payload: FollowUpUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("followup:create")),
    session: AsyncSession = Depends(get_db),
):
    followup = await _visible_followup(session, user, followup_id)
    if followup.followup_type == "系统":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "系统过程记录不可编辑，请在原单执行业务操作", 422)
    await session.refresh(followup, with_for_update=True)
    before = serialize(followup)
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("followup_type") == "系统":
        raise AppError(ErrorCode.PARAM_ERROR, "人工跟进不能改为系统过程记录", 422)
    plan_changed = bool(set(changes) & {"next_action", "task_due_at", "exemption_reason"})
    if plan_changed:
        next_action = changes.get("next_action", followup.next_action)
        due_at = changes.get("task_due_at", followup.planned_at)
        reason = changes.get("exemption_reason", followup.exemption_reason)
        try:
            validate_plan(next_action, due_at, reason)
        except ValueError as exc:
            raise AppError(ErrorCode.PARAM_ERROR, str(exc), 422) from exc
        task = (await session.execute(select(Task).where(Task.id == followup.next_task_id)
                    .with_for_update().execution_options(populate_existing=True))).scalar_one_or_none() if followup.next_task_id else None
        real_plan_change = (next_action, due_at, reason) != (followup.next_action, followup.planned_at, followup.exemption_reason)
        if real_plan_change:
            if task and task.status in ("pending", "doing"):
                # 任务可能已经转交：不能通过跟进编辑绕过任务的数据范围。
                from app.modules.task.router import _visible_task
                await _visible_task(session, user, task.id)
                if reason:
                    task.status = "cancelled"
                else:
                    task.title, task.due_at = next_action, due_at
            elif not reason:
                task = Task(title=next_action, task_type="followup", due_at=due_at,
                            customer_id=followup.customer_id, contact_id=followup.contact_id,
                            lead_id=followup.lead_id, opportunity_id=followup.opportunity_id,
                            quote_id=followup.quote_id, order_id=followup.order_id,
                            owner_id=user.id, source="manual", status="pending")
                session.add(task)
                await session.flush()
            followup.next_task_id = task.id if task and not reason else None
        followup.planned_at = due_at
    for field, value in changes.items():
        if field != "task_due_at":
            setattr(followup, field, value)
    await session.flush()
    await customer_service.refresh_next_followup_at(session, followup.customer_id)
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
    if before != serialize(followup):
        await notify_manual_followup(session, followup, user, action="update")
    await session.commit()
    await notification_service.dispatch_pending(session)
    return ok(serialize(followup), "已保存")


# ------------------------------------------------- 03-API §24 新增的两个接口


@router.get("/followups/{followup_id}")
async def get_followup(
    followup_id: int,
    user: CurrentUser = Depends(require_permission("followup:view")),
    session: AsyncSession = Depends(get_db),
):
    """单条跟进详情。"""
    followup = await _visible_followup(session, user, followup_id)
    owners = {
        int(uid): name
        for uid, name in (
            await session.execute(
                select(User.id, User.name).where(User.id == followup.owner_id)
            )
        ).all()
    } if followup.owner_id else {}
    return ok(
        serialize(
            followup, owners.get(followup.owner_id) if followup.owner_id else None
        )
    )


@router.post("/followups/{followup_id}/create-next-task")
async def create_next_task(
    followup_id: int,
    payload: FollowUpNextTask,
    request: Request,
    user: CurrentUser = Depends(require_permission("task:manage")),
    session: AsyncSession = Depends(get_db),
):
    """在已有跟进上补建后续任务。

    新任务继承这条跟进关联的全部业务对象（客户/联系人/线索/商机），
    不用再手填一遍 —— 这也是这个接口存在的意义。
    负责人默认记跟进的本人；显式指定时校验存在且在职。
    """
    followup = await _visible_followup(session, user, followup_id)

    await session.refresh(followup, with_for_update=True)
    if followup.next_task_id:
        task = await session.get(Task, followup.next_task_id)
        if task:
            if (task.due_at == payload.due_at and task.title == (payload.title or followup.next_action or f"跟进后续：{followup.content[:30]}")
                    and task.owner_id == (payload.owner_id or followup.owner_id)
                    and task.task_type == payload.task_type and task.priority == payload.priority):
                return ok({"task_id": task.id, "title": task.title, "owner_id": task.owner_id,
                           "due_at": task.due_at, "followup_id": followup.id}, "后续任务已创建")
            raise AppError(ErrorCode.VERSION_CONFLICT, "已有后续任务，请在待办中修改，或修改跟进计划", 409)
    owner_id = payload.owner_id if payload.owner_id is not None else followup.owner_id
    if payload.owner_id is not None:
        target = await session.get(User, payload.owner_id)
        if target is None:
            raise AppError(ErrorCode.NOT_FOUND, f"负责人 id={payload.owner_id} 不存在", 404)
        if target.status != "active":
            raise AppError(
                ErrorCode.PARAM_ERROR, f"负责人「{target.name}」已停用", 422
            )

    task = Task(
        title=payload.title or followup.next_action or f"跟进后续：{followup.content[:30]}",
        task_type=payload.task_type,
        priority=payload.priority,
        customer_id=followup.customer_id,
        contact_id=followup.contact_id,
        lead_id=followup.lead_id,
        opportunity_id=followup.opportunity_id,
        owner_id=owner_id,
        status="pending",
        due_at=payload.due_at,
        source="manual",
    )
    session.add(task)
    await session.flush()
    followup.next_task_id = task.id
    if followup.followup_type != "系统":
        followup.next_action = task.title
        followup.planned_at = task.due_at
        followup.exemption_reason = None
    # 补建后续任务 = 补上一个约定：第三个时钟要跟着变（§2.3）
    await customer_service.refresh_next_followup_at(session, followup.customer_id)
    await write_audit(
        session,
        operator_id=user.id,
        action="create_next_task",
        business_type="followup",
        business_id=followup.id,
        after={"task_id": task.id, "title": task.title, "due_at": str(task.due_at)},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(
        {
            "task_id": task.id,
            "title": task.title,
            "owner_id": task.owner_id,
            "due_at": task.due_at,
            "followup_id": followup.id,
        },
        "后续任务已创建",
    )


@router.delete("/followups/{followup_id}")
async def delete_followup(
    followup_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("followup:create")),
    session: AsyncSession = Depends(get_db),
):
    # update 与详情都走 _visible_followup，删除原先却只 session.get——
    # 任何有跟进权限的人拿别人的 id 就能删（"列表看不到的，按 id 也拿不到"是本项目铁律）。
    followup = await _visible_followup(session, user, followup_id)
    if followup.followup_type == "系统":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "系统过程记录不可删除，请在原单执行业务操作", 422)
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
    user: CurrentUser = Depends(require_permission("followup:view")),
    session: AsyncSession = Depends(get_db),
):
    from app.modules.opportunity import service as opportunity_service

    # 原先 user 参数写成 `_`（被丢掉），查询只按 opportunity_id 过滤——
    # 换个商机 id 就能读到别人商机下的全部跟进内容。先按数据范围确认商机可见，
    # 与"删跟进不校验"是同一个形状，两处一起堵。
    await opportunity_service.get_visible_opportunity(session, user, opportunity_id)
    rows = (
        await session.execute(
            select(FollowUp)
            .where(FollowUp.opportunity_id == opportunity_id)
            .order_by(FollowUp.id.desc())
        )
    ).scalars().all()
    return ok([serialize(row) for row in rows])


__all__ = ["Opportunity", "router"]
