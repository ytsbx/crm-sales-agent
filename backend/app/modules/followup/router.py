"""跟进记录接口（对齐 03-API §24）。"""


from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok, page_data, paginate
from app.modules.customer import service as customer_service
from app.modules.followup.model import FollowUp
from app.modules.followup.mutations import serialize, notify_manual_followup, create_manual_followup
from app.modules.notification import service as notification_service
from app.modules.followup.visibility import get_visible_followup, followup_visibility_filter
from app.modules.followup.schema import (
    FollowUpCreate,
    FollowUpNextTask,
    FollowUpUpdate,
    validate_plan,
)
from app.modules.opportunity.model import Opportunity
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
    stmt = select(FollowUp).where(await followup_visibility_filter(session, user))
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
                           "due_at": task.due_at, "followup_id": followup.id,
                           "skipped_references": []}, "后续任务已创建")
            raise AppError(ErrorCode.VERSION_CONFLICT, "已有后续任务，请在待办中修改，或修改跟进计划", 409)
    owner_id = payload.owner_id if payload.owner_id is not None else followup.owner_id
    # 负责人校验**不分来源**（第十一批 11.7）：显式选的、从跟进继承来的走同一道闸门。
    # 原实现只在"显式传了 owner_id"时才查 —— 而历史跟进的负责人早已停用时，
    # 补建出来的任务会被分给一个停用账号：挂在没人管的账号下，谁都看不到、
    # 也没人会处理。提示里要分清是"你选的那个人"还是"这条跟进原来的人"。
    if owner_id is not None:
        target = await session.get(User, owner_id)
        if target is None:
            raise AppError(ErrorCode.NOT_FOUND, f"负责人 id={owner_id} 不存在", 404)
        if target.status != "active":
            who = (
                f"这条跟进的负责人「{target.name}」"
                if payload.owner_id is None
                else f"负责人「{target.name}」"
            )
            raise AppError(
                ErrorCode.PARAM_ERROR, f"{who}已停用，请选一位在职负责人再补建任务", 422
            )

    # 业务关联继承（第十一批 11.6）：任务表有 `quote_id` / `order_id` 两列，直接带上；
    # **打样没有对应的列**，改用"来源业务对象"记（任务详情可据此跳转）。
    #
    # 每个关联都**当场复核**：对象还在、没被软删、且属于**同一个客户**。
    # 不为了"复制字段"把一条越权或跨客户的脏关联带进新任务；对不上的就不继承，
    # 并在返回里说明（原历史跟进一个字不动）。
    # 业务关联继承走**普通建任务那一份校验**（`task/refs.normalize_task_refs`，
    # 这里用 strict=False 的"跳过"档）—— 关联是从历史数据**继承**来的，用户并没有
    # 在"选关联"，对不上的跳过、不报错；跳过的在返回里列出来（只说类别与编号）。
    from app.modules.task.refs import normalize_task_refs

    inherited, skipped = await normalize_task_refs(
        session,
        user,
        {
            "customer_id": followup.customer_id,
            "contact_id": followup.contact_id,
            "lead_id": followup.lead_id,
            "opportunity_id": followup.opportunity_id,
            "quote_id": followup.quote_id,
            "order_id": followup.order_id,
            "sample_id": followup.sample_id,
        },
        strict=False,
        base_customer_id=followup.customer_id,
    )
    # 打样没有自己的列，落在"来源业务对象"上（任务详情据此跳转）
    sample_id: int | None = inherited.get("sample_id")

    task = Task(
        title=payload.title or followup.next_action or f"跟进后续：{followup.content[:30]}",
        task_type=payload.task_type,
        priority=payload.priority,
        customer_id=inherited.get("customer_id"),
        contact_id=inherited.get("contact_id"),
        lead_id=inherited.get("lead_id"),
        opportunity_id=inherited.get("opportunity_id"),
        quote_id=inherited.get("quote_id"),
        order_id=inherited.get("order_id"),
        owner_id=owner_id,
        status="pending",
        due_at=payload.due_at,
        source="manual",
        # 打样用"来源业务对象"承载（任务表没有 sample 列）
        source_business_type="sample" if sample_id is not None else None,
        source_business_id=sample_id,
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
    message = "后续任务已创建"
    if skipped:
        # "明确处理结果"（11.6 第 4 条）：哪些关联没带过来、为什么，要让人看得见，
        # 而不是悄悄少几个字段。⚠️ 说明里**只有类别与编号** —— 受限对象的名字不写出来。
        message += "；以下关联未继承：" + "、".join(skipped)
    return ok(
        {
            "task_id": task.id,
            "title": task.title,
            "owner_id": task.owner_id,
            "due_at": task.due_at,
            "followup_id": followup.id,
            # 这次没继承成功的业务关联（人话列表），空列表表示全带上了
            "skipped_references": skipped,
        },
        message,
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
