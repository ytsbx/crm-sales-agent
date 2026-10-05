"""样品接口（03-API §26、PRD §19）。

12 个接口：
  GET    /samples                 列表
  POST   /samples                 新建申请
  GET    /samples/{id}            详情
  PATCH  /samples/{id}            编辑
  POST   /samples/{id}/approve    审批（批准/拒绝）
  POST   /samples/{id}/resubmit   已驳回原样重新提交
  POST   /samples/{id}/ship       寄样
  POST   /samples/{id}/sign       签收
  POST   /samples/{id}/feedback   登记反馈
  GET    /samples/{id}/items      明细列表
  POST   /samples/{id}/items      加明细
  PATCH  /samples/{id}/items/{iid} 改明细的车间依据（材质/工艺/图纸版本）

最后两个不在文档 §26 里，都是口径落地时必需的写入口：
  - PATCH .../items/{iid}：「材质 / 工艺 / 图纸版本逐行不同」——没有它，
    明细上这三项建完就再也不能改；
  - POST .../resubmit：「驳回不是终态」——没有它，认为驳回理由不成立的跟单
    只能去改个无关字段"骗"系统回到待审批，那条审计留痕是假的。
"""

from datetime import UTC, datetime
import hashlib
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok, page_data, paginate
from app.modules.customer import service as customer_service
from app.modules.followup import service as followup_service
from app.modules.notification import service as notification_service
from app.modules.opportunity import service as opportunity_service
from app.modules.product.model import Sku
from app.modules.sample import service as svc
from app.modules.sample.model import SAMPLE_STATUS_LABEL, SampleItem, SampleRequest, SampleShipment
from app.modules.sample.schema import (
    SampleApprove,
    SampleConfirm,
    SampleCreate,
    SampleFeedback,
    SampleItemAdd,
    SampleItemPatch,
    SampleMade,
    SampleShip,
    SampleSign,
    SampleUpdate,
    SampleFromSource,
    SampleSource,
)

router = APIRouter(tags=["Sample"])

#: 单头上的「车间依据」。明细上的三项（材质 / 工艺 / 图纸版本）在 SampleItemPatch 里，
#: 与这里合起来才是完整的一套——**车间就是照着这几项干活**：
#: 用什么材质、走什么工艺、按哪版图纸、什么时候要、按什么标准验收。
PART_LOCK_FIELDS: tuple[str, ...] = ("target_completion_date", "acceptance_criteria")
PART_LOCK_LABEL = "材质、工艺、图纸版本、目标完成日、验收标准"


def _gate_part_lock(sample: SampleRequest) -> bool:
    """改「车间依据」时统一走这里：判断这单还能不能改、要不要退回重审批。

    口径（业务方 2026-10-05 确认：「改了就要重新审批」）：
    - **待审批**：本来就还没有"已批准的那一版"要作废，照常改；
    - **已批准**：允许改，但这一改等于把审批批的那版资料作废了 → 退回「待审批」，
      由主管重新点一次同意。返回 True 让调用方去改状态；
    - **已驳回**：驳回本身就是「这一版资料不行」这个结论，所以改完不叫「退回」，
      叫**重新提交**——同样回到「待审批」再走一遍。返回 True。
      不给驳回单这条出口，它就是死结：既改不了也重报不了，只能重开一张。
    - **已寄样 / 已签收**：直接拒绝。货已经在客户手上，改资料只会让单子和实物
      对不上；且这时候退回"待审批"更荒唐（货都到了）。真要变就重新开一单。

    只有"锁"没有"退路"会让单据卡死，所以已批准 / 已驳回这两档必须配 _reopen。
    """
    if sample.status in ("shipped", "signed"):
        label = SAMPLE_STATUS_LABEL.get(sample.status, sample.status)
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"样品已「{label}」，不能再增改车间依据（{PART_LOCK_LABEL}）。"
            f"货已出，改了会和实物对不上；确需变更请重新开一单",
            422,
        )
    return sample.status in ("approved", "rejected")


def _reopen(sample: SampleRequest) -> None:
    """把单据拉回「待审批」，等主管重新点一次同意。

    两种由来：已批准的单子改了车间依据（那一版作废），
    或已驳回的单子改完资料重新提交（驳回不是终态）。
    reject_reason 故意不清：主管重审时要能看到上一轮为什么被打回。
    """
    sample.status = "pending"
    sample.approved_at = None



