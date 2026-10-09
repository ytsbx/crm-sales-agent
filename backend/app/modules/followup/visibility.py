"""跟进记录及其附件的统一可见性规则。"""

from sqlalchemy import and_, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.data_scope import scoped_owner_ids
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

    visible = (await session.execute(select(FollowUp.id).where(
        FollowUp.id == followup.id, await followup_scope_filter(session, user)
    ))).scalar_one_or_none()
    if visible is None:
        raise AppError(ErrorCode.DATA_SCOPE_DENIED, "跟进记录不在你的数据范围内", 403)
    if followup.followup_type == "系统":
        visible = (await session.execute(select(FollowUp.id).where(
            FollowUp.id == followup.id, await system_source_filter(session, user)
        ))).scalar_one_or_none()
        if visible is None:
            raise AppError(ErrorCode.FORBIDDEN, "无权查看此系统过程记录的原单", 403)
    return followup


async def followup_scope_filter(session: AsyncSession, user: CurrentUser):
    """跟进按关联对象归属可见，记录人不限制正常协作；各读入口共用。

    关联优先级沿用详情规则：客户、线索、商机、报价、订单；无关联才看记录人。
    客户和线索的公海记录允许查看，系统事实另过来源单据权限。
    """
    owner_ids = await scoped_owner_ids(session, user)
    if owner_ids is None:
        return true()
    allowed = []
    higher_empty = []
    for kind, model in (("customer", Customer), ("lead", Lead), ("opportunity", Opportunity),
                        ("quote", Quote), ("order", SalesOrder)):
        column = getattr(FollowUp, f"{kind}_id")
        owner_condition = model.owner_id.in_(owner_ids)
        if kind in ("customer", "lead"):
            owner_condition = or_(owner_condition, model.owner_id.is_(None))
        ids = select(model.id).where(owner_condition)
        allowed.append(and_(*higher_empty, column.in_(ids)))
        higher_empty.append(column.is_(None))
    allowed.append(and_(*higher_empty, FollowUp.owner_id.in_(owner_ids)))
    return or_(*allowed)


async def followup_visibility_filter(session: AsyncSession, user: CurrentUser):
    """先过滤范围和系统来源，再计数、排序、分页。模块权限由读入口检查。"""
    return and_(await followup_scope_filter(session, user), await system_source_filter(session, user))


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
