"""钉钉 OA 审批接口（文档 §11.3 :152 / 场景11）。

发起与回收都在这里收口；**模板与字段映射读设置项 `dingtalk_oa`**，
所以换模板不用改代码——这正是"业务定了走钉钉"之后还需要的那一步。
"""

from datetime import UTC, datetime
import json
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, PositiveInt
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.config import settings as app_settings
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, get_current_user, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok
from app.modules.dingtalk import service as svc
from app.modules.oa import client as oa_client
from app.modules.oa import inquiry as oa_inquiry
from app.modules.dingtalk.model import OaInstance, allowed_actions

router = APIRouter(tags=["DingTalk"])


def _legacy_contract_readonly(row) -> bool:
    """历史钉钉合同审批行：只读留痕（2026-11-25 主人拍板）。

    旧钉钉合同通道已删除，这些行只剩查看价值；认领/重发/作废/撤回一律不允许，
    且不依赖 DINGTALK_PUSH_OFF 总闸——总闸关只是其中一层，不能当永久保证。
    """
    return getattr(row, "oa_type", None) == "contract" and getattr(row, "provider", "dingtalk") != "self_oa"

#: CRM 侧字段 → 取哪条需求的哪个属性。放在这里而不是散在业务代码里，
#: 是因为"预填什么"是业务口径，改它不该动流程代码。
INQUIRY_FIELDS = {
    "inquiry_no": lambda q: q.inquiry_no,
    "title": lambda q: q.title,
    "description": lambda q: q.description,
    "quantity": lambda q: str(q.quantity) if q.quantity is not None else None,
    "target_price": lambda q: str(q.target_price) if q.target_price is not None else None,
    "version": lambda q: str(q.version),
}


# 公司询价发起页字段（含选填）；后续生产节点不让销售代填。
SUBMISSION_FIELD_LABELS = {
    "报价员负责范围", "期望回复时间", "是否提供样品",
    "客户名称、联系人、联系方式", "产品型号及规格", "大货交付时间", "特别说明", "附件",
}


async def _submission_fields(cfg: dict, *, include_all: bool = False) -> list[dict]:
    from app.modules.dingtalk.client import get_client
    if not cfg.get("process_code") or app_settings.dingtalk_push_off:
        return []
    schema = await get_client().get_process_schema(cfg["process_code"])
    content = schema.get("schemaContent") or {}
    if isinstance(content, str):
        content = json.loads(content)
    fields = []
    for item in content.get("items", []):
        props = item.get("props") or {}
        if (not include_all and props.get("label") not in SUBMISSION_FIELD_LABELS) or not props.get("id"):
            continue
        kind = item.get("componentName")
        # 这张“产品询价申请”模板同时承载后续打样、备货节点，完整 schema
        # 里会出现 SeqNumberField、TableField 等后续节点控件。询价发起阶段
        # 不会填写这些控件，不能因为它们存在就把整个模板判定为已变化。
        if kind not in ("TextField", "TextareaField", "DDDateField", "DDSelectField", "DDAttachment",
                        "DDPhotoField", "NumberField", "InnerContactField", "DepartmentField"):
            continue
        options = []
        for option in props.get("options", []):
            parsed = json.loads(option) if isinstance(option, str) and option.startswith("{") else option
            value = parsed.get("value") if isinstance(parsed, dict) else parsed
            if value is not None:
                options.append(str(value))
        fields.append({"id": props["id"], "label": props["label"], "type": kind,
                       "required": bool(props.get("required")), "options": options})
    return fields


class StartApproval(BaseModel):
    """发起询价审批。`resubmit=True` 用于**驳回后重提**（建新一轮，见 service 注释）。"""

    resubmit: bool = False
    quote_owner_user_id: str | None = Field(default=None, min_length=1, max_length=64)
    extra_fields: dict[str, str] | None = None
    attachment_file_ids: list[PositiveInt] = Field(default_factory=list, max_length=10)


#: 发起钉钉询价前，CRM 侧必须已经有的两项。生产部就是拿这两项去报价的。
#: 为什么放在后端（2026-10-16 补）：它们原先**只在前端拦**
#: （`KnowledgePage` 保存前校验），而接口接受为空并照发 —— 于是任何直接调接口的
#: 路径、或"先存草稿 → 清空字段 → 再提交"的人，都会让生产部收到一张没有需求说明、
#: 没有数量的询价单。文档口径是「提交前检查审批必填项」，检查不能只长在界面上。
#:
#: ⚠️ 只用于**提交**，不用于保存草稿：草稿允许暂缺是同一份文档的明确要求。
SUBMIT_REQUIRED_FIELDS = (("材质及工艺要求", "description"), ("预估订购数量", "quantity"))


def missing_submit_fields(inquiry) -> list[str]:
    """提交前缺哪些必填项（返回中文名列表，空列表表示齐了）。

    `None`、空串、纯空白都算"没填"——前端清空一个字段存下来的就是空串。
    """
    missing: list[str] = []
    for label, attr in SUBMIT_REQUIRED_FIELDS:
        value = getattr(inquiry, attr, None)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(label)
    if getattr(inquiry, "customer_id", None) is None and getattr(inquiry, "opportunity_id", None) is None:
        missing.append("客户或商机关联")
    return missing