@router.get('/samples/source')
async def sample_source(quote_version_id: int | None = None, inquiry_id: int | None = None,
                        user: CurrentUser = Depends(require_permission('sample:manage')),
                        session: AsyncSession = Depends(get_db)):
    from app.modules.sample import sources
    try:
        source = SampleSource(quote_version_id=quote_version_id, inquiry_id=inquiry_id)
    except ValueError:
        raise AppError(ErrorCode.PARAM_ERROR, '请指定一个有效的报价版本或询价来源', 422)
    return ok(await sources.load_source(session, user, **source.model_dump()))


@router.post('/samples/from-source')
async def create_sample_from_source(payload: SampleFromSource, request: Request,
                                    user: CurrentUser = Depends(require_permission('sample:manage')),
                                    session: AsyncSession = Depends(get_db)):
    from app.modules.sample import sources
    sample, replayed = await sources.create_from_source(session, user, payload, ip=client_ip(request))
    await session.commit()
    await notification_service.dispatch_pending(session)
    result = await svc.detail(session, sample)
    return ok({**result, 'replayed': replayed}, '此前已创建该打样申请' if replayed else '打样申请已创建')


def _event_time(value: datetime) -> str:
    aware = value if value.tzinfo else value.replace(tzinfo=UTC)
    return aware.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")


def _fingerprint(*parts: object) -> str:
    """按**请求内容**算一个稳定短号，专用于幂等事件号。

    同一请求重发 → 同一个号（时间线只一条、主管只收一次通知）；
    内容真的变了 → 号跟着变（照常通知）。
    **绝不能拿 uuid4 当事件号**——那等于没有幂等，弱网重发就会重复留痕、重复通知。
    """
    raw = "|".join("" if part is None else str(part) for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]



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
        # 数据范围：此前只判"商机存在"，拿别人的商机 id 就能建打样单（跨人引用）
        opportunity = await opportunity_service.get_visible_opportunity(
            session, user, payload.opportunity_id
        )
        if customer_id is not None and customer_id != opportunity.customer_id:
            raise AppError(ErrorCode.PARAM_ERROR, "关联商机不属于该客户", 422)
        customer_id = customer_id or opportunity.customer_id
    if customer_id:
        # 客户同样要过数据范围（公海客户放行，与业务口径一致）
        await customer_service.get_visible_customer(session, user, customer_id)
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
            user,
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
        content=f"样品申请 #{sample.id} 已创建（{len(payload.items)} 条明细）",
        business_type="sample",
        business_id=sample.id,
        opportunity_id=sample.opportunity_id,
        sample_id=sample.id,
        operator_id=user.id,
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
    sample = await svc.get_visible_or_404(session, user, sample_id, for_update=True)
    before = await svc.detail(session, sample)
    data = payload.model_dump(exclude_unset=True)
    # 生产打样资料（文档 §3.5）与联系人/负责人/备注走同一个"改了留痕"的入口：
    # 跟单在这里补资料，打样需求单出图时逐项带给车间。
    # 材质 / 工艺 / 图纸版本**不在这个列表里**：它们逐行不同，走明细接口
    # （见下面的 update_sample_item），这样一单多样时每行能有自己的车间依据。
    for field in (
        "contact_id",
        "owner_id",
        "remark",
        "purpose",
        "target_completion_date",
        "acceptance_criteria",
        "sample_fee",
        "production_owner_id",
    ):
        if field in data:
            setattr(sample, field, data[field])
    await session.flush()
    after = await svc.detail(session, sample)
    changed = [field for field in data if before.get(field) != after.get(field)]
    if not changed:
        return ok(after, "打样资料没有变化")

    # 闸门分两档，看**改之前**是什么状态：
    #   已驳回 —— 驳回意见就是「这一版不行」，所以改任何一项都算在响应驳回，一律重新提交
    #             （只认车间依据的话，因「费用超预算」被驳回的单子改完费用还是死结）；
    #   其它   —— 只有动到车间依据才需要重批（改个用途、补个联系人不必惊动主管）。
    # 已寄样/已签收的单子这里会直接抛 422（见 _gate_part_lock 的说明）。
    was_rejected = sample.status == "rejected"
    locked = (
        list(changed)
        if was_rejected
        else [field for field in changed if field in PART_LOCK_FIELDS]
    )
    reopened = False
    if locked and _gate_part_lock(sample):
        _reopen(sample)
        reopened = True
        await session.flush()
        after = await svc.detail(session, sample)

    await write_audit(
        session,
        operator_id=user.id,
        action="update",
        business_type="sample",
        business_id=sample.id,
        before=before,
        after=after,
        ip=client_ip(request),
    )
    if reopened:
        await followup_service.record_and_notify(
            session, customer_id=sample.customer_id, owner_id=sample.owner_id, operator_id=user.id,
            title="打样单重新提交审批" if was_rejected else "打样单退回待审批",
            content=(
                f"打样 #{sample.id} 的资料被修改（{'、'.join(locked)}），"
                + ("该单已重新提交至「待审批」" if was_rejected else "该单已退回「待审批」")
                + "，需要重新审批后才能投产"
            ),
            business_type="sample", business_id=sample.id, sample_id=sample.id,
            opportunity_id=sample.opportunity_id, exclude_user_id=user.id,
            event_key=f"sample:reopen:{sample.id}:{uuid4().hex}",
        )
    else:
        await followup_service.record_and_notify(
            session, customer_id=sample.customer_id, owner_id=sample.owner_id, operator_id=user.id,
            title="打样资料已修改", content=f"打样 #{sample.id} 已修改资料字段：{'、'.join(changed)}",
            business_type="sample", business_id=sample.id, sample_id=sample.id,
            opportunity_id=sample.opportunity_id, exclude_user_id=user.id, event_key=f"sample:update:{sample.id}:{uuid4().hex}",
        )
    await session.commit()
    await notification_service.dispatch_pending(session)
    if reopened:
        message = "已重新提交，需重新审批" if was_rejected else "已退回待审批，需重新审批"
    else:
        message = "已保存"
    return ok(await svc.detail(session, sample), message)


