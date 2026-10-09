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
from app.core.timebase import business_day_start, to_business, today_business
from app.modules.customer import service as customer_service
from app.modules.customer.model import Contact
from app.modules.user.model import User
from app.modules.file.model import BusinessFile, FileRecord
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
    SampleResubmit,
    SampleRevise,
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
    if sample.made_at is not None or sample.status in ("shipped", "signed"):
        # 已制作 / 已寄出：车间依据**不能再原地改**（§3.3 口径 A）。
        # 旧写法只挡了 shipped/signed，于是"已制作"的单子改完只是回到待审批——
        # 同一行上留着旧的制作时间却写着新资料，和已经做出来的实物对不上，
        # 也看不出这是第几版。现在一律要求开新修订版：
        # 原单连同制作/寄送事实冻结保留，新版从当前资料起改。
        why = (
            "已登记制作完成" if sample.made_at is not None
            else f"已「{SAMPLE_STATUS_LABEL.get(sample.status, sample.status)}」"
        )
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"该打样单{why}，不能再原地改车间依据（{PART_LOCK_LABEL}）——"
            f"改了会和已做出来的实物对不上。请开新修订版"
            f"（POST /samples/{sample.id}/revise）：原单的制作与寄送事实会冻结保留，"
            f"新版本从当前资料起改，再走一遍审批。",
            422,
        )
    return sample.status in ("approved", "rejected")


