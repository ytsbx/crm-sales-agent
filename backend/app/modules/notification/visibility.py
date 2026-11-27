"""过程通知的原单权限：生成、读取与真正投递保持同一数据范围。"""
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.data_scope import scoped_owner_ids
from app.core.deps import CurrentUser, ensure_permission
from app.core.errors import AppError
from app.modules.customer.model import Customer
from app.modules.customer.visibility import customer_scope_clause_from
from app.modules.lead.model import Lead
from app.modules.notification.model import BusinessEvent, Notification
from app.modules.opportunity.model import Opportunity
from app.modules.order.model import SalesOrder
from app.modules.quote.model import Quote
from app.modules.sample.model import SampleRequest


async def direct_notification_visible(session: AsyncSession, user: CurrentUser, row: Notification) -> bool:
    """推送前按原单详情的权限复核，没有关联单据的系统消息沿用原规则。"""
    if row.business_id is None:
        return True
    from app.modules.contract.router import _ensure_doc_in_scope
    from app.modules.contract.service import get_doc_or_404
    from app.modules.customer.service import get_visible_customer
    from app.modules.lead.service import get_visible_lead
    from app.modules.opportunity.service import get_visible_opportunity
    from app.modules.order.service import get_visible_order
    from app.modules.quote.service import get_visible_quote
    from app.modules.sample.service import get_visible_or_404 as get_visible_sample
    from app.modules.settings.model import PublicPoolRecycleCandidate
    from app.modules.settings.service import assert_candidate_in_scope
    from app.modules.task.router import _visible_task

    accessors = {
        "customer": ("customer:view", get_visible_customer),
        "lead": ("lead:view", get_visible_lead),
        "opportunity": ("opportunity:view", get_visible_opportunity),
        "quote": ("quote:view", get_visible_quote),
        "order": ("order:view", get_visible_order),
        "sample": ("sample:view", get_visible_sample),
        "task": ("task:view", _visible_task),
    }
    try:
        if row.business_type == "contract":
            ensure_permission(user, "order:view")
            doc = await get_doc_or_404(session, row.business_id)
            await _ensure_doc_in_scope(session, user, doc)
        elif row.business_type == "pool_recycle":
            ensure_permission(user, "customer:pool_review")
            candidate = await session.get(PublicPoolRecycleCandidate, row.business_id)
            if candidate is None:
                return False
            await assert_candidate_in_scope(session, user, candidate)
        elif row.business_type in accessors:
            permission, lookup = accessors[row.business_type]
            ensure_permission(user, permission)
            await lookup(session, user, row.business_id)
        else:
            return False
    except AppError as error:
        if error.http_status in (403, 404):
            return False
        raise
    return True


async def process_notification_filter(session: AsyncSession, user: CurrentUser):
    owners = await scoped_owner_ids(session, user)
    admin = 'admin' in user.roles
    allowed = []
    for kind, model in {'customer': Customer, 'lead': Lead, 'opportunity': Opportunity,
                        'quote': Quote, 'order': SalesOrder, 'sample': SampleRequest}.items():
        if not admin and not user.has(f'{kind}:view'):
            continue
        stmt = select(model.id).where(model.id == Notification.business_id)
        if owners is not None:
            if kind == 'customer':
                stmt = stmt.where(customer_scope_clause_from(owners))
            elif kind == 'lead':
                stmt = stmt.where(or_(model.owner_id.in_(owners), model.owner_id.is_(None)))
            else:
                stmt = stmt.where(model.owner_id.in_(owners))
        if kind == 'quote':
            stmt = stmt.where(model.deleted_at.is_(None))
        allowed.append(and_(Notification.business_type == kind, stmt.exists()))
    payload = BusinessEvent.notification_payload
    customer_id = payload['customer_id'].as_integer()
    required = payload['required_permission'].as_string()
    customer_visible = customer_id.is_(None)
    if admin or user.has('customer:view'):
        customers = select(Customer.id).where(Customer.id == customer_id)
        if owners is not None:
            customers = customers.where(customer_scope_clause_from(owners))
        customer_visible = or_(customer_visible, customers.exists())
    event = select(BusinessEvent.id).where(
        BusinessEvent.id == Notification.business_event_id,
        customer_visible,
        or_(required.is_(None), required == 'followup:view' if admin or user.has('followup:view') else False),
    )
    return or_(Notification.business_event_id.is_(None), and_(or_(False, *allowed), event.exists()))