@router.post("/inquiries/{inquiry_id}/oa-approval")
async def start_inquiry_approval(
    inquiry_id: int,
    payload: StartApproval,
    request: Request,
    user: CurrentUser = Depends(require_permission("quote:manage")),
    session: AsyncSession = Depends(get_db),
):
    """从 CRM 的定制需求发起钉钉询价审批（幂等；驳回后重提走 resubmit）。"""
    oa_inquiry.ensure_submission_enabled()

    # 复用 inquiry 模块的可见性检查（P1 修复）：直接 `session.get` 按 id 取会**绕过数据范围**
    # ——业务员能对别人的定制需求发起审批。同一个文件里就有现成的
    # `get_visible_or_404`，没有理由另写一套（另写必然和列表页的判据分叉）。
    from app.modules.inquiry import service as inquiry_service

    inquiry = await inquiry_service.get_visible_or_404(session, user, inquiry_id)
    oa_inquiry.require_submittable(inquiry)

    # 提交前的必填项检查（2026-10-16 补，后端侧）：判据见 `missing_submit_fields`。
    # 只拦**提交**，不拦保存草稿：草稿本来就允许暂缺（同一份文档的明确要求）。
    missing_required = missing_submit_fields(inquiry)
    if missing_required:
        raise AppError(
            ErrorCode.REQUIRED_FIELD_MISSING,
            f"提交询价审批前请先补齐：{'、'.join(missing_required)}。"
            "生产部要按这两项报价，缺了报不出来；也可以先保存草稿。",
            422,
        )

    if app_settings.oa_inquiry_template_id or app_settings.oa_inquiry_template_code:
        if not oa_client.configured() or not oa_inquiry.uses_native_inquiry():
            raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "请先连接同序已发布的产品询价模板，可先保存草稿", 422)
        row, message = await oa_inquiry.start_inquiry_approval(session, user, inquiry, payload)
        await write_audit(
            session, operator_id=user.id, action="update", business_type="custom_inquiry",
            business_id=inquiry.id, after={"oa_provider": "self_oa", "oa_status": row.status,
                                           "instance_id": row.instance_id}, ip=client_ip(request),
        )
        await session.commit()
        return ok(svc.serialize(row), message)
    raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "尚未配置同序 OA 询价审批，可先保存草稿", 422)


class ResolveOa(BaseModel):
    """人工处理"结果不明"的发起：认领 / 重发 / 作废。

    `request_key` 是**同一次核定的请求键**（前端每次打开对话框生成一个 uuid）。
    带上它就能保证"同一次核定重发只生效一次并回放同一份结果"；
    不带则由服务端按（记录 + 动作）派生一个稳定键——仍然幂等，
    只是"同一个动作再来一次"也会命中回放。键不能跨记录复用（数据库唯一索引会挡）。
    """

    action: str
    instance_id: str | None = None
    note: str | None = None
    request_key: str | None = None


#: 人工核定在幂等表里的动作名（`request_keys` 按 (用户, 动作, 请求键) 分区）
RESOLVE_ACTION = "oa:resolve"

#: 允许人工核定的状态：结果未知、明确失败、确定没发出。
#: 共同点是"外部**没有**一张正在正常走的单"，所以人都可以介入；
#: pending / approved 不在其中——那两种状态下钉钉有一张活单，人工只该去查询。
RESOLVABLE_STATUSES = svc.RESOLVABLE_STATUSES


async def _drop_stale_reservation(
    session: AsyncSession, *, user_id: int, key: str, row: OaInstance
) -> bool:
    """清掉"同一个请求键留下的死占位"，让**中途重启可恢复**（第七批 7.8）。

    为什么需要它：`request_keys` 的 `in_flight` 占位没有超时机制。进程如果在
    "占请求键"和"写完结果"之间被杀，这条占位会永久挡住同一把键——
    而 7.8 要求中途重启后还能恢复，否则用户只能换个键，等于把幂等当摆设。

    判据用 `oa_instances` 上的占用：真正在处理的请求一定同时持有
    `resolve_state='processing'` 且未僵死。只要这一行**没有**在被有效占用，
    同键的 `in_flight` 就一定是死占位。清掉它不会让两个请求同时调外部——
    真正的互斥在行级 CAS 上，清完之后仍要抢占用，抢不到照样 409。
    """
    from app.core.idempotency import RequestKey

    if row.resolve_state == "processing" and not svc.is_claim_stale(row):
        return False
    stale = (
        await session.execute(
            select(RequestKey).where(
                RequestKey.user_id == user_id,
                RequestKey.action == RESOLVE_ACTION,
                RequestKey.request_key == key,
                RequestKey.status == "in_flight",
            )
        )
    ).scalars().first()
    if stale is None:
        return False
    await session.delete(stale)
    await session.commit()
    return True


async def ensure_oa_type_scope(session, user, row: OaInstance) -> None:
    """按 `oa_type` 用**该业务模块自己的**可见性判据守这条 OA 记录。

    三个类型各走各的口径，不新写规则：
      · `inquiry`  → `inquiry.service.get_visible_or_404`（需求的数据范围）
      · `sample`   → `sample.service.get_visible_or_404(allow_external_flow=True)`
                     （打样单已提交钉钉后会被冻结，不放开这个开关，发单的人自己都动不了）
      · `contract` → `contract.visibility` 的归属判据（客户负责人→订单负责人→创建人）

    未知类型**不放行**：新增 oa_type 时必须同时在这里给出判据，默认拒绝。
    """
    from app.core.deps import ensure_permission
    permission = {'inquiry': 'quote:manage', 'sample': 'sample:manage', 'contract': 'order:manage'}.get(row.oa_type)
    if permission:
        ensure_permission(user, permission)
    if row.oa_type == "inquiry":
        from app.modules.inquiry import service as inquiry_service

        await inquiry_service.get_visible_or_404(session, user, row.inquiry_id)
        return
    if row.oa_type == "sample":
        from app.modules.sample import service as sample_service

        await sample_service.get_visible_or_404(
            session, user, row.inquiry_id, allow_external_flow=True
        )
        return
    if row.oa_type == "contract":
        from app.core.data_scope import ensure_in_scope
        from app.modules.contract.model import ContractDocument
        from app.modules.contract.visibility import contract_scope_owner_id

        doc = await session.get(ContractDocument, row.inquiry_id)
        if doc is None or doc.deleted_at is not None:
            raise AppError(ErrorCode.NOT_FOUND, "合同文档不存在", 404)
        await ensure_in_scope(
            session, user,
            owner_id=await contract_scope_owner_id(session, doc), label="合同文档",
        )
        return
    raise AppError(ErrorCode.NOT_FOUND, "这条审批记录的业务类型无法处理", 404)


