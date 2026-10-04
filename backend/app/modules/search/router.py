"""跨对象的全局搜索。

设计取舍：不做全文检索引擎（ES 留给后续），先用最简单的 ILIKE 覆盖
客户/联系人/线索/商机/报价/订单六类，够日常找人找单用。
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.data_scope import scoped_owner_ids
from app.core.database import get_db
from app.core.deps import CurrentUser, get_current_user
from app.core.response import ok
from app.modules.customer.model import Contact, Customer
from app.modules.lead.model import Lead
from app.modules.opportunity.model import Opportunity, OpportunityStage
from app.modules.order.model import ORDER_STATUS_LABEL, SalesOrder
from app.modules.quote.model import QUOTE_STATUS_LABEL, Quote

router = APIRouter(tags=["Search"])


async def _scope(
    stmt, user: CurrentUser, column, session: AsyncSession, *, allow_unowned: bool = False
):
    """统一走 app/core/data_scope.py（`department_and_sub` 递归到下级部门）。

    `allow_unowned`：公海客户 / 无主线索（owner_id 为空）要显式放行——
    否则"详情给 id 能看、全局搜索却搜不到"，与业务口径不一致。
    """
    owner_ids = await scoped_owner_ids(session, user)
    if owner_ids is None:
        return stmt
    cond = column.in_(owner_ids)
    if allow_unowned:
        cond = or_(cond, column.is_(None))
    return stmt.where(cond)


@router.get("/search")
async def global_search(
    keyword: str = Query(min_length=1, max_length=64),
    limit: int = Query(5, ge=1, le=20),
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    like = f"%{keyword.strip()}%"

    customer_stmt = await _scope(
        select(Customer)
        .where(
            Customer.deleted_at.is_(None),
            or_(
                Customer.name.ilike(like),
                Customer.short_name.ilike(like),
                Customer.region.ilike(like),
            ),
        )
        .order_by(Customer.id.desc())
        .limit(limit),
        user,
        Customer.owner_id,
        session,
        allow_unowned=True,
    )
    customers = [
        {
            "id": row.id,
            "title": row.name,
            "subtitle": f"{row.level or '-'} 级 · {row.region or '未填地区'}",
        }
        for row in (await session.execute(customer_stmt)).scalars().all()
    ]

    # 联系人原先**是六类里唯一没过数据范围的**：按手机号/邮箱一搜全公司，
    # 姓名+手机+邮箱+所属客户名全出来。联系人跟着它所属客户的负责人走。
    contact_rows = (
        await session.execute(
            await _scope(
                select(Contact, Customer.name)
                .join(Customer, Customer.id == Contact.customer_id)
                .where(
                    Contact.deleted_at.is_(None),
                    or_(
                        Contact.name.ilike(like),
                        Contact.mobile.ilike(like),
                        Contact.email.ilike(like),
                    ),
                )
                .order_by(Contact.id.desc())
                .limit(limit),
                user,
                Customer.owner_id,
                session,
                allow_unowned=True,
            )
        )
    ).all()
    contacts = [
        {
            "id": contact.id,
            "customer_id": contact.customer_id,
            "title": contact.name,
            "subtitle": f"{customer_name} · {contact.mobile or '无手机'}",
        }
        for contact, customer_name in contact_rows
    ]

    lead_stmt = await _scope(
        select(Lead)
        .where(
            Lead.deleted_at.is_(None),
            or_(Lead.name.ilike(like), Lead.company_name.ilike(like), Lead.mobile.ilike(like)),
        )
        .order_by(Lead.id.desc())
        .limit(limit),
        user,
        Lead.owner_id,
        session,
        allow_unowned=True,
    )
    leads = [
        {
            "id": row.id,
            "title": row.name,
            "subtitle": f"{row.company_name or '未填公司'} · {row.status}",
        }
        for row in (await session.execute(lead_stmt)).scalars().all()
    ]

    opportunity_stmt = await _scope(
        select(Opportunity, OpportunityStage.name, Customer.name)
        .join(OpportunityStage, OpportunityStage.id == Opportunity.stage_id)
        .join(Customer, Customer.id == Opportunity.customer_id)
        .where(Opportunity.deleted_at.is_(None), Opportunity.title.ilike(like))
        .order_by(Opportunity.id.desc())
        .limit(limit),
        user,
        Opportunity.owner_id,
        session,
    )
    opportunities = [
        {
            "id": opp.id,
            "title": opp.title,
            "subtitle": f"{customer_name} · {stage_name}",
        }
        for opp, stage_name, customer_name in (await session.execute(opportunity_stmt)).all()
    ]

    quote_stmt = await _scope(
        select(Quote, Customer.name)
        .join(Customer, Customer.id == Quote.customer_id)
        .where(Quote.deleted_at.is_(None), Quote.quote_no.ilike(like))
        .order_by(Quote.id.desc())
        .limit(limit),
        user,
        Quote.owner_id,
        session,
    )
    quotes = [
        {
            "id": quote.id,
            "title": quote.quote_no,
            "subtitle": f"{customer_name} · {QUOTE_STATUS_LABEL.get(quote.status, quote.status)}",
        }
        for quote, customer_name in (await session.execute(quote_stmt)).all()
    ]

    order_stmt = await _scope(
        select(SalesOrder, Customer.name)
        .join(Customer, Customer.id == SalesOrder.customer_id)
        .where(SalesOrder.order_no.ilike(like))
        .order_by(SalesOrder.id.desc())
        .limit(limit),
        user,
        SalesOrder.owner_id,
        session,
    )
    orders = [
        {
            "id": order.id,
            "title": order.order_no,
            "subtitle": f"{customer_name} · {ORDER_STATUS_LABEL.get(order.status, order.status)}",
        }
        for order, customer_name in (await session.execute(order_stmt)).all()
    ]

    total = (
        len(customers) + len(contacts) + len(leads) + len(opportunities) + len(quotes) + len(orders)
    )
    return ok(
        {
            "keyword": keyword,
            "total": total,
            "groups": [
                {"type": "customer", "label": "客户", "route": "/customers", "items": customers},
                {"type": "opportunity", "label": "商机", "route": "/opportunities", "items": opportunities},
                {"type": "quote", "label": "报价单", "route": "/quotes", "items": quotes},
                {"type": "order", "label": "订单", "route": "/orders", "items": orders},
                {"type": "lead", "label": "线索", "route": "/leads", "items": leads},
                {"type": "contact", "label": "联系人", "route": "/customers", "items": contacts},
            ],
        }
    )