def _reopen(sample: SampleRequest) -> None:
    """把单据拉回「待审批」，等主管重新点一次同意。

    两种由来：已批准的单子改了车间依据（那一版作废），
    或已驳回的单子改完资料重新提交（驳回不是终态）。
    reject_reason 故意不清：主管重审时要能看到上一轮为什么被打回。

    **审批轮次在这里自增**（第一批返修 §3.2）：一次"真的重新提交"就是新的一轮，
    通知/时间线的事件键带上它，否则第二轮会撞上第一轮的固定键被去重吞掉，
    事后看不出被驳回过几次。只在状态**真的发生变化**时自增——
    待审批期间反复改资料不该虚增轮次。
    """
    if sample.status != "pending":
        sample.review_round = (sample.review_round or 1) + 1
    sample.status = "pending"
    sample.approved_at = None
    # 换轮次就把上一轮的请求键清掉：留着会让"上一轮那次提交的重试"误判成幂等。
    sample.review_request_key = None



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
    include_history: bool = Query(
        False, description="是否包含被新修订版取代的历史版本（默认只看当前版本）"
    ),
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
    if not include_history:
        # 默认只列**当前版本**：开过修订版的单子（§3.3）否则会在列表里出现两份，
        # 看着像重复造单。历史版本仍可按 id 打开、也可显式传 include_history=true 列出——
        # "历史版本可核对"这条要求不受影响。
        stmt = stmt.where(
            SampleRequest.id.not_in(
                select(SampleRequest.parent_id).where(SampleRequest.parent_id.is_not(None))
            )
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
    # 落库前校验关联完整性（审查 C3-02）：以前"不存在的负责人"要到 flush 才炸成 500，
    # "已停用的负责人"更是直接 200 落库
    await _assert_active_owner(session, owner_id, "负责人")
    await _assert_contact_belongs(session, payload.contact_id, customer_id)
    if payload.production_owner_id is not None:
        await _assert_active_owner(session, payload.production_owner_id, "制作负责人")

    sample = SampleRequest(
        opportunity_id=payload.opportunity_id,
        customer_id=customer_id,
        contact_id=payload.contact_id,
        owner_id=owner_id,
        production_owner_id=payload.production_owner_id,
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
            # ⚠️ 这三项以前**没往下传**，于是"新建时带明细"的 craft/material/
            # drawing_version 被静默丢弃（库内三项全 NULL），而"后续追加明细"
            # 那条路是传的 —— 同一份数据两条路两个结果（审查 C3-01）。
            # 打样制作依据（工艺/材质/图纸版本）就靠这三个字段，丢了车间没法干活。
            craft=item.craft,
            material=item.material,
            drawing_version=item.drawing_version,
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
    # ---- 落库前校验关联完整性（审查 C3-02）----
    #
    # 以前这里直接把传进来的值 setattr 上去，于是：
    #   · `owner_id: null` → 200 落库，**原负责人立刻读不到这张单**
    #     （服务端报「该样品申请没有负责人，无法判定可见范围，已拒绝访问」——
    #      拦是拦住了，但入口没堵，单子就成了谁都进不去的孤儿）；
    #   · 不存在的负责人 → flush 时外键炸成 **500**；
    #   · 已停用的负责人 → 200 落库，单子挂在一个登不上系统的人名下；
    #   · 别人家的联系人 → 200 落库，打样通知发给别家联系人。
    # 这里把四种都变成明确的 4xx。
    if "owner_id" in data:
        await _assert_active_owner(session, data["owner_id"], "负责人")
    if "production_owner_id" in data and data["production_owner_id"] is not None:
        await _assert_active_owner(session, data["production_owner_id"], "制作负责人")
    if "contact_id" in data:
        await _assert_contact_belongs(session, data["contact_id"], sample.customer_id)

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
        opportunity_id=sample.opportunity_id, exclude_user_id=user.id, event_key=f"sample:approve:{sample.id}:r{sample.review_round}",
    )
    await session.commit()
    await notification_service.dispatch_pending(session)
    return ok(after, "样品已批准" if payload.approved else "样品已拒绝")


@router.post("/samples/{sample_id}/resubmit")
async def resubmit_sample(
    sample_id: int,
    request: Request,
    payload: SampleResubmit | None = None,
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
    request_key = payload.request_key if payload else None
    if (request_key and sample.status == "pending"
            and sample.review_request_key == request_key):
        # **同一次提交的弱网重试**（第一批返修 §3.2）：幂等返回，不再加一轮、不重复通知。
        # 只有**带了同一个键**才认幂等——不带键（或换了一把键）时行为完全不变，
        # 下面 ensure_transition 照旧拦住"待审批的单子又来重提"。
        # 用状态本身当幂等信号是错的，那会把这条既有口径悄悄改掉。
        return ok(await svc.detail(session, sample), "已重新提交，等待审批")
    # 状态规则的唯一出处是 SAMPLE_TRANSITIONS，这里不另写一套判断。
    # 非"已驳回"的单子调到这儿会被它拦住（待审批/已批准不支持这个动作）。
    svc.ensure_transition(sample.status, "pending")
    before = await svc.detail(session, sample)

    # 走 _reopen：轮次自增与状态回流是同一件事，别在这里再写一遍
    _reopen(sample)
    # _reopen 会把键清空（新的一轮），这里再登记本次提交的键
    sample.review_request_key = request_key
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
        event_key=f"sample:resubmit:{sample.id}:r{sample.review_round}",
    )
    await session.commit()
    await notification_service.dispatch_pending(session)
    return ok(after, "已重新提交，等待审批")


@router.post("/samples/{sample_id}/revise")
async def revise_sample(
    sample_id: int,
    request: Request,
    payload: SampleRevise | None = None,
    user: CurrentUser = Depends(require_permission("sample:manage")),
    session: AsyncSession = Depends(get_db),
):
    """开新修订版（第一批返修 §3.3，口径已确认 A：原单出 V2、旧版冻结只读）。

    什么时候用它：车间依据（材质 / 工艺 / 图纸版本 / 目标完成日 / 验收标准 / 数量）
    要在**已制作 / 已寄出之后**改。那时代替"原地改"，这里复制出新的一版：

    - 复制单头与明细（含车间依据）作为起点，`version = 旧版 + 1`、`parent_id = 旧版`；
    - **不继承**制作 / 寄送 / 签收 / 客户确认与审批结论——那些是旧版身上的**既成事实**，
      新版本还没做出来，继承过来就是伪造；
    - 新版本从「待审批」开始，走一遍正常审批；
    - 旧版自此**冻结只读**（有子版本即冻结），它当时的资料与事实随时可回看对账。

    没有制作/寄出事实时不给开：那种情况直接改就行（改完自动退回重审），
    多开一版只会让台账变脏。要开就得先有"必须冻结"的事实。
    """
    parent = await svc.get_visible_or_404(session, user, sample_id, for_update=True)
    if parent.made_at is None and parent.status not in ("shipped", "signed"):
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            "该打样单还没有制作/寄出事实，直接改就行（改完会自动退回重审），"
            "不必开修订版",
            422,
        )

    parent_items = (
        await session.execute(
            select(SampleItem).where(SampleItem.sample_request_id == parent.id)
        )
    ).scalars().all()

    child = SampleRequest(
        opportunity_id=parent.opportunity_id,
        customer_id=parent.customer_id,
        contact_id=parent.contact_id,
        owner_id=parent.owner_id,
        # 新版本从待审批开始：它的资料还没被主管批过
        status="pending",
        remark=(payload.remark if payload and payload.remark else parent.remark),
        source_context=parent.source_context,
        version=(parent.version or 1) + 1,
        parent_id=parent.id,
        purpose=parent.purpose,
        target_completion_date=parent.target_completion_date,
        acceptance_criteria=parent.acceptance_criteria,
        sample_fee=parent.sample_fee,
        production_owner_id=parent.production_owner_id,
        created_by=user.id,
        requested_at=svc.now(),
    )
    session.add(child)
    await session.flush()
    for item in parent_items:
        session.add(
            SampleItem(
                sample_request_id=child.id,
                sku_id=item.sku_id,
                inquiry_id=item.inquiry_id,
                inquiry_no_snapshot=item.inquiry_no_snapshot,
                item_name=item.item_name,
                source_snapshot=item.source_snapshot,
                original_quantity=item.original_quantity,
                specification=item.specification,
                craft=item.craft,
                material=item.material,
                drawing_version=item.drawing_version,
                quantity=item.quantity,
                remark=item.remark,
            )
        )
    await session.flush()
    after = await svc.detail(session, child)

    await write_audit(
        session,
        operator_id=user.id,
        action="revise",
        business_type="sample",
        business_id=child.id,
        before={"parent_id": parent.id, "version": parent.version or 1},
        after={"id": child.id, "version": child.version, "parent_id": parent.id},
        ip=client_ip(request),
    )
    await followup_service.record_and_notify(
        session, customer_id=child.customer_id, owner_id=child.owner_id,
        operator_id=user.id,
        title="打样单开了新修订版",
        content=f"打样 #{parent.id}（第 {parent.version or 1} 版）已开第 "
                f"{child.version} 版待审批；原版制作/寄送事实冻结保留",
        business_type="sample", business_id=child.id, sample_id=child.id,
        opportunity_id=child.opportunity_id, exclude_user_id=user.id,
        event_key=f"sample:revise:{parent.id}:v{child.version}",
    )
    await session.commit()
    await notification_service.dispatch_pending(session)
    return ok(after, f"已开第 {child.version} 版（原版冻结保留，等审批）")


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

    # 日期口径与校验（审查 C3-04）：北京日历日 → UTC 瞬时；不能晚于今天
    _assert_not_future(payload.shipped_at, "寄出日期")
    shipped_at = _parse_business_date(payload.shipped_at) if payload.shipped_at else svc.now()
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

    # 签收不能早于寄出（审查 C3-04）；只比前后顺序，保留历史补录能力
    _assert_not_future(payload.signed_at, "签收日期")
    signed_at = _parse_business_date(payload.signed_at) if payload.signed_at else svc.now()
    _assert_not_before(signed_at, sample.shipped_at, "签收日期", "寄出日期")
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
    # **幂等靠结构化事件的唯一键，不靠备注文本**（第一批返修 §3.4）。
    # 旧写法是 `note in sample.remark` —— 整段备注的子串匹配：新说明只要恰好是
    # 旧说明的子串（"已制作完成，等待寄出" → "已制作"）就被判成"已经写过"而被吞掉，
    # 界面还回"没有变化"；而且备注是给人看的展示字段，不该承担幂等判定。
    # 现在：带 request_key 就用它；不带则按 (完成时间, 说明) **精确**算一个键。
    # 说明只要不同就是一次**新事件**，照常追加、照常通知。
    made_at = payload.made_at or sample.made_at or datetime.now(UTC)
    note = (payload.remark or "").strip()
    event_key = payload.request_key or _fingerprint(made_at.isoformat(), note)
    events = list(sample.made_events or [])
    if any((item or {}).get("key") == event_key for item in events):
        # 重发同一个请求（弱网下客户端重试很常见）：不重复留痕、不重复通知。
        return ok(await svc.detail(session, sample), "制作完成记录没有变化")
    events.append(
        {
            "key": event_key,
            "at": made_at.isoformat(),
            "note": note or None,
            "by": user.id,
            "recorded_at": datetime.now(UTC).isoformat(),
        }
    )
    sample.made_events = events
    sample.made_at = made_at
    # ---- 制作依据快照（2026-10-06）----
    # 显式指定"这次照哪几份文件做的"。校验三点：
    #   ① 文件确实挂在这张打样单上——否则等于拿别处的文件来背书这一单；
    #   ② 文件本身存在；
    #   ③ 之前没登记过（登记即固化，要换开修订版）。
    # 快照里存 sha256：光有文件名证明不了是哪一份，文件可以被同名替换。
    basis_ids = [int(fid) for fid in (payload.basis_file_ids or [])]
    if basis_ids:
        if sample.basis_files:
            raise AppError(
                ErrorCode.STATUS_NOT_ALLOWED,
                "这张打样单已经登记过制作依据，不能改。要换依据请开修订版——"
                "新版用新依据，旧版的凭证原样留着，这样每批样按什么做的都查得到",
                422,
            )
        attached = {
            link.file_id: link.category
            for link in (
                await session.execute(
                    select(BusinessFile).where(
                        BusinessFile.business_type == "sample",
                        BusinessFile.business_id == sample.id,
                        BusinessFile.file_id.in_(basis_ids),
                    )
                )
            ).scalars().all()
        }
        missing = [fid for fid in basis_ids if fid not in attached]
        if missing:
            raise AppError(
                ErrorCode.PARAM_ERROR,
                f"这些文件没有挂在这张打样单上（不能拿别处的文件当依据）：{missing}",
                422,
            )
        # ⚠️ 逐个**锁住文件行并重读**（2026-10-08 复审 11.2）：上面那句"挂在这张单上吗"
        # 是普通查询，完全可能读到一份**正在被删**的文件（删除事务尚未提交）。
        # 不锁不重读，就会把一份马上要消失的文件记成"制作依据"——而依据是要长期留证的。
        # 按 id 升序取锁：与别处"一次锁多行"的入口统一锁序，避免互相等成环。
        from app.modules.file import service as file_service

        records: dict[int, FileRecord] = {}
        for fid in sorted(basis_ids):
            rec = await file_service.lock_file_row(session, fid)
            if rec is None:
                raise AppError(
                    ErrorCode.NOT_FOUND,
                    f"制作依据里有文件已经不存在（id={fid}），请重新选择附件",
                    404,
                )
            records[fid] = rec
        sample.basis_files = [
            {
                "file_id": fid,
                "file_name": records[fid].file_name if fid in records else None,
                # 校验值才是"就是这一份"的凭据；文件名可以被同名替换
                "checksum": records[fid].checksum if fid in records else None,
                "size": records[fid].size if fid in records else None,
                "category": attached[fid],
                # 记下这是哪一版打样单的依据（V1 的依据不能拿来背书 V2）
                "sample_version": sample.version or 1,
                "designated_at": datetime.now(UTC).isoformat(),
                "designated_by": user.id,
            }
            for fid in basis_ids
        ]
    elif sample.basis_files is None:
        # **明确没选，也要留痕**（R06，2026-10-06 修）。此前不选就什么都不写，
        # 结果"登记的人明确没选依据"和"功能上线前的老单"在库里长得一模一样
        # （都是 NULL），界面分不出来，只能含糊成"登记时未指定"——
        # 既冤枉了老数据（当时根本没得选），也抹掉了登记人做过的那个判断。
        # 写一个空列表把这次判断记下来：[] 与 NULL 是两件事。
        sample.basis_files = []
    if note:
        # 备注只负责展示：拼成可读文本。**删掉它也不影响上面的幂等判断**。
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
        opportunity_id=sample.opportunity_id, exclude_user_id=user.id, event_key=f"sample:made:{sample.id}:{event_key}",
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
    # 客户确认不能早于签收（审查 C3-04）
    confirmed_at = (
        _parse_business_date(payload.confirmed_at) if payload.confirmed_at else datetime.now(UTC)
    )
    if payload.confirmed_at is not None:
        _assert_not_future(payload.confirmed_at, "客户确认时间")
    _assert_not_before(confirmed_at, sample.signed_at, "客户确认时间", "签收时间")
    sample.confirm_status = decision
    sample.customer_confirmed_at = confirmed_at
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