@router.post("/oa-instances/{oa_id}/resolve")
async def resolve_oa_instance(
    oa_id: int,
    payload: ResolveOa,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """人工处理"结果不明"的钉钉发起（口径 2026-10-04：不自动重发，转人工）。

    发起过程中断时钉钉那边可能已经建单，而接口没有幂等键——所以不自动重发，
    由人先去钉钉核对，再选择：
      - `adopt`：钉钉已建单 → 填实例号接过来（**先核实模板/发起人/来源需求**）；
      - `resend`：确认没建 → 复用同一轮重新发起；
      - `abandon`：确认不发了 → 作废本轮。

    7.8：整段由"请求键 + 行级原子占用"保护。两个并发核定只有一个能碰外部，
    另一个拿到明确的 409；同一个请求键重放则回放同一份结果。
    """
    from app.core.idempotency import complete, release, request_key_from, reserve
    from app.modules.dingtalk.model import OA_ACTION_LABEL, OA_STATUS_LABEL

    row = await session.get(OaInstance, oa_id)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "审批记录不存在", 404)
    # 数据范围**跟着业务对象走**（2026-10-16 放开到三种类型）：
    #   · 询价 → 需求（quote:view 那套可见性）
    #   · 打样 → 打样单（允许已提交钉钉的单据，否则自己发的单自己动不了）
    #   · 合同 → 合同文档（客户负责人 → 订单负责人 → 创建人）
    #
    # 这里原来写死"非 inquiry 直接 404"，于是合同与打样一旦落在「结果待人工核对」
    # 就**没有任何出口**：不能认领钉钉上可能已建出的那张单、不能重发、不能作废本轮
    # （而三动作正是共享状态机为这个状态定义的唯一出路）。回退的作法是作废整份单据
    # 重新发起——那恰恰是状态机要防的重复建单。
    #
    # ⚠️ 各分支调用的都是**该模块自己的**可见性判据，不是新写一套：
    # 少一处就等于给这些单据开了个跨人越权的后门。
    await ensure_oa_type_scope(session, user, row)
    if _legacy_contract_readonly(row):
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "历史钉钉合同审批仅供查看，新合同审批请使用同序 OA", 409)
    if app_settings.dingtalk_push_off and getattr(row, "provider", "dingtalk") != "self_oa":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "历史审批仅供查看，新审批请使用同序 OA", 409)

    if getattr(row, "provider", None) == "self_oa" and payload.action == "resend":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "请先到同序 OA 核对原发起结果", 409)
    if getattr(row, "provider", None) == "self_oa" and payload.action == "adopt":
        from app.modules.oa import service as own

        if row.instance_id and row.instance_id == (payload.instance_id or "").strip():
            return ok(svc.serialize(row), "已关联同序 OA 审批")
        if row.status != "needs_review":
            raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "只有结果待核对的同序审批可以认领", 409)
        summary = await own.find_receipt_for_adoption(row, payload.instance_id)
        row = await svc.lock_oa_business_then_instance(session, row)
        if row is None:
            raise AppError(ErrorCode.VERSION_CONFLICT, "审批记录正在处理，请稍后核对", 409)
        await ensure_oa_type_scope(session, user, row)
        if row.instance_id == str(summary["id"]):
            return ok(svc.serialize(row), "已关联同序 OA 审批")
        if row.status != "needs_review" or row.idempotency_key != summary["external_ref"]:
            raise AppError(ErrorCode.VERSION_CONFLICT, "审批记录已更新，请刷新后核对", 409)
        existing = await session.scalar(select(OaInstance.id).where(
            OaInstance.instance_id == str(summary["id"]), OaInstance.id != row.id,
        ))
        if existing is not None:
            raise AppError(ErrorCode.VERSION_CONFLICT, "该同序 OA 审批已关联其他记录", 409)
        row.instance_id = str(summary["id"])
        await own.apply_adopted_receipt(session, row, summary)
        await write_audit(session, operator_id=user.id, action="resolve_oa_adopt", business_type="oa_instance",
                          business_id=row.id, after={"status": row.status, "instance_id": row.instance_id},
                          ip=client_ip(request))
        await session.commit()
        return ok(svc.serialize(row), "已关联同序 OA 审批")

    # **同键回放排在状态闸门之前**（8.15 的约定：同键同内容必须回放原结果）。
    # 反过来写的话，`resend` 成功之后状态已经推进到"审批中"，客户端弱网重发
    # 同一把键会被状态闸门挡成 422「当前是「审批中」」——外部调用确实只有一次，
    # 但用户看到"状态不允许"，会以为操作失败，转头去点别的按钮。
    key = request_key_from(request, payload.request_key)
    reservation = None
    if key:
        # 先清掉同一把键留下的死占位（上一次进程被杀时留下的），否则这张单会
        # 永远报"正在处理中"，而实际上根本没有人在处理
        await _drop_stale_reservation(session, user_id=user.id, key=key, row=row)
        reservation = await reserve(
            session,
            user_id=user.id,
            action=RESOLVE_ACTION,
            request_key=key,
            payload={
                "oa_id": oa_id,
                "action": payload.action,
                "instance_id": payload.instance_id,
            },
            result_type="oa_instance",
        )
        if reservation.should_replay:
            # 同一次核定被重发（弱网 / 用户重复点）：回放同一份结果，**不再碰外部**
            return ok(
                reservation.replay_payload, "已处理（同一次核定重放，未再向钉钉发起）"
            )

    # 卡在"发起中"且已经僵死的，先按统一口径转人工——否则这条记录两头堵：
    # 同轮发起直接返回（不会重发），核定又因为状态不符被拒。
    if svc.demote_stuck_submitting(row):
        await session.commit()

    if row.status not in RESOLVABLE_STATUSES:
        allowed = [
            OA_ACTION_LABEL.get(a, a)
            for a in allowed_actions(row.status, row.resolve_state)
            if a.startswith("resolve_")
        ]
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            "只有当发起结果是「结果待人工核对 / 发起失败 / 未发出」时才需要人工核定，"
            f"当前是「{OA_STATUS_LABEL.get(row.status, row.status)}」"
            + (f"；现在可以做的是：{'、'.join(allowed)}" if allowed else ""),
            422,
        )

    try:
        row = await svc.resolve_reviewed_instance(
            session,
            row,
            action=payload.action,
            instance_id=payload.instance_id,
            note=payload.note,
            request_key=key,
        )
    except BaseException:
        # 业务失败要把请求键还回去：用户改完会带着同一个键重试，
        # 占着不放就会把"重试"误判成"正在处理中"。
        # 这里**要提交**：请求异常时 get_db 会 rollback，不提交的话这次释放会被一起回滚，
        # 同键下次仍然撞"正在处理中"（虽然下次会由 _drop_stale_reservation 补救，
        # 但那是兜底，不该当成常规路径）
        if reservation is not None:
            await release(session, reservation)
            await session.commit()
        raise

    await write_audit(
        session,
        operator_id=user.id,
        action=f"resolve_oa_{payload.action}",
        business_type="oa_instance",
        business_id=row.id,
        after={
            "status": row.status,
            "instance_id": row.instance_id,
            "note": payload.note,
            "request_no": row.idempotency_key,
            "resolve_request_key": row.resolve_request_key,
        },
        ip=client_ip(request),
    )
    if reservation is not None:
        await complete(
            session,
            reservation,
            result_payload=svc.serialize(row),
            result_id=row.id,
        )
    await session.commit()
    return ok(svc.serialize(row), "已处理")


