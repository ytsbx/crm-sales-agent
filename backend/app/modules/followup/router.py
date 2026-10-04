"""跟进记录接口（对齐 03-API §24）。"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.data_scope import ensure_in_scope, scoped_owner_ids
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.refs import ensure_refs
from app.core.response import ok, page_data, paginate
from app.modules.customer import service as customer_service
from app.modules.customer.model import Contact, Customer
from app.modules.followup.model import FollowUp
from app.modules.followup.schema import (
    FollowUpCreate,
    FollowUpNextTask,
    FollowUpUpdate,
)
from app.modules.lead.model import Lead
from app.modules.opportunity.model import Opportunity
from app.modules.order.model import SalesOrder
from app.modules.quote.model import Quote
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


async def _visible_followup(
    session: AsyncSession, user: CurrentUser, followup_id: int
) -> FollowUp:
    """取跟进记录并校验数据范围。

    跟进自己没有负责人语义上的"归属"（owner_id 是记录人），
    所以跟着它关联的业务对象走：客户 / 线索 / 商机 / 报价 / 订单，
    哪一个有就用哪一个的范围。全都没关联是脏数据，直接放行并留给治理。
    """
    # 注意：这里必须用 session.get —— 上一版补丁把"改用 _visible_followup"
    # 误插进本函数体，造成自我递归，详情接口直接 500（真被套件抓到过）。
    followup = await session.get(FollowUp, followup_id)
    if followup is None:
        raise AppError(ErrorCode.NOT_FOUND, "跟进记录不存在", 404)

    owner_id: int | None = None
    allow_unowned = False
    if followup.customer_id:
        customer = await session.get(Customer, followup.customer_id)
        owner_id = customer.owner_id if customer else None
        allow_unowned = True
    elif followup.lead_id:
        lead = await session.get(Lead, followup.lead_id)
        owner_id = lead.owner_id if lead else None
        allow_unowned = True
    elif followup.opportunity_id:
        opportunity = await session.get(Opportunity, followup.opportunity_id)
        owner_id = opportunity.owner_id if opportunity else None
    elif followup.quote_id:
        # 只挂报价/订单的跟进此前逐个 elif 都没覆盖，owner_id 停在 None，
        # 于是被"无归属默认拒绝"挡住 —— 连记录人自己都看不了、改不了、删不掉。
        quote = await session.get(Quote, followup.quote_id)
        owner_id = quote.owner_id if quote else None
    elif followup.order_id:
        order = await session.get(SalesOrder, followup.order_id)
        owner_id = order.owner_id if order else None
    else:
        # 什么业务对象都没关联的脏数据：用记录人兜底，至少让他和主管能治理。
        owner_id = followup.owner_id

    # 跟进记录跟着**被关联对象**走。挂公海客户（无负责人）的跟进仍可看：
    # 口径已确认——客户档案本身可见，跟进是同一批信息的延续，
    # 否则"领养前先看看谈到哪一步"就做不到，领取会变成抽盲盒。
    await ensure_in_scope(
        session, user, owner_id=owner_id, label="跟进记录", allow_unowned=allow_unowned
    )
    return followup


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
    if not any(
        [payload.customer_id, payload.opportunity_id, payload.lead_id, payload.quote_id, payload.order_id]
    ):
        raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "跟进记录必须关联一个业务对象")

    # 库里没有外键约束，不校验就会静默留下悬空引用：下面 `if customer:` /
    # `if lead:` 的写法会**安静跳过**，跟进记录看起来正常落库，
    # 但客户"最近跟进时间"永远不会更新 —— 排查起来极难。
    #
    # 注意：Contact 与 Customer 是**两个不同的模型**，不能塞进同一个 ids 字典
    # （那样会把 customer_id 当联系人主键去查）。分两次查，再单独校验归属。
    await ensure_refs(
        session, model=Customer, ids={"customer_id": payload.customer_id}, label="客户"
    )
    if payload.contact_id is not None:
        contact = await session.get(Contact, payload.contact_id)
        if contact is None or contact.deleted_at is not None:
            raise AppError(ErrorCode.NOT_FOUND, f"联系人 id={payload.contact_id} 不存在", 404)
        if payload.customer_id and contact.customer_id != payload.customer_id:
            raise AppError(
                ErrorCode.PARAM_ERROR,
                f"联系人 id={payload.contact_id} 不属于客户 id={payload.customer_id}",
            )
    await ensure_refs(
        session, model=Lead, ids={"lead_id": payload.lead_id}, label="线索"
    )
    await ensure_refs(
        session,
        model=Opportunity,
        ids={"opportunity_id": payload.opportunity_id},
        label="商机",
    )
    await ensure_refs(
        session, model=Quote, ids={"quote_id": payload.quote_id}, label="报价单"
    )
    await ensure_refs(
        session, model=SalesOrder, ids={"order_id": payload.order_id}, label="订单"
    )

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

    # 第三个时钟（§2.3）：重算「约定下次跟进时间」。口径是该客户最近的未完成
    # 跟进任务到期时间——本次跟进约了下次动作就写进去，没约就清空。
    # 与 last_followup_at（真的联系过了）严格分开：约了 ≠ 联系了。
    await customer_service.refresh_next_followup_at(session, payload.customer_id)

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
    followup = await _visible_followup(session, user, followup_id)
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
        title=payload.title or f"跟进后续：{followup.content[:30]}",
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
