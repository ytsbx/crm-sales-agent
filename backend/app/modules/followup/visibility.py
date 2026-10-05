"""跟进记录及其附件的统一可见性规则。"""

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.data_scope import ensure_in_scope, scoped_owner_ids
from app.core.deps import CurrentUser
from app.core.errors import AppError, ErrorCode
from app.modules.customer.model import Customer
from app.modules.followup.model import FollowUp
from app.modules.lead.model import Lead
from app.modules.opportunity.model import Opportunity
from app.modules.order.model import SalesOrder
from app.modules.quote.model import Quote
from app.modules.sample.model import SampleRequest


async def get_visible_followup(
    session: AsyncSession, user: CurrentUser, followup_id: int
) -> FollowUp:
    """取跟进并按其关联对象的数据范围校验。"""
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
        quote = await session.get(Quote, followup.quote_id)
        owner_id = quote.owner_id if quote else None
    elif followup.order_id:
        order = await session.get(SalesOrder, followup.order_id)
        owner_id = order.owner_id if order else None
    else:
        # 无关联对象的历史脏数据只允许记录人及其数据范围内的用户治理。
        owner_id = followup.owner_id

    await ensure_in_scope(
        session,
        user,
        owner_id=owner_id,
        label="跟进记录",
        allow_unowned=allow_unowned,
    )
    if followup.followup_type == "系统":
        visible = (await session.execute(select(FollowUp.id).where(
            FollowUp.id == followup.id, await system_source_filter(session, user)
        ))).scalar_one_or_none()
        if visible is None:
            raise AppError(ErrorCode.FORBIDDEN, "无权查看此系统过程记录的原单", 403)
    return followup


async def system_source_filter(session: AsyncSession, user: CurrentUser):
    """先过滤不可见的系统事实，再取 limit，避免旧的可见动态被挤掉。"""
    owner_ids = await scoped_owner_ids(session, user)
    allowed = []
    higher_sources_empty = []
    for kind, model in (("sample", SampleRequest), ("order", SalesOrder), ("quote", Quote), ("opportunity", Opportunity)):
        column = getattr(FollowUp, f"{kind}_id")
        if "admin" in user.roles or user.has(f"{kind}:view"):
            stmt = select(model.id).where(model.id == column)
            if owner_ids is not None:
                stmt = stmt.where(model.owner_id.in_(owner_ids))
            if kind == "quote":
                stmt = stmt.where(Quote.deleted_at.is_(None))
            allowed.append(and_(*higher_sources_empty, stmt.exists()))
        higher_sources_empty.append(column.is_(None))
    return or_(FollowUp.followup_type != "系统", and_(*higher_sources_empty), *allowed)