@router.post("/oa-instances/{oa_id}/sync")
async def sync_oa_instance(
    oa_id: int,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """向当前审批系统查询单据；不发起、不撤销、不改变审批决定。"""
    row = await session.get(OaInstance, oa_id)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, '审批记录不存在', 404)
    await ensure_oa_type_scope(session, user, row)
    row = await svc.lock_oa_business_then_instance(session, row)
    if row is None:
        raise AppError(ErrorCode.VERSION_CONFLICT, "审批记录正在处理，请稍后同步", 409)
    await ensure_oa_type_scope(session, user, row)
    if _legacy_contract_readonly(row):
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "历史钉钉合同审批仅供查看，新合同审批请使用同序 OA", 422)
    if getattr(row, 'provider', 'dingtalk') == 'self_oa':
        from app.modules.oa import service as oa_service
        # 手动同步必须真查：原来 3 秒内复用快照，用户刚在 OA 批完就点同步，
        # 会拿到旧的 pending，提示却仍是"已查询最新"。并发点击由行锁串行化。
        try:
            current = await oa_service.sync_self_oa_row(session, row)
        except oa_client.OaError as exc:
            raise AppError(
                ErrorCode.EXTERNAL_ERROR,
                '暂时无法确认最新审批结果，请稍后重试；审批尚未确认前，暂不能执行需要审批通过的操作',
                502,
            ) from exc
        if current is None:
            raise AppError(ErrorCode.VERSION_CONFLICT, "审批状态已更新，请刷新后再同步", 409)
        row = current
        await ensure_oa_type_scope(session, user, row)
        # B07-10：手工同步是**人主动去外部系统取结果**的动作，必须留痕——
        # 否则"谁在什么时候把状态从待审批同步成了已通过"在台账里查不到。
        await write_audit(
            session,
            operator_id=getattr(user, "id", None),
            action="sync_oa",
            business_type="oa_instance",
            business_id=row.id,
            after={
                "provider": "self_oa",
                "status": getattr(row, "status", None),
                "instance_id": getattr(row, "instance_id", None),
                "synced_at": row.synced_at.isoformat() if getattr(row, "synced_at", None) else None,
            },
            ip=None,
        )
        await session.commit()
        return ok(oa_service.serialize(row), '已查询 OA 最新记录')
    if app_settings.dingtalk_push_off:
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, '历史审批仅供查看，新审批请使用同序 OA', 422)
    if not row.instance_id:
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, '尚无钉钉审批单，请先核对提交状态', 422)
    # 同一条审批短时间重复点击复用快照；行锁串行化并发请求。
    synced = row.synced_at
    if synced is not None and synced.tzinfo is None:
        synced = synced.replace(tzinfo=UTC)
    if synced is None or (datetime.now(UTC) - synced).total_seconds() >= 3:
        result = await svc.sync_pending_instances(session, oa_id=row.id)
        if not result["checked"]:
            raise AppError(ErrorCode.VERSION_CONFLICT, "审批状态已更新，请刷新后再同步", 409)
        await ensure_oa_type_scope(session, user, row)
        await write_audit(
            session,
            operator_id=getattr(user, "id", None),
            action="sync_oa",
            business_type="oa_instance",
            business_id=row.id,
            after={
                "provider": "dingtalk",
                "status": getattr(row, "status", None),
                "instance_id": getattr(row, "instance_id", None),
                "synced_at": row.synced_at.isoformat() if getattr(row, "synced_at", None) else None,
            },
            ip=None,
        )
        await session.commit()
    if row.error:
        raise AppError(ErrorCode.EXTERNAL_ERROR, '未能取得钉钉最新记录，请稍后重试', 502)
    return ok(svc.serialize(row), '已查询钉钉最新记录')


