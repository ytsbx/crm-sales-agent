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
from app.modules.followup import service as followup_service
from app.modules.notification import service as notification_service
from app.modules.opportunity.model import Opportunity
from app.modules.product.model import Sku
from app.modules.sample import service as svc
from app.modules.sample.model import SAMPLE_STATUS_LABEL, SampleItem, SampleRequest, SampleShipment
from app.modules.sample.schema import (
    SampleApprove,
    SampleConfirm,
    SampleCreate,
    SampleFeedback,
    SampleItemAdd,
    SampleMade,
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
        await _add_item(
            session,
            sample.id,
            item.sku_id,
            item.quantity,
            item.remark,
            inquiry_id=item.inquiry_id,
            item_name=item.item_name,
        )

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
    # 领导六阶段口径"过程记录"：打样动作自动写跟进并推送业务主管
    await followup_service.record_and_notify(
        session,
        customer_id=sample.customer_id,
        owner_id=sample.owner_id,
        title="打样申请已创建",
        content=f"样品申请已创建（{len(payload.items)} 个 SKU）",
        business_type="sample",
        business_id=sample.id,
        opportunity_id=sample.opportunity_id,
        exclude_user_id=user.id,
        event_key=f"sample:create:{sample.id}",
    )
    await session.commit()
    await notification_service.dispatch_pending(session)
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
    # 生产打样资料（文档 §3.5）与联系人/负责人/备注走同一个"改了留痕"的入口：
    # 跟单在这里补资料，打样需求单出图时逐项带给车间
    for field in (
        "contact_id",
        "owner_id",
        "remark",
        "purpose",
        "craft",
        "material",
        "drawing_version",
        "target_completion_date",
        "acceptance_criteria",
        "sample_fee",
        "production_owner_id",
    ):
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
    await followup_service.record_and_notify(
        session,
        customer_id=sample.customer_id,
        owner_id=sample.owner_id,
        title="打样已寄出",
        content=f"打样已寄出（{payload.carrier or '承运商待定'} 单号 {payload.tracking_no or '无'}）",
        business_type="sample",
        business_id=sample.id,
        opportunity_id=sample.opportunity_id,
        exclude_user_id=user.id,
        event_key=f"sample:ship:{sample.id}",
    )
    await session.commit()
    await notification_service.dispatch_pending(session)
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


@router.post("/samples/{sample_id}/made")
async def register_sample_made(
    sample_id: int,
    payload: SampleMade,
    request: Request,
    user: CurrentUser = Depends(require_permission("sample:manage")),
    session: AsyncSession = Depends(get_db),
):
    """登记制作完成（文档 §3.5「分别记录制作、寄出、签收、客户确认」）。

    刻意**不做成状态闸门**：CRM 管不到车间，把"制作完成"变成必须点的状态，
    只会让跟单为了往下走而随手点一下，反而污染数据。这里只记录事实与时间，
    供打样需求单与跟单看板回答"目标完成日到了没有"。
    """
    from datetime import UTC, datetime

    sample = await svc.get_visible_or_404(session, user, sample_id)
    if sample.status in ("pending", "rejected"):
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED, "还没批准的打样申请不能登记制作完成", 422
        )
    sample.made_at = payload.made_at or datetime.now(UTC)
    if payload.remark:
        prefix = f"{sample.remark}\n" if sample.remark else ""
        sample.remark = f"{prefix}制作说明：{payload.remark}"
    await session.flush()
    after = await svc.detail(session, sample)
    await write_audit(
        session,
        operator_id=user.id,
        action="sample_made",
        business_type="sample",
        business_id=sample.id,
        after={"made_at": sample.made_at.isoformat()},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(after, "已登记制作完成")


@router.post("/samples/{sample_id}/confirm")
async def confirm_sample(
    sample_id: int,
    payload: SampleConfirm,
    request: Request,
    user: CurrentUser = Depends(require_permission("sample:manage")),
    session: AsyncSession = Depends(get_db),
):
    """登记客户确认结果（文档 §3.5）。

    规则只有一条，但它是这条流程的重点：**必须先签收才能确认**。
    客户没收到样品就"确认接受"在业务上是假数据；签收是物流事实、
    确认是业务事实，分开记才答得了"这批样到底过没过"。
    """
    from datetime import UTC, datetime

    from app.modules.sample.model import CONFIRM_ACCEPTED, CONFIRM_REJECTED

    sample = await svc.get_visible_or_404(session, user, sample_id)
    if sample.signed_at is None:
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            "还没登记签收，不能登记客户确认（客户收到样品才是确认的前提）",
            422,
        )
    sample.confirm_status = CONFIRM_ACCEPTED if payload.accepted else CONFIRM_REJECTED
    sample.customer_confirmed_at = payload.confirmed_at or datetime.now(UTC)
    sample.confirm_remark = payload.remark
    if payload.remark:
        # 客户说的话留进反馈里：这是"为什么过/不过"的原始依据
        sample.feedback = payload.remark
    await svc.record_opportunity_touch(
        session, sample, "客户已确认样品" if payload.accepted else "客户未通过样品"
    )
    await session.flush()
    after = await svc.detail(session, sample)
    await write_audit(
        session,
        operator_id=user.id,
        action="sample_confirm",
        business_type="sample",
        business_id=sample.id,
        after={
            "confirm_status": sample.confirm_status,
            "customer_confirmed_at": sample.customer_confirmed_at.isoformat(),
        },
        ip=client_ip(request),
    )
    await session.commit()
    return ok(after, "客户确认已登记")