def _parse_business_date(value) -> datetime:
    """把"用户填的日期/时间"统一换算成 UTC 瞬时。

    ⚠️ 这里修两件事（审查 C3-04）：

    1. **口径**：用户填的是**北京日历日**。原写法
       `datetime.combine(d, min.time(), tzinfo=UTC)` 把它当成 UTC 零点，
       存进去的时刻比实际**早 8 小时**（北京 10-09 00:00 存成了 UTC 10-09 00:00
       = 北京 10-09 08:00）。项目已有 `timebase.business_day_start`，
       注释写明口径是"年/月/今天这些业务口径一律先换算到北京时间再看"，改用它。
    2. **datetime 入参**（`confirmed_at`）也走同一套，naive 视为北京时间。

    历史数据保持原样不动：只改"以后新填的"，不迁移已落库的（在途单据不该被改）。
    """
    if isinstance(value, datetime):
        # naive datetime 按**项目既有口径视为 UTC**（与 `timebase.to_business` 一致），
        # 不能当北京时间 —— 两处口径不一致会差 8 小时，是最难查的一类 bug。
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    # date（用户填的日历日）→ 该北京日的 00:00 对应 UTC 瞬时
    return business_day_start(value.year, value.month, value.day)


def _business_date(value):
    """把 date / datetime 统一取成"北京日历日"，供比较用。

    ⚠️ 必须统一：(`signed_at` 是 date，`confirmed_at` 是 datetime)。
    我第一版直接 `value > today_business()`，datetime 与 date 比较会抛
    `TypeError: can't compare datetime.datetime to datetime.date` —— 实测 500。
    用户填的是哪一天，就按"北京那一天的日历日"比，不看具体时刻。
    """
    if isinstance(value, datetime):
        # ⚠️ 用 `to_business` 而不是 `.date()`：项目口径是**naive datetime 视为 UTC**
        # （见 `timebase.to_business` 的 `replace(tzinfo=UTC)`）。naive 直接取 .date()
        # 会把"UTC 的 23:59"当成北京同一天，实际那是北京的次日 07:59。
        return to_business(value).date()
    return value