@router.post("/oa-instances/{oa_id}/withdraw")
async def withdraw_oa_instance(
    oa_id: int,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """撤回仍在钉钉流转中的询价/打样/合同审批。审批完成后不能从 CRM 撤回。

    ⚠️ 权限不再写死 `quote:manage`（2026-10-16）：打样弹窗由只要
    `sample:manage` 的接口打开，按钮却打到这里 → 只有打样权限的人点撤回必然 403，
    而按钮照常显示。改成按 `oa_type` 走各模块自己的可见性判据（与核定入口同一套）。
    """
    from app.core.config import settings as app_settings
    from app.modules.dingtalk.client import get_client, DingTalkError
    from app.modules.user.model import User

    row = await session.get(OaInstance, oa_id)
    if row is None or row.oa_type not in ("inquiry", "sample", "contract"):
        raise AppError(ErrorCode.NOT_FOUND, "询价、打样或合同审批记录不存在", 404)
    await ensure_oa_type_scope(session, user, row)
    row = await svc.lock_oa_business_then_instance(session, row)
    if row is None:
        raise AppError(ErrorCode.VERSION_CONFLICT, "审批记录正在处理，请稍后撤回", 409)
    await ensure_oa_type_scope(session, user, row)
    if _legacy_contract_readonly(row):
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "历史钉钉合同审批仅供查看，新合同审批请使用同序 OA", 422)
    if row.status != "pending" or not row.instance_id:
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, f"当前审批状态为「{row.status}」，不能撤回", 422)
    if getattr(row, "provider", "dingtalk") == "self_oa":
        from app.modules.oa import service as oa_service
        row = await oa_service.withdraw_self_oa_row(session, row, "CRM 撤回 OA 审批")
        await ensure_oa_type_scope(session, user, row)
        await write_audit(
            session, operator_id=user.id, action="withdraw_oa_instance", business_type="oa_instance",
            business_id=row.id, after={"oa_type": row.oa_type, "instance_id": row.instance_id, "status": row.status},
            ip=client_ip(request),
        )
        await session.commit()
        return ok(svc.serialize(row), "已撤回 OA 审批")
    if app_settings.dingtalk_push_off:
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "历史审批仅供查看，新审批请使用同序 OA", 422)
    account = await session.get(User, user.id)
    bound_id = account.dingtalk_userid if account else None

    async def send(instance_id):
        ding_user_id = bound_id or await get_client().search_user_id_by_name(user.name)
        if not ding_user_id:
            raise DingTalkError("找不到当前用户的钉钉身份，无法撤回", api="terminate")
        await get_client().terminate_process_instance(
            instance_id=instance_id,
            operating_user_id=ding_user_id,
            remark="CRM 撤回钉钉审批",
        )
        return ding_user_id

    row, withdrawn_by = await svc.withdraw_oa_without_locks(session, row, send)
    await ensure_oa_type_scope(session, user, row)
    if withdrawn_by:
        row.error = None
        row.status = "withdrawn"
        row.result = {**(row.result or {}), "crm_withdrawn": True, "withdrawn_by": withdrawn_by,
                      "withdraw_snapshot_pending": True}
        row.synced_at = datetime.now(UTC)
        row.updated_at = row.synced_at
    await write_audit(
        session, operator_id=user.id, action="withdraw_oa_instance", business_type="oa_instance",
        business_id=row.id, after={"oa_type": row.oa_type, "instance_id": row.instance_id, "status": row.status},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize(row), "已撤回钉钉审批")


@router.get("/inquiries/{inquiry_id}/oa-approvals")
async def list_inquiry_approvals(
    inquiry_id: int,
    user: CurrentUser = Depends(require_permission("quote:view")),
    session: AsyncSession = Depends(get_db),
):
    """该需求历次提交的审批实例（含被驳回的旧轮次——重提不改写历史）。"""
    # 读也要过范围（P1）：审批实例里可能带着价格与审批意见，不能按 id 直取
    from app.modules.inquiry import service as inquiry_service

    await inquiry_service.get_visible_or_404(session, user, inquiry_id)
    rows = (
        await session.execute(
            select(OaInstance)
            .where(OaInstance.inquiry_id == inquiry_id, OaInstance.oa_type == "inquiry")
            .order_by(OaInstance.submit_round.desc())
        )
    ).scalars().all()
    return ok([svc.serialize(row) for row in rows])