@router.get("/samples/{sample_id}/items")
async def list_sample_items(
    sample_id: int,
    user: CurrentUser = Depends(require_permission("sample:view")),
    session: AsyncSession = Depends(get_db),
):
    await svc.get_visible_or_404(session, user, sample_id)
    items = await svc.items_of(session, sample_id)
    sku_ids = {item.sku_id for item in items if item.sku_id}  # 定制项无 SKU
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
    item = await _add_item(
        session,
        sample.id,
        payload.sku_id,
        payload.quantity,
        payload.remark,
        inquiry_id=payload.inquiry_id,
        item_name=payload.item_name,
    )
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
    session: AsyncSession,
    sample_id: int,
    sku_id: int | None,
    quantity,
    remark: str | None,
    *,
    inquiry_id: int | None = None,
    item_name: str | None = None,
) -> SampleItem:
    """加一条打样明细。

    两条路径（场景09）：有 SKU 走 SKU；定制件尚无 SKU 时给需求编号——
    定制件本来就要先打样再定 SKU，强制先建档等于把顺序反过来。
    两个都不给直接拒：这条明细得说得清打的是什么。
    """
    if sku_id is None and inquiry_id is None:
        raise AppError(
            ErrorCode.PARAM_ERROR, "打样明细必须关联 SKU 或定制需求编号", 422
        )
    if sku_id is not None:
        sku = await session.get(Sku, sku_id)
        if sku is None or sku.deleted_at is not None:
            raise AppError(ErrorCode.NOT_FOUND, f"SKU {sku_id} 不存在", 404)
        item = SampleItem(
            sample_request_id=sample_id,
            sku_id=sku_id,
            quantity=quantity,
            remark=remark,
        )
    else:
        from app.modules.inquiry.model import CustomInquiry

        inquiry = await session.get(CustomInquiry, inquiry_id)
        if inquiry is None or inquiry.deleted_at is not None:
            raise AppError(ErrorCode.NOT_FOUND, f"定制需求 id={inquiry_id} 不存在", 404)
        item = SampleItem(
            sample_request_id=sample_id,
            sku_id=None,
            inquiry_id=inquiry.id,
            inquiry_no_snapshot=inquiry.inquiry_no,
            item_name=item_name or inquiry.title,
            quantity=quantity,
            remark=remark,
        )
    session.add(item)
    await session.flush()
    return item