@router.post("/samples/{sample_id}/approve")
async def approve_sample(
    sample_id: int,
    payload: SampleApprove,
    request: Request,
    user: CurrentUser = Depends(require_permission("sample:manage")),
    session: AsyncSession = Depends(get_db),
):
    """审批样品申请：批准或拒绝（PRD §19 的「样品申请」环节）。"""
    sample = await svc.get_visible_or_404(session, user, sample_id, for_update=True)
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
    await followup_service.record_and_notify(
        session, customer_id=sample.customer_id, owner_id=sample.owner_id, operator_id=user.id,
        title="打样审批结果", content=f"打样 #{sample.id} 已{'批准' if payload.approved else '拒绝'}" + (f"；原因：{payload.reject_reason}" if not payload.approved else ""),
        business_type="sample", business_id=sample.id, sample_id=sample.id,
        opportunity_id=sample.opportunity_id, exclude_user_id=user.id, event_key=f"sample:approve:{sample.id}",
    )
    await session.commit()
    await notification_service.dispatch_pending(session)
    return ok(after, "样品已批准" if payload.approved else "样品已拒绝")


@router.post("/samples/{sample_id}/resubmit")
async def resubmit_sample(
    sample_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("sample:manage")),
    session: AsyncSession = Depends(get_db),
):
    """把已驳回的打样单**原样**重新提交审批（业务方 2026-10-05 定）。

    为什么需要一个专门的接口：「改资料会自动回到待审批」只覆盖了"改完再报"。
    跟单如果认为驳回理由不成立、一个字都不想改，就**没有任何入口** ——
    只能去改个无关字段"骗"系统回待审批，那条留痕是假的（审计里写着改了备注，
    实际上什么都没改）。

    与"改资料重提"的分工：
    - 改了东西 → 走 PATCH，自动回待审批，审计里记着改了什么；
    - 什么都没改 → 走这里，明确表达"原样再报一次"。

    资料一字不动、`reject_reason` 也保留 —— 主管重审时要知道上一轮为什么被打回。
    """
    sample = await svc.get_visible_or_404(session, user, sample_id, for_update=True)
    # 状态规则的唯一出处是 SAMPLE_TRANSITIONS，这里不另写一套判断。
    # 非"已驳回"的单子调到这儿会被它拦住（待审批/已批准不支持这个动作）。
    svc.ensure_transition(sample.status, "pending")
    before = await svc.detail(session, sample)

    sample.status = "pending"
    sample.approved_at = None
    await session.flush()
    after = await svc.detail(session, sample)

    await write_audit(
        session,
        operator_id=user.id,
        action="resubmit",
        business_type="sample",
        business_id=sample.id,
        before=before,
        after=after,
        ip=client_ip(request),
    )
    await followup_service.record_and_notify(
        session, customer_id=sample.customer_id, owner_id=sample.owner_id, operator_id=user.id,
        title="打样单重新提交审批",
        content=f"打样 #{sample.id} 已原样重新提交至「待审批」，等待审批",
        business_type="sample", business_id=sample.id, sample_id=sample.id,
        opportunity_id=sample.opportunity_id, exclude_user_id=user.id,
        event_key=f"sample:resubmit:{sample.id}",
    )
    await session.commit()
    await notification_service.dispatch_pending(session)
    return ok(after, "已重新提交，等待审批")


