from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.deps import CurrentUser, require_permission
from app.core.data_scope import scoped_owner_ids
from app.core.errors import AppError, ErrorCode
from app.core.response import ok, page_data, paginate
from app.modules.order import drafts
from app.modules.order.model import OrderDraft
from app.modules.order.schema import OrderDraftCreate, OrderDraftUpdate, OrderDraftConfirm
from app.modules.sample.schema import SampleSource
from app.modules.notification import service as notifications

router = APIRouter(tags=['Order drafts'])


@router.get('/order-drafts/source')
async def source(quote_version_id: int | None = None, inquiry_id: int | None = None,
                 user: CurrentUser = Depends(require_permission('order:manage')), session: AsyncSession = Depends(get_db)):
    try:
        ref = SampleSource(quote_version_id=quote_version_id, inquiry_id=inquiry_id)
    except ValueError:
        raise AppError(ErrorCode.PARAM_ERROR, '请指定一个有效来源', 422)
    return ok(await drafts.preview(session, user, **ref.model_dump()))


@router.get('/order-drafts')
async def listing(opportunity_id: int | None = None, customer_id: int | None = None,
                  page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                  user: CurrentUser = Depends(require_permission('order:view')), session: AsyncSession = Depends(get_db)):
    stmt = select(OrderDraft)
    owners = await scoped_owner_ids(session, user)
    if owners is not None:
        stmt = stmt.where(OrderDraft.owner_id.in_(owners))
    if opportunity_id is not None:
        stmt = stmt.where(OrderDraft.opportunity_id == opportunity_id)
    if customer_id is not None:
        stmt = stmt.where(OrderDraft.customer_id == customer_id)
    rows, count = await paginate(session, stmt.order_by(OrderDraft.id.desc()), page, page_size)
    return ok(page_data([await drafts.detail(session, row) for row in rows], count, page, page_size))


@router.post('/order-drafts')
async def create(payload: OrderDraftCreate, user: CurrentUser = Depends(require_permission('order:manage')),
                 session: AsyncSession = Depends(get_db)):
    row = await drafts.create(session, user, payload)
    await session.commit()
    return ok(await drafts.detail(session, row))


@router.get('/order-drafts/{draft_id}')
async def get(draft_id: int, user: CurrentUser = Depends(require_permission('order:view')), session: AsyncSession = Depends(get_db)):
    return ok(await drafts.detail(session, await drafts.get_visible(session, user, draft_id)))


@router.patch('/order-drafts/{draft_id}')
async def update(draft_id: int, payload: OrderDraftUpdate, user: CurrentUser = Depends(require_permission('order:manage')), session: AsyncSession = Depends(get_db)):
    row = await drafts.update(session, user, draft_id, payload)
    await session.commit()
    return ok(await drafts.detail(session, row))


@router.post('/order-drafts/{draft_id}/confirm')
async def confirm(draft_id: int, payload: OrderDraftConfirm, user: CurrentUser = Depends(require_permission('order:manage')), session: AsyncSession = Depends(get_db)):
    order = await drafts.confirm(session, user, draft_id, payload)
    await session.commit()
    await notifications.dispatch_pending(session)
    return ok({'order_id': order.id, 'order_no': order.order_no})


@router.post('/order-drafts/{draft_id}/documents')
async def document(draft_id: int, user: CurrentUser = Depends(require_permission('order:manage')), session: AsyncSession = Depends(get_db)):
    from app.modules.bizdoc.service import serialize_doc
    doc = await drafts.generate_document(session, user, draft_id)
    await session.commit()
    return ok(serialize_doc(doc))