def _assert_not_future(value, field: str) -> None:
    """业务日期不能晚于**北京时间今天**（审查 C3-04 主人的口径：A+B）。

    为什么要按北京时间：服务器跑在 UTC，比北京晚 8 小时。北京时间凌晨 0-8 点
    提交"今天"，按 UTC 比会被判成未来、把正常操作拒掉。
    """
    # ⚠️ 括号不能省：写 `if value is None or x > y: raise` 会把 **None 也拒掉**
    # （or 左边为真就直接 raise）—— 我第一版就是这么写的，实测"不填日期"被拒。
    if value is not None and _business_date(value) > today_business():
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"{field}不能晚于今天（{today_business().isoformat()}）",
            422,
        )


def _assert_not_before(earlier, later, early_label: str, late_label: str) -> None:
    """`early_label` 不能早于 `late_label`。

    **保留历史补录能力**：只比前后顺序，不比"是不是今天"——
    寄出 8/20、签收 9/1 这种全是过去的补录照常放行（主人明确要求）。
    """
    if earlier is None or later is None:
        return
    e, l = _business_date(earlier), _business_date(later)
    if e < l:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"{early_label}（{e.isoformat()}）不能早于{late_label}（{l.isoformat()}）",
            422,
        )


async def _assert_active_owner(session: AsyncSession, owner_id: int | None, field: str) -> None:
    """负责人必须**存在且在岗**（审查 C3-02）。

    实测过的后果：传不存在的 id → **500**（外键炸在 flush 时）；传已停用的人 → 200 落库，
    于是这张单挂在一个登不上系统的人名下。两者都该是明确的 4xx。

    与 `task._active_owner`、`customer` 的负责人校验同一口径。
    """
    if owner_id is None:
        raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, f"{field}不能为空", 422)
    target = await session.get(User, owner_id)
    if target is None:
        raise AppError(ErrorCode.NOT_FOUND, f"{field} id={owner_id} 不存在", 404)
    if target.status != "active":
        raise AppError(ErrorCode.PARAM_ERROR, f"{field}「{target.name}」已停用", 422)


