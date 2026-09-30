"""统一时间线接口（对齐 03-API §34）。

`build_timeline` 早就支持 quote / order / contact 三种业务类型，
但只有 customer / opportunity / lead 三个路由 —— 文档里那三条是缺的。
这里补齐，并加上数据范围校验：时间线会带出跟进内容与操作记录，
不能因为"列表看不到"却"时间线能拉"而泄露。
"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.data_scope import ensure_in_scope
from app.core.database import get_db
from app.core.deps import CurrentUser, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok
from app.modules.customer.model import Contact, Customer
from app.modules.lead.model import Lead
from app.modules.opportunity.model import Opportunity
from app.modules.order.model import SalesOrder
from app.modules.quote.model import Quote
from app.modules.timeline.service import build_timeline

router = APIRouter(tags=["Timeline"])


async def _ensure_exists(session: AsyncSession, model, object_id: int, label: str) -> None:
    row = (await session.execute(select(model.id).where(model.id == object_id))).first()
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, f"{label}不存在", 404)


@router.get("/customers/{customer_id}/timeline")
async def customer_timeline(
    customer_id: int,
    user: CurrentUser = Depends(require_permission("customer:view")),
    session: AsyncSession = Depends(get_db),
):
    # 文件头自己写着"时间线会带出跟进内容与操作记录，所以补数据范围校验"，
    # 但当时只补了报价/订单/联系人三条，客户/商机/线索三条漏了——
    # 拿别人的 id 就能读出跟进内容。这里补齐，判据与其余三条一致。
    customer = await session.get(Customer, customer_id)
    if customer is None:
        raise AppError(ErrorCode.NOT_FOUND, "客户不存在", 404)
    await ensure_in_scope(session, user, owner_id=customer.owner_id, label="客户")
    return ok(await build_timeline(session, "customer", customer_id))


@router.get("/opportunities/{opportunity_id}/timeline")
async def opportunity_timeline(
    opportunity_id: int,
    user: CurrentUser = Depends(require_permission("opportunity:view")),
    session: AsyncSession = Depends(get_db),
):
    opportunity = await session.get(Opportunity, opportunity_id)
    if opportunity is None:
        raise AppError(ErrorCode.NOT_FOUND, "商机不存在", 404)
    await ensure_in_scope(session, user, owner_id=opportunity.owner_id, label="商机")
    return ok(await build_timeline(session, "opportunity", opportunity_id))


@router.get("/leads/{lead_id}/timeline")
async def lead_timeline(
    lead_id: int,
    user: CurrentUser = Depends(require_permission("lead:view")),
    session: AsyncSession = Depends(get_db),
):
    lead = await session.get(Lead, lead_id)
    if lead is None:
        raise AppError(ErrorCode.NOT_FOUND, "线索不存在", 404)
    await ensure_in_scope(session, user, owner_id=lead.owner_id, label="线索")
    return ok(await build_timeline(session, "lead", lead_id))


# ------------------------------------------- 03-API §34 补齐的三条时间线


@router.get("/quotes/{quote_id}/timeline")
async def quote_timeline(
    quote_id: int,
    user: CurrentUser = Depends(require_permission("quote:view")),
    session: AsyncSession = Depends(get_db),
):
    quote = await session.get(Quote, quote_id)
    if quote is None or quote.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "报价单不存在", 404)
    await ensure_in_scope(session, user, owner_id=quote.owner_id, label="报价单")
    return ok(await build_timeline(session, "quote", quote_id))


@router.get("/orders/{order_id}/timeline")
async def order_timeline(
    order_id: int,
    user: CurrentUser = Depends(require_permission("order:view")),
    session: AsyncSession = Depends(get_db),
):
    order = await session.get(SalesOrder, order_id)
    if order is None:
        raise AppError(ErrorCode.NOT_FOUND, "订单不存在", 404)
    await ensure_in_scope(session, user, owner_id=order.owner_id, label="订单")
    return ok(await build_timeline(session, "order", order_id))


@router.get("/contacts/{contact_id}/timeline")
async def contact_timeline(
    contact_id: int,
    user: CurrentUser = Depends(require_permission("customer:view")),
    session: AsyncSession = Depends(get_db),
):
    """联系人时间线。联系人自己没有负责人，跟着所属客户走范围。"""
    contact = await session.get(Contact, contact_id)
    if contact is None or contact.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "联系人不存在", 404)
    if contact.customer_id:
        customer = await session.get(Customer, contact.customer_id)
        if customer is not None:
            await ensure_in_scope(
                session, user, owner_id=customer.owner_id, label="客户"
            )
    return ok(await build_timeline(session, "contact", contact_id))
