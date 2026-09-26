"""样品接口（03-API §26、PRD §19）。

10 个接口与文档 §26 一一对应：
  GET    /samples                 列表
  POST   /samples                 新建申请
  GET    /samples/{id}            详情
  PATCH  /samples/{id}            编辑
  POST   /samples/{id}/approve    审批（批准/拒绝）
  POST   /samples/{id}/ship       寄样
  POST   /samples/{id}/sign       签收
  POST   /samples/{id}/feedback   登记反馈
  GET    /samples/{id}/items      明细列表
  POST   /samples/{id}/items      加明细
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok, page_data, paginate
from app.modules.opportunity.model import Opportunity
from app.modules.product.model import Sku
from app.modules.sample import service as svc
from app.modules.sample.model import SAMPLE_STATUS_LABEL, SampleItem, SampleRequest, SampleShipment
from app.modules.sample.schema import (
    SampleApprove,
    SampleCreate,
    SampleFeedback,
    SampleItemAdd,
    SampleShip,
    SampleSign,
    SampleUpdate,
)

router = APIRouter(tags=["Sample"])


@router.get("/samples")
async def list_samples(
    keyword: str | None = None,
    status: str | None = None,
    customer_id: int | None = None,
    opportunity_id: int | None = None,
    owner_id: int | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user: CurrentUser = Depends(require_permission("sample:view")),
    session: AsyncSession = Depends(get_db),
):
    stmt = svc.build_list_stmt(
        keyword=keyword,
        status=status,
        customer_id=customer_id,
        opportunity_id=opportunity_id,
        owner_id=owner_id,
    )
    stmt = await svc.apply_data_scope(stmt, user, session)
    rows, total = await paginate(session, stmt.order_by(SampleRequest.id.desc()), page, page_size)

    return ok(page_data(await svc.list_payload(session, rows), total, page, page_size))


@router.post("/samples")
async def create_sample(
    payload: SampleCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("sample:manage")),
    session: AsyncSession = Depends(get_db),
):
    """新建样品申请。明细可一次带上，也可稍后用 /samples/{id}/items 追加。"""
    opportunity = None
    customer_id = payload.customer_id
    if payload.opportunity_id:
        opportunity = await session.get(Opportunity, payload.opportunity_id)
        if opportunity is None or opportunity.deleted_at is not None:
            raise AppError(ErrorCode.NOT_FOUND, "商机不存在", 404)
        customer_id = customer_id or opportunity.customer_id
    if customer_id:
        from app.modules.customer.model import Customer

        if await session.get(Customer, customer_id) is None:
            raise AppError(ErrorCode.NOT_FOUND, "客户不存在", 404)
    if not customer_id and not opportunity:
        raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "必须指定商机或客户")

    # 负责人：传入 > 商机负责人 > 当前用户
    owner_id = payload.owner_id or (opportunity.owner_id if opportunity else None) or user.id

    sample = SampleRequest(
        opportunity_id=payload.opportunity_id,
        customer_id=customer_id,
        contact_id=payload.contact_id,
        owner_id=owner_id,
        status="pending",
        remark=payload.remark,
        requested_at=svc.now(),
        created_by=user.id,
        created_at=svc.now(),
    )
    session.add(sample)
    await session.flush()

    for item in payload.items:
        await _add_item(session, sample.id, item.sku_id, item.quantity, item.remark)

    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="create",
        business_type="sample",
        business_id=sample.id,
        after=await svc.detail(session, sample),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(await svc.detail(session, sample), "样品申请已创建")


@router.get("/samples/{sample_id}")
async def get_sample(
    sample_id: int,
    user: CurrentUser = Depends(require_permission("sample:view")),
    session: AsyncSession = Depends(get_db),
):
    sample = await svc.get_visible_or_404(session, user, sample_id)
    return ok(await svc.detail(session, sample))


@router.patch("/samples/{sample_id}")
async def update_sample(
    sample_id: int,
    payload: SampleUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("sample:manage")),
    session: AsyncSession = Depends(get_db),
):
    sample = await svc.get_visible_or_404(session, user, sample_id)
    before = await svc.detail(session, sample)
    data = payload.model_dump(exclude_unset=True)
    for field in ("contact_id", "owner_id", "remark"):
        if field in data:
            setattr(sample, field, data[field])
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="update",
        business_type="sample",
        business_id=sample.id,
        before=before,
        after=await svc.detail(session, sample),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(await svc.detail(session, sample), "已保存")


@router.post("/samples/{sample_id}/approve")
async def approve_sample(
    sample_id: int,
    payload: SampleApprove,
    request: Request,
    user: CurrentUser = Depends(require_permission("sample:manage")),
    session: AsyncSession = Depends(get_db),
):
    """审批样品申请：批准或拒绝（PRD §19 的「样品申请」环节）。"""
    sample = await svc.get_visible_or_404(session, user, sample_id)
    target = "approved" if payload.approved else "rejected"
    svc.ensure_transition(sample.status, target)

    if not payload.approved and not (payload.reject_reason or "").strip():
        raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "拒绝时必须填写原因")

    sample.status = target
    sample.approved_at = svc.now()
    sample.reject_reason = None if payload.approved else payload.reject_reason
    await svc.record_opportunity_touch(
        session, sample, "已批准" if payload.approved else "被拒绝"
    )
    await session.flush()

    after = await svc.detail(session, sample)
    await write_audit(
        session,
        operator_id=user.id,
        action="approve" if payload.approved else "reject",
        business_type="sample",
        business_id=sample.id,
        after=after,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(after, "样品已批准" if payload.approved else "样品已拒绝")


@router.post("/samples/{sample_id}/ship")
async def ship_sample(
    sample_id: int,
    payload: SampleShip,
    request: Request,
    user: CurrentUser = Depends(require_permission("sample:manage")),
    session: AsyncSession = Depends(get_db),
):
    """寄样：登记承运商与快递单号（PRD §19 的「寄样 / 快递单号」）。"""
    sample = await svc.get_visible_or_404(session, user, sample_id)
    svc.ensure_transition(sample.status, "shipped")

    shipped_at = (
        datetime.combine(payload.shipped_at, datetime.min.time(), tzinfo=UTC)
        if payload.shipped_at
        else svc.now()
    )
    shipment = SampleShipment(
        sample_request_id=sample.id,
        carrier=payload.carrier,
        tracking_no=payload.tracking_no,
        shipping_fee=payload.shipping_fee,
        shipped_at=shipped_at,
    )
    session.add(shipment)

    sample.status = "shipped"
    sample.shipped_at = shipped_at
    await svc.record_opportunity_touch(session, sample, "已寄出")
    await session.flush()

    after = await svc.detail(session, sample)
    await write_audit(
        session,
        operator_id=user.id,
        action="ship",
        business_type="sample",
        business_id=sample.id,
        after=after,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(after, "已登记寄样")


@router.post("/samples/{sample_id}/sign")
async def sign_sample(
    sample_id: int,
    payload: SampleSign,
    request: Request,
    user: CurrentUser = Depends(require_permission("sample:manage")),
    session: AsyncSession = Depends(get_db),
):
    """签收确认（PRD §19 的「签收」）。"""
    sample = await svc.get_visible_or_404(session, user, sample_id)
    svc.ensure_transition(sample.status, "signed")

    signed_at = (
        datetime.combine(payload.signed_at, datetime.min.time(), tzinfo=UTC)
        if payload.signed_at
        else svc.now()
    )
    sample.status = "signed"
    sample.signed_at = signed_at

    # 寄样记录也标记签收时间，便于对账
    for shipment in await svc.shipments_of(session, sample.id):
        if shipment.signed_at is None:
            shipment.signed_at = signed_at

    await svc.record_opportunity_touch(session, sample, "客户已签收")
    await session.flush()

    after = await svc.detail(session, sample)
    await write_audit(
        session,
        operator_id=user.id,
        action="sign",
        business_type="sample",
        business_id=sample.id,
        after=after,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(after, "已登记签收")


@router.post("/samples/{sample_id}/feedback")
async def feedback_sample(
    sample_id: int,
    payload: SampleFeedback,
    request: Request,
    user: CurrentUser = Depends(require_permission("sample:manage")),
    session: AsyncSession = Depends(get_db),
):
    """登记客户反馈（PRD §19 的「反馈」）。"""
    sample = await svc.get_visible_or_404(session, user, sample_id)
    if not payload.feedback.strip():
        raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "反馈内容不能为空")

    sample.feedback = payload.feedback
    await svc.record_opportunity_touch(session, sample, "客户已反馈")
    await session.flush()

    after = await svc.detail(session, sample)
    await write_audit(
        session,
        operator_id=user.id,
        action="feedback",
        business_type="sample",
        business_id=sample.id,
        after=after,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(after, "反馈已登记")


@router.get("/samples/{sample_id}/items")
async def list_sample_items(
    sample_id: int,
    user: CurrentUser = Depends(require_permission("sample:view")),
    session: AsyncSession = Depends(get_db),
):
    await svc.get_visible_or_404(session, user, sample_id)
    items = await svc.items_of(session, sample_id)
    sku_ids = {item.sku_id for item in items}
    skus = {}
    if sku_ids:
        skus = {
            sku.id: sku
            for sku in (
                await session.execute(select(Sku).where(Sku.id.in_(sku_ids)))
            ).scalars().all()
        }
    return ok([svc.serialize_item(item, skus.get(item.sku_id)) for item in items])


@router.post("/samples/{sample_id}/items")
async def add_sample_item(
    sample_id: int,
    payload: SampleItemAdd,
    request: Request,
    user: CurrentUser = Depends(require_permission("sample:manage")),
    session: AsyncSession = Depends(get_db),
):
    sample = await svc.get_visible_or_404(session, user, sample_id)
    if sample.status in ("shipped", "signed"):
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"样品已是「{SAMPLE_STATUS_LABEL.get(sample.status, sample.status)}」，不能再加明细",
            422,
        )
    item = await _add_item(session, sample.id, payload.sku_id, payload.quantity, payload.remark)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="create",
        business_type="sample_item",
        business_id=item.id,
        after=svc.serialize_item(item),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(await svc.detail(session, sample), "明细已添加")


async def _add_item(
    session: AsyncSession, sample_id: int, sku_id: int, quantity, remark: str | None
) -> SampleItem:
    sku = await session.get(Sku, sku_id)
    if sku is None or sku.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, f"SKU {sku_id} 不存在", 404)
    item = SampleItem(
        sample_request_id=sample_id, sku_id=sku_id, quantity=quantity, remark=remark
    )
    session.add(item)
    await session.flush()
    return item