async def _assert_contact_belongs(session: AsyncSession, contact_id: int | None, customer_id: int | None) -> None:
    """联系人必须**未被删除、存在、且属于本申请的客户**（审查 C3-02）。

    实测过的两条反例：
      1. 给甲客户的单挂乙客户的联系人 → 200 并落库，打样通知会发给**别人家**的联系人；
      2. **已删除**的联系人 → 读它自己返回 404，但拿它的 id 建/改打样单却 200 并落库
         （补修，漏在 `deleted_at` 上）。

    ⚠️ 为什么第 2 条特别要防：联系人是**软删除**（`deleted_at` 置时间戳，
    `delete_contact` 就是这么做的），而 `session.get()` **不走软删除过滤** ——
    列表、详情那些走 `Contact.deleted_at.is_(None)` 的地方看不到它，
    只有"按 id 直取"的这条校验看得到。于是形成一个**只有内行才知道的入口**：
    界面上选不到这个联系人，但拿 id 就能挂上去，打样通知就会发给一个
    "已经被删掉、本不该再联系"的人。

    历史关联**保持原样不迁移**：以前挂上的联系人即使后来被删，那张单的联系人
    仍然显示（那是当时的事实），这里只拦**新的**关联。
    """
    if contact_id is None:
        return
    contact = await session.get(Contact, contact_id)
    if contact is None:
        raise AppError(ErrorCode.NOT_FOUND, f"联系人 id={contact_id} 不存在", 404)
    if contact.deleted_at is not None:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"联系人「{contact.name}」已被删除，不能再关联到打样单；请从该客户的有效联系人里选",
            422,
        )
    if contact.customer_id != customer_id:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"联系人「{contact.name}」不属于该客户（申请客户 id={customer_id}）",
            422,
        )


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