@router.post("/samples/{sample_id}/ship")
async def ship_sample(
    sample_id: int,
    payload: SampleShip,
    request: Request,
    user: CurrentUser = Depends(require_permission("sample:manage")),
    session: AsyncSession = Depends(get_db),
):
    """寄样：登记承运商与快递单号（PRD §19 的「寄样 / 快递单号」）。"""
    sample = await svc.get_visible_or_404(session, user, sample_id, for_update=True)
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
        sample_id=sample.id,
        operator_id=user.id,
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
    sample = await svc.get_visible_or_404(session, user, sample_id, for_update=True)
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
    await followup_service.record_and_notify(
        session, customer_id=sample.customer_id, owner_id=sample.owner_id, operator_id=user.id,
        title="样品已签收", content=f"打样 #{sample.id} 已签收，签收时间 {_event_time(sample.signed_at)}",
        business_type="sample", business_id=sample.id, sample_id=sample.id,
        opportunity_id=sample.opportunity_id, exclude_user_id=user.id, event_key=f"sample:sign:{sample.id}",
    )
    await session.commit()
    await notification_service.dispatch_pending(session)
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
    sample = await svc.get_visible_or_404(session, user, sample_id, for_update=True)
    if not payload.feedback.strip():
        raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "反馈内容不能为空")

    if sample.feedback == payload.feedback:
        return ok(await svc.detail(session, sample), "反馈没有变化")
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
    await followup_service.record_and_notify(
        session, customer_id=sample.customer_id, owner_id=sample.owner_id, operator_id=user.id,
        title="客户样品反馈", content=f"打样 #{sample.id} 客户反馈：{sample.feedback}",
        business_type="sample", business_id=sample.id, sample_id=sample.id,
        opportunity_id=sample.opportunity_id, exclude_user_id=user.id, event_key=f"sample:feedback:{sample.id}:{uuid4().hex}",
    )
    await session.commit()
    await notification_service.dispatch_pending(session)
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

    sample = await svc.get_visible_or_404(session, user, sample_id, for_update=True)
    if sample.status in ("pending", "rejected"):
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED, "还没批准的打样申请不能登记制作完成", 422
        )
    # **重发同一个请求不该留下第二条记录**（弱网下客户端重试很常见）。
    # 判定不能只看 made_at：带说明的重发会走到下面的追加分支，
    # 结果是说明重复、过程记录重复、主管被重复通知。所以说明也要一起看。
    made_at = payload.made_at or sample.made_at or datetime.now(UTC)
    note = (payload.remark or "").strip()
    note_already_present = bool(note) and note in (sample.remark or "")
    if sample.made_at == made_at and (not note or note_already_present):
        return ok(await svc.detail(session, sample), "制作完成记录没有变化")
    sample.made_at = made_at
    if note and not note_already_present:
        prefix = f"{sample.remark}\n" if sample.remark else ""
        sample.remark = f"{prefix}制作说明：{note}"
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
    await followup_service.record_and_notify(
        session, customer_id=sample.customer_id, owner_id=sample.owner_id, operator_id=user.id,
        title="样品制作完成", content=f"打样 #{sample.id} 制作完成，完成时间 {_event_time(sample.made_at)}",
        business_type="sample", business_id=sample.id, sample_id=sample.id,
        opportunity_id=sample.opportunity_id, exclude_user_id=user.id, event_key=f"sample:made:{sample.id}:{_fingerprint(made_at, note)}",
    )
    await session.commit()
    await notification_service.dispatch_pending(session)
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

    sample = await svc.get_visible_or_404(session, user, sample_id, for_update=True)
    if sample.signed_at is None:
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            "还没登记签收，不能登记客户确认（客户收到样品才是确认的前提）",
            422,
        )
    decision = CONFIRM_ACCEPTED if payload.accepted else CONFIRM_REJECTED
    if sample.customer_confirmed_at and sample.confirm_status == decision and sample.confirm_remark == payload.remark and (payload.confirmed_at is None or payload.confirmed_at == sample.customer_confirmed_at):
        return ok(await svc.detail(session, sample), "客户确认记录没有变化")
    sample.confirm_status = decision
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
    await followup_service.record_and_notify(
        session, customer_id=sample.customer_id, owner_id=sample.owner_id, operator_id=user.id,
        title="客户确认样品结果", content=f"打样 #{sample.id} 客户确认：{'接受' if payload.accepted else '未通过'}；{payload.remark or '无补充说明'}",
        business_type="sample", business_id=sample.id, sample_id=sample.id,
        opportunity_id=sample.opportunity_id, exclude_user_id=user.id, event_key=f"sample:confirm:{sample.id}:{uuid4().hex}",
    )
    await session.commit()
    await notification_service.dispatch_pending(session)
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
    sample = await svc.get_visible_or_404(session, user, sample_id, for_update=True)
    # 加明细和改明细走同一道闸门：新明细带进来的材质 / 工艺 / 图纸版本**也是车间依据**，
    # 往已批准的单子里塞新明细，等于让车间按没批过的资料干活 —— 这里原来只挡了
    # 已寄样 / 已签收，已批准的单子能随便加，是个漏口。
    was_rejected = sample.status == "rejected"
    reopened = _gate_part_lock(sample)
    if reopened:
        _reopen(sample)
    item = await _add_item(
        session,
        user,
        sample.id,
        payload.sku_id,
        payload.quantity,
        payload.remark,
        inquiry_id=payload.inquiry_id,
        item_name=payload.item_name,
        craft=payload.craft,
        material=payload.material,
        drawing_version=payload.drawing_version,
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
    await followup_service.record_and_notify(
        session, customer_id=sample.customer_id, owner_id=sample.owner_id, operator_id=user.id,
        title=(
            "打样单重新提交审批"
            if (reopened and was_rejected)
            else ("打样单退回待审批" if reopened else "打样明细已添加")
        ),
        content=(
            f"打样 #{sample.id} 新增明细 #{item.id}，数量 {item.quantity}"
            + (
                "；该单已重新提交至「待审批」"
                if (reopened and was_rejected)
                else ("；该单已退回「待审批」" if reopened else "")
            )
            + ("，需要重新审批后才能投产" if reopened else "")
        ),
        business_type="sample", business_id=sample.id, sample_id=sample.id,
        opportunity_id=sample.opportunity_id, exclude_user_id=user.id, event_key=f"sample:item_create:{item.id}",
    )
    await session.commit()
    await notification_service.dispatch_pending(session)
    return ok(await svc.detail(session, sample), "明细已添加")


async def _add_item(
    session: AsyncSession,
    user: CurrentUser,
    sample_id: int,
    sku_id: int | None,
    quantity,
    remark: str | None,
    *,
    inquiry_id: int | None = None,
    item_name: str | None = None,
    craft: str | None = None,
    material: str | None = None,
    drawing_version: str | None = None,
) -> SampleItem:
    """加一条打样明细。

    两条路径（场景09）：有 SKU 走 SKU；定制件尚无 SKU 时给需求编号——
    定制件本来就要先打样再定 SKU，强制先建档等于把顺序反过来。
    两个都不给直接拒：这条明细得说得清打的是什么。

    车间依据（材质/工艺/图纸版本）随明细一起存：一单多样时每行可以不同。
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
        # 定制需求也要过数据范围：此前只 session.get，能引别人的需求编号
        from app.modules.inquiry import service as inquiry_service

        inquiry = await inquiry_service.get_visible_or_404(session, user, inquiry_id)
        sample = await svc.get_or_404(session, sample_id)
        if inquiry.customer_id and inquiry.customer_id != sample.customer_id:
            raise AppError(ErrorCode.PARAM_ERROR, "定制询价不属于打样客户", 422)
        if inquiry.opportunity_id:
            await opportunity_service.get_visible_opportunity(session, user, inquiry.opportunity_id)
            if sample.opportunity_id is None:
                sample.opportunity_id = inquiry.opportunity_id
            elif inquiry.opportunity_id != sample.opportunity_id:
                raise AppError(ErrorCode.PARAM_ERROR, "定制询价不属于打样商机", 422)
        item = SampleItem(
            sample_request_id=sample_id,
            sku_id=None,
            inquiry_id=inquiry.id,
            inquiry_no_snapshot=inquiry.inquiry_no,
            item_name=item_name or inquiry.title,
            quantity=quantity,
            remark=remark,
        )
    # 车间依据：两条路径共用，避免两边各写一遍漏掉其中一条
    item.craft = craft
    item.material = material
    item.drawing_version = drawing_version
    session.add(item)
    await session.flush()
    return item


async def _get_item_or_404(session: AsyncSession, sample_id: int, item_id: int) -> SampleItem:
    """按明细 id 取行，**并确认它属于这张打样单**。

    少这一步会出一个隐蔽的越权：路径是 /samples/{A}/items/{B}，
    sample_id 只用来过数据范围，明细却按全局 id 找——两者可以对不上，
    于是拿自己单子的 id 就能改到别人单子的明细。必须两边一起校验。
    """
    item = await session.get(SampleItem, item_id)
    if item is None or item.sample_request_id != sample_id:
        raise AppError(ErrorCode.NOT_FOUND, "打样明细不存在", 404)
    return item


@router.patch("/samples/{sample_id}/items/{item_id}")
async def update_sample_item(
    sample_id: int,
    item_id: int,
    payload: SampleItemPatch,
    request: Request,
    user: CurrentUser = Depends(require_permission("sample:manage")),
    session: AsyncSession = Depends(get_db),
):
    """改一条明细的车间依据（材质 / 工艺 / 图纸版本）。

    与单头资料的编辑**共用同一套闸门**（_gate_part_lock）：已批准的单子改了这三项
    就退回「待审批」重新批，已寄样/已签收直接拒。两处各写一份规则迟早会漂移，
    而"哪一档能改"是业务口径，只该有一个出处。
    """
    sample = await svc.get_visible_or_404(session, user, sample_id, for_update=True)
    item = await _get_item_or_404(session, sample_id, item_id)

    before = svc.serialize_item(item)
    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(item, field, value)
    await session.flush()
    after = svc.serialize_item(item)
    changed = [field for field in data if before.get(field) != after.get(field)]
    if not changed:
        return ok(await svc.detail(session, sample), "车间依据没有变化")

    # 闸门放在"确实有改动"之后：同一个值重发一次不该被判违规（它什么都没改）
    reopened = _gate_part_lock(sample)
    if reopened:
        _reopen(sample)

    await write_audit(
        session,
        operator_id=user.id,
        action="update",
        business_type="sample_item",
        business_id=item.id,
        before=before,
        after=after,
        ip=client_ip(request),
    )
    await followup_service.record_and_notify(
        session, customer_id=sample.customer_id, owner_id=sample.owner_id, operator_id=user.id,
        title="打样单退回待审批" if reopened else "打样车间依据已修改",
        content=(
            f"打样 #{sample.id} 明细 #{item.id} 的车间依据已修改（{'、'.join(changed)}）"
            + ("；该单已退回「待审批」，需要重新审批后才能投产" if reopened else "")
        ),
        business_type="sample", business_id=sample.id, sample_id=sample.id,
        opportunity_id=sample.opportunity_id, exclude_user_id=user.id,
        event_key=f"sample:item_update:{item.id}:{uuid4().hex}",
    )
    await session.commit()
    await notification_service.dispatch_pending(session)
    return ok(
        await svc.detail(session, sample),
        "已退回待审批，需重新审批" if reopened else "已保存",
    )