@router.post("/dingtalk/sync-users")
async def sync_dingtalk_users(
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    """把 CRM 账号与钉钉成员对上（写 `users.dingtalk_userid`）——**钉钉登录靠它认人**。

    钉钉只会告诉系统"这是某某的钉钉编号"，它不知道这人在 CRM 里是什么角色。
    这一步就是把两边的人对上，对上了以后才谈得上登录。

    **不做的事**：不建账号（建账号要定角色与数据范围，那是人的决定）。
    钉钉里有人、CRM 里没账号的，只列出来让管理员去建。
    已绑好的不会被改绑 —— 改绑等于换掉一个人的登录身份。

    需要 `settings:manage` 权限：这是管理员动作，
    而且界面就挂在系统设置里。
    """
    result = await svc.sync_users(session)
    if result.get("disabled"):
        # 登录和审批都关闭时，没有读取通讯录或写入绑定。
        # 如实回报，不假装成功——否则界面上会显示"同步完成 0 人"，
        # 让人以为是"钉钉里没人"，实际是一次请求都没发。
        return ok(result, result.get("message"))
    await write_audit(
        session,
        operator_id=user.id,
        action="dingtalk_sync_users",
        business_type="integration",
        after={
            "total": result["total"],
            "bound": result["bound"],
            "already_bound": result["already_bound"],
            "bound_users": result.get("bound_users", []),
            "unmatched": len(result["unmatched"]),
        },
        ip=client_ip(request),
    )
    await session.commit()
    return ok(
        result,
        f"钉钉通讯录 {result['total']} 人：本次绑定 {result['bound']} 人，"
        f"原已绑定 {result['already_bound']} 人，未对上 {len(result['unmatched'])} 人",
    )


@router.post("/dingtalk/align-departments")
async def align_departments(
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    """按同序目录覆盖已有账号的部门；返回逐人变更，管理员在界面核对。"""
    result = await svc.align_departments(session)
    await write_audit(
        session,
        operator_id=user.id,
        action="dingtalk_align_departments",
        business_type="integration",
        after={"source": result["source"], "matched": result["matched"],
               "changed": len(result["changes"])},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(result, "按同序部门对齐："
                      f"匹配 {result['matched']} 人，变更 {len(result['changes'])} 人")


class ProvisionDingTalkUser(BaseModel):
    """从同序身份建 CRM 账号：姓名/部门取通讯录，登录名/密码/角色由管理员定。"""

    dingtalk_userid: str = Field(min_length=1, max_length=128)
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=6, max_length=72)
    role_ids: list[int] = Field(default_factory=list)


async def _oa_directory():
    """读取本企业的同序人员和部门目录。"""
    try:
        data = await oa_client.list_directory()
    except oa_client.OaError as exc:
        raise AppError(ErrorCode.EXTERNAL_ERROR, "无法读取同序人员目录，请检查同序 OA 连接后重试", 502) from exc
    departments = {str(row["id"]): row for row in (data.get("departments") or [])}
    return list(data.get("users") or []), departments


class OaUserBinding(BaseModel):
    oa_user_id: UUID | None


class OaUserCreate(BaseModel):
    oa_user_id: UUID
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=6, max_length=72)
    role_ids: list[int] = Field(default_factory=list)


async def _require_oa_account_admin(user: CurrentUser = Depends(require_permission("user:manage"))) -> CurrentUser:
    if "admin" not in user.roles:
        raise AppError(ErrorCode.FORBIDDEN, "只有系统管理员可以维护同序账号绑定", 403)
    return user


def _oa_member(members: list[dict], identity: str) -> dict:
    member = next((row for row in members if str(row.get("id") or "") == identity), None)
    if member is None:
        raise AppError(ErrorCode.PARAM_ERROR, "同序目录里没有这位在职人员，请刷新后再选择", 422)
    return member


async def _save_oa_binding(session: AsyncSession, account, identity: str | None) -> None:
    from app.modules.user.model import User

    if identity and (await session.execute(select(User.id).where(
        User.oa_user_id == identity, User.id != account.id
    ))).first() is not None:
        raise AppError(ErrorCode.DUPLICATE, "这位同序人员已经绑定了另一个 CRM 账号", 409)
    try:
        async with session.begin_nested():
            account.oa_user_id = identity
            await session.flush()
    except IntegrityError as exc:
        raise AppError(ErrorCode.DUPLICATE, "这位同序人员已经绑定了另一个 CRM 账号", 409) from exc


@router.get("/oa/members")
async def list_oa_members(
    user: CurrentUser = Depends(_require_oa_account_admin),
    session: AsyncSession = Depends(get_db),
):
    from app.modules.user.model import User

    members, _ = await _oa_directory()
    bound = dict((await session.execute(select(User.oa_user_id, User.id).where(
        User.oa_user_id.is_not(None)
    ))).all())
    rows = [{
        "id": str(row["id"]), "name": str(row.get("name") or ""),
        "department": str(row.get("department_name") or ""),
        "employee_number": str(row.get("employee_number") or ""),
        "crm_user_id": bound.get(str(row["id"])),
    } for row in members if row.get("id")]
    return ok(sorted(rows, key=lambda row: (row["department"], row["name"], row["id"])))


@router.patch("/users/{user_id}/oa-binding")
async def set_oa_binding(
    user_id: int, payload: OaUserBinding, request: Request,
    user: CurrentUser = Depends(_require_oa_account_admin),
    session: AsyncSession = Depends(get_db),
):
    from app.modules.user import service as users
    from app.modules.user.model import User

    identity = str(payload.oa_user_id) if payload.oa_user_id else None
    member = None
    departments = {}
    if identity:
        members, departments = await _oa_directory()
        member = _oa_member(members, identity)
    await users.lock_user_row(session, user_id)
    account = await session.get(User, user_id, populate_existing=True)
    if account is None:
        raise AppError(ErrorCode.NOT_FOUND, "CRM 账号不存在", 404)
    old = account.oa_user_id
    old_department_id = account.department_id
    await _save_oa_binding(session, account, identity)
    oa_department_id = str(member.get("department_id") or "") if member else ""
    if oa_department_id:
        department, _ = await svc.ensure_oa_department(session, departments, oa_department_id)
        account.department_id = department.id
    await write_audit(
        session, operator_id=user.id, action="oa_account_binding", business_type="user",
        business_id=user_id, before={"oa_user_id": old, "department_id": old_department_id},
        after={"oa_user_id": identity, "department_id": account.department_id},
        ip=client_ip(request),
    )
    await session.commit()
    return ok({"id": user_id, "oa_user_id": identity, "department_synced": bool(oa_department_id)}, "同序人员绑定已保存")


@router.post("/oa/users")
async def provision_oa_user(
    payload: OaUserCreate, request: Request,
    user: CurrentUser = Depends(_require_oa_account_admin),
    session: AsyncSession = Depends(get_db),
):
    from app.modules.user import service as users

    members, departments = await _oa_directory()
    identity = str(payload.oa_user_id)
    member = _oa_member(members, identity)
    department_id = str(member.get("department_id") or "")
    department = None
    if department_id:
        department, _ = await svc.ensure_oa_department(session, departments, department_id)
    account = await users.create_user(session, {
        "name": str(member.get("name") or "").strip() or payload.username,
        "username": payload.username, "password": payload.password,
        "department_id": department.id if department else None, "role_ids": payload.role_ids,
    })
    await _save_oa_binding(session, account, identity)
    await write_audit(
        session, operator_id=user.id, action="oa_provision_user", business_type="user",
        business_id=account.id, after={"oa_user_id": identity, "department_id": account.department_id,
                                      "role_ids": payload.role_ids},
        ip=client_ip(request),
    )
    await session.commit()
    return ok({"id": account.id, "oa_user_id": identity}, "已按同序人员创建 CRM 账号")


@router.get("/dingtalk/members")
async def list_dingtalk_members(
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    """给「新建用户」用的同序人员列表，只列尚未绑定 CRM 的人员。"""
    from app.modules.user.model import User

    users, _departments = await _oa_directory()
    bound = {
        str(value) for value in (await session.execute(
            select(User.dingtalk_userid).where(User.dingtalk_userid.isnot(None))
        )).scalars().all() if value
    }
    rows = [{
        "userid": row["dingtalk_user_id"],
        "name": str(row.get("name") or ""),
        "department": str(row.get("department_name") or ""),
    } for row in users
        if row.get("dingtalk_user_id") and str(row["dingtalk_user_id"]) not in bound]
    rows.sort(key=lambda row: (row["department"], row["name"]))
    return ok(rows)


@router.post("/dingtalk/provision-user")
async def provision_dingtalk_user(
    payload: ProvisionDingTalkUser,
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    """用同序身份建 CRM 账号：自动带姓名和所在部门，角色由管理员选。"""
    from app.modules.user import service as user_service
    from app.modules.user.model import User

    if (await session.execute(
        select(User.id).where(User.dingtalk_userid == payload.dingtalk_userid)
    )).first() is not None:
        raise AppError(ErrorCode.DUPLICATE, "这个同序身份已经绑定过 CRM 账号", 409)

    users, departments = await _oa_directory()
    target = next((row for row in users
                   if str(row.get("dingtalk_user_id") or "") == payload.dingtalk_userid), None)
    if target is None:
        raise AppError(ErrorCode.PARAM_ERROR, "同序目录里找不到这个身份，请刷新后重试", 422)
    department, _created = await svc.ensure_oa_department(
        session, departments, str(target.get("department_id") or ""))
    name = str(target.get("name") or "").strip()
    created = await user_service.create_user(session, {
        "name": name or payload.username,
        "username": payload.username,
        "password": payload.password,
        "department_id": department.id,
        "dingtalk_userid": payload.dingtalk_userid,
        "role_ids": payload.role_ids,
    })
    await write_audit(
        session,
        operator_id=user.id,
        action="dingtalk_provision_user",
        business_type="user",
        business_id=created.id,
        after={"dingtalk_userid": payload.dingtalk_userid, "department_id": department.id,
               "role_ids": payload.role_ids},
        ip=client_ip(request),
    )
    await session.commit()
    return ok({
        "id": created.id, "name": created.name, "username": created.username,
        "department_id": department.id, "department": department.name,
    }, "已按同序身份创建 CRM 账号")


@router.get("/inquiries/{inquiry_id}/oa-submission")
async def inquiry_submission_options(
    inquiry_id: int,
    user: CurrentUser = Depends(require_permission("quote:manage")),
    session: AsyncSession = Depends(get_db),
):
    """仅准备CRM提交界面，不创建审批、不上传钉钉、不触发通知。"""
    from app.modules.inquiry.service import get_visible_or_404
    inquiry = await get_visible_or_404(session, user, inquiry_id)
    selected_quote_owner = inquiry.oa_quote_user_id
    if app_settings.oa_inquiry_template_id or app_settings.oa_inquiry_template_code:
        selected_quote_owner = oa_inquiry.saved_quote_owner(inquiry)
    return await _submission_options(session, selected_quote_owner, applicant=user, inquiry=inquiry)


@router.get("/inquiries/oa-submission-options")
async def new_inquiry_submission_options(
    user: CurrentUser = Depends(require_permission("quote:manage")),
    session: AsyncSession = Depends(get_db),
):
    """新建表单只读模板；不创建需求、审批或附件。"""
    return await _submission_options(session, applicant=user)


async def _fallback_submission_options(session, applicant, inquiry, message):
    """同序不可用时也要把销售段的字段完整展示出来，不能只剩 CRM 那几格。

    字段顺序照同序《产品询价申请》；提报人/部门先用 CRM 账号资料顶上，
    等 OA 恢复后提交时再以同序身份为准。
    """
    from app.modules.oa.inquiry import _department_name
    from app.modules.user.model import User

    account = await session.get(User, applicant.id) if applicant is not None else None
    department = (await _department_name(session, account)
                  if getattr(account, 'department_id', None) else '')
    return {
        "prefill": {"applicant_name": getattr(account, 'name', '') or '', "department": department},
        "prefill_fields": await oa_inquiry.submission_prefill(session, inquiry),
        "configured": False, "enabled": False, "provider": "self_oa",
        "quote_owner_required": True, "quote_owners": [], "selected_quote_owner": None,
        "image_required": False, "file_business_type": "inquiry",
        "fields": oa_inquiry.fallback_entry_fields(),
        "template_fields": oa_inquiry.SALES_TEMPLATE_FIELDS,
        "message": message,
    }


async def _submission_options(session, selected_quote_owner=None, *, applicant=None, inquiry=None):
    if not app_settings.oa_inquiry_submission_enabled:
        return ok(await _fallback_submission_options(
            session, applicant, inquiry, "询价审批暂未启用，可先保存需求和内部报价草稿"))
    if app_settings.oa_inquiry_template_id or app_settings.oa_inquiry_template_code:
        if not oa_client.configured() or not oa_inquiry.uses_native_inquiry():
            return ok(await _fallback_submission_options(
                session, applicant, inquiry, "同序 OA 对接还没完成，可先保存草稿；请联系管理员"))
        from app.modules.user.model import User
        account = await session.get(User, applicant.id) if applicant is not None else None
        identity = oa_client.applicant_identity(account)
        if not identity:
            return ok(await _fallback_submission_options(
                session, applicant, inquiry,
                "当前账号还没绑定同序 OA 登录身份，可先保存草稿；请联系管理员绑定后再提交"))
        try:
            templates = await oa_client.list_templates(identity)
        except oa_client.OaError as exc:
            if exc.code in {'applicant_not_found', 'applicant_unmapped'}:
                return ok(await _fallback_submission_options(
                    session, applicant, inquiry,
                    "同序 OA 里找不到当前账号或所属部门，可先保存草稿；请联系管理员补齐"))
            raise AppError(ErrorCode.EXTERNAL_ERROR, '无法读取同序询价模板，请稍后重试；仍可保存草稿', 502) from exc
        template = max((t for t in templates if t['code'] == oa_inquiry.NATIVE_INQUIRY_CODE),
                       key=lambda t: t['version'], default=None)
        if not template:
            return ok(await _fallback_submission_options(
                session, applicant, inquiry, "当前账号没有可用的同序产品询价模板，可先保存草稿；请联系管理员"))
        identity = template.get('applicant') or {}
        quote_owners = [{'user_id': p['id'], 'name': p['name']} for p in oa_inquiry.native_quoters(template)]
        enabled = bool(quote_owners and identity.get("id") and identity.get("department_id"))
        return ok({
            "prefill": {"applicant_name": identity.get("name", ""), "department": identity.get("department_name", "")},
            "prefill_fields": await oa_inquiry.submission_prefill(session, inquiry),
            "configured": True,
            "enabled": enabled,
            "provider": "self_oa",
            "quote_owner_required": True,
            "quote_owners": quote_owners,
            "selected_quote_owner": selected_quote_owner if any(p["user_id"] == selected_quote_owner for p in quote_owners) else None,
            "image_required": any(f["id"] == "reference_images" and f.get("required") for f in template["fields"]),
            "file_business_type": "inquiry",
            "fields": oa_inquiry.native_entry_fields(template),
            "template_fields": template["fields"],
            "message": "" if enabled else "同序模板缺少可选的对接报价员或申请人部门，请联系管理员",
        })
    if app_settings.dingtalk_push_off:
        return ok(await _fallback_submission_options(
            session, applicant, inquiry, "尚未配置同序 OA 询价审批，可先保存草稿"))
    from app.modules.settings.service import get_setting
    cfg = await get_setting(session, "dingtalk_oa") or {}
    owners = [{"user_id": str(item["user_id"]), "name": str(item.get("name") or "报价员")}
              for item in cfg.get("quote_owners", []) if isinstance(item, dict) and item.get("user_id")]
    fields = await _submission_fields(cfg)
    prefill = await oa_inquiry.submission_prefill(session, inquiry)
    # 老钉钉模板的控件 id 由模板决定；按 OA 的中文标签把 CRM 已有资料填进去。
    labels = {
        "customer_contact": "客户名称、联系人、联系方式",
        "special_note": "特别说明",
    }
    by_label = {field["label"]: field["id"] for field in fields}
    prefill_fields = {
        by_label[labels[key]]: value
        for key, value in prefill.items()
        if key in labels and labels[key] in by_label
    }
    return ok({
        "configured": bool(cfg.get("process_code")),
        "enabled": not app_settings.dingtalk_push_off,
        "quote_owner_required": bool(cfg.get("quote_owner_component")),
        "quote_owners": owners,
        "selected_quote_owner": selected_quote_owner,
        "image_required": bool(cfg.get("image_component")),
        "file_business_type": cfg.get("inquiry_file_business_type") or "inquiry",
        "fields": fields,
        "prefill_fields": prefill_fields,
    })
