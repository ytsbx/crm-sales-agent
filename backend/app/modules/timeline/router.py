"""统一时间线接口（对齐 03-API §34）。"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import CurrentUser, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok
from app.modules.customer.model import Customer
from app.modules.lead.model import Lead
from app.modules.opportunity.model import Opportunity
from app.modules.timeline.service import build_timeline

router = APIRouter(tags=["Timeline"])


async def _ensure_exists(session: AsyncSession, model, object_id: int, label: str) -> None:
    row = (await session.execute(select(model.id).where(model.id == object_id))).first()
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, f"{label}不存在", 404)


@router.get("/customers/{customer_id}/timeline")
async def customer_timeline(
    customer_id: int,
    _: CurrentUser = Depends(require_permission("customer:view")),
    session: AsyncSession = Depends(get_db),
):
    await _ensure_exists(session, Customer, customer_id, "客户")
    return ok(await build_timeline(session, "customer", customer_id))


@router.get("/opportunities/{opportunity_id}/timeline")
async def opportunity_timeline(
    opportunity_id: int,
    _: CurrentUser = Depends(require_permission("opportunity:view")),
    session: AsyncSession = Depends(get_db),
):
    await _ensure_exists(session, Opportunity, opportunity_id, "商机")
    return ok(await build_timeline(session, "opportunity", opportunity_id))


@router.get("/leads/{lead_id}/timeline")
async def lead_timeline(
    lead_id: int,
    _: CurrentUser = Depends(require_permission("lead:view")),
    session: AsyncSession = Depends(get_db),
):
    await _ensure_exists(session, Lead, lead_id, "线索")
    return ok(await build_timeline(session, "lead", lead_id))
