"""过程通知的原单权限：生成、读取与真正投递保持同一数据范围。"""
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.data_scope import scoped_owner_ids
from app.core.deps import CurrentUser
from app.modules.customer.model import Customer
from app.modules.lead.model import Lead
from app.modules.notification.model import BusinessEvent, Notification
from app.modules.opportunity.model import Opportunity
from app.modules.order.model import SalesOrder
from app.modules.quote.model import Quote
from app.modules.sample.model import SampleRequest


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
            stmt = stmt.where(or_(model.owner_id.in_(owners), model.owner_id.is_(None))
                              if kind in ('customer', 'lead') else model.owner_id.in_(owners))
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
            customers = customers.where(or_(Customer.owner_id.in_(owners), Customer.owner_id.is_(None)))
        customer_visible = or_(customer_visible, customers.exists())
    event = select(BusinessEvent.id).where(
        BusinessEvent.id == Notification.business_event_id,
        customer_visible,
        or_(required.is_(None), required == 'followup:view' if admin or user.has('followup:view') else False),
    )
    return or_(Notification.business_event_id.is_(None), and_(or_(False, *allowed), event.exists()))
