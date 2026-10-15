"""钉钉 OA 审批接口（文档 §11.3 :152 / 场景11）。

发起与回收都在这里收口；**模板与字段映射读设置项 `dingtalk_oa`**，
所以换模板不用改代码——这正是"业务定了走钉钉"之后还需要的那一步。
"""

from datetime import datetime
import json

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.config import settings as app_settings
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok
from app.modules.dingtalk import service as svc
from app.modules.dingtalk.model import OaInstance, allowed_actions

router = APIRouter(tags=["DingTalk"])

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


# 现有公司询价模板发起页的补充字段。只取这五项；后续生产节点字段不让销售代填。
SUBMISSION_FIELD_LABELS = {
    "报价员负责范围", "期望回复时间", "是否提供样品",
    "客户名称、联系人、联系方式", "产品型号及规格",
}


async def _submission_fields(cfg: dict) -> list[dict]:
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
        if props.get("label") not in SUBMISSION_FIELD_LABELS or not props.get("id"):
            continue
        kind = item.get("componentName")
        if kind not in ("TextField", "TextareaField", "DDDateField", "DDSelectField"):
            raise AppError(ErrorCode.PARAM_ERROR, "询价模板控件已变化，请联系管理员核对", 422)
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


@router.post("/inquiries/{inquiry_id}/oa-approval")
async def start_inquiry_approval(
    inquiry_id: int,
    payload: StartApproval,
    request: Request,
    user: CurrentUser = Depends(require_permission("quote:manage")),
    session: AsyncSession = Depends(get_db),
):
    """从 CRM 的定制需求发起钉钉询价审批（幂等；驳回后重提走 resubmit）。"""
    from app.modules.settings import service as settings_service

    # 复用 inquiry 模块的可见性检查（P1 修复）：直接 `session.get` 按 id 取会**绕过数据范围**
    # ——业务员能对别人的定制需求发起审批。同一个文件里就有现成的
    # `get_visible_or_404`，没有理由另写一套（另写必然和列表页的判据分叉）。
    from app.modules.inquiry import service as inquiry_service

    inquiry = await inquiry_service.get_visible_or_404(session, user, inquiry_id)

    cfg = await settings_service.get_setting(session, "dingtalk_oa")
    process_code = (cfg or {}).get("process_code") or ""
    if not process_code:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            "还没配置钉钉审批模板（设置项 dingtalk_oa.process_code）。"
            "业务定了用哪个模板后填上即可，不用改代码",
            422,
        )
    mapping = (cfg or {}).get("field_map") or {}
    field_map = {}
    for crm_field, component_id in mapping.items():
        getter = INQUIRY_FIELDS.get(crm_field)
        if getter is None or not component_id:
            continue
        value = getter(inquiry)
        if value not in (None, ""):
            field_map[component_id] = value
    supplemental_fields = await _submission_fields(cfg or {})
    supplied = payload.extra_fields or {}
    for field in supplemental_fields:
        value = str(supplied.get(field["id"], "")).strip()
        if field["required"] and not value:
            raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, f"请填写{field['label']}", 422)
        if value and field["options"] and value not in field["options"]:
            raise AppError(ErrorCode.PARAM_ERROR, f"{field['label']}选项无效，请重新选择", 422)
        if value and field["type"] == "DDDateField":
            try:
                datetime.strptime(value, "%Y-%m-%d %H:%M")
            except ValueError as error:
                raise AppError(ErrorCode.PARAM_ERROR, "期望回复时间格式无效，请重新选择", 422) from error
    # 业务补充字段不能覆盖发起人、部门、报价员或图片等系统生成字段。
    protected = {v for v in (cfg or {}).get("field_map", {}).values() if v}
    protected.update(v for v in ((cfg or {}).get("originator_component"), (cfg or {}).get("dept_component"),
                                (cfg or {}).get("quote_owner_component"), (cfg or {}).get("image_component")) if v)
    if protected.intersection(supplied):
        raise AppError(ErrorCode.PARAM_ERROR, "补充资料不能覆盖系统自动填写字段", 422)
    # 钉钉那格是必填的**联系人**控件，只能在这几位里选（编号从钉钉通讯录拿的）；
    # 销售在 CRM 里选好的那位直接带过去
    quote_component = (cfg or {}).get("quote_owner_component")
    if quote_component:
        quote_owner = payload.quote_owner_user_id or inquiry.oa_quote_user_id
        if payload.quote_owner_user_id:
            allowed_owners = {str(item.get("user_id")) for item in (cfg or {}).get("quote_owners", []) if isinstance(item, dict) and item.get("user_id")}
            if quote_owner not in allowed_owners:
                raise AppError(ErrorCode.PARAM_ERROR, "请选择当前配置中的对接报价员", 422)
        if not quote_owner:
            raise AppError(
                ErrorCode.REQUIRED_FIELD_MISSING,
                "钉钉要求先指定「对接报价员」，请在需求上选好再发起",
                422,
            )
        field_map[quote_component] = quote_owner
    # 提报人和发起人必须用同一个真实钉钉编号。关闭总闸时仅记待查询字段，
    # 不查通讯录，不用 CRM 主键冒充钉钉身份；今后真发前会重新查询并生成表单。
    from app.modules.dingtalk.client import get_client

    ding_user_id = ""
    if not app_settings.dingtalk_push_off:
        from app.modules.user.model import User
        account = await session.get(User, user.id)
        if account is None or account.status != "active":
            raise AppError(ErrorCode.UNAUTHORIZED, "账号不存在或已停用", 401)
        ding_user_id = account.dingtalk_userid or await get_client().search_user_id_by_name(user.name)
        if not ding_user_id:
            raise AppError(
                ErrorCode.PARAM_ERROR,
                f"钉钉里找不到与「{user.name}」同名的账号，无法发起审批。"
                "请确认你的钉钉姓名与本系统一致，或联系管理员",
                422,
            )
    originator_component = (cfg or {}).get("originator_component")
    if originator_component:
        field_map[originator_component] = ding_user_id or f"（待查询：{user.name}的钉钉编号）"
    dept_component = (cfg or {}).get("dept_component")
    if dept_component:
        if app_settings.dingtalk_push_off:
            field_map[dept_component] = "（待查询：钉钉部门编号）"
        else:
            dept_ids = await get_client().get_user_dept_ids(ding_user_id)
            if dept_ids:
                field_map[dept_component] = dept_ids[0]
    # 额外字段（业务临时补的）按 componentId 直接给
    for component_id, value in (payload.extra_fields or {}).items():
        if component_id and value not in (None, ""):
            field_map[component_id] = value

    # 「产品参考图片」是**必填的图片控件**：图不能直接塞进审批单，
    # 得先上传给钉钉换 media_id——**这一步也是对外的**，所以只在总闸打开时真传；
    # 关着的时候只把"会用哪张图"记下来，等真发的时候再传。
    image_component = (cfg or {}).get("image_component")
    if image_component:
        biz_type = (cfg or {}).get("inquiry_file_business_type") or "inquiry"
        found = await svc.first_image_attachment(
            session, business_type=biz_type, business_id=inquiry.id
        )
        if found is None:
            raise AppError(
                ErrorCode.REQUIRED_FIELD_MISSING,
                "钉钉要求必须上传「产品参考图片」，请先在需求里上传图纸或参考图再发起",
                422,
            )
        filename, content = found
        if app_settings.dingtalk_push_off:
            # 干跑：不传图，只记下会用哪一张（对外零请求）
            field_map[image_component] = f"（待上传：{filename}）"
        else:
            media_id = await get_client().upload_media(
                content=content, filename=filename, media_type="image"
            )
            field_map[image_component] = media_id

    row = await svc.create_inquiry_instance(
        session,
        user=user,
        inquiry_id=inquiry.id,
        inquiry_version=int(inquiry.version or 1),
        customer_id=inquiry.customer_id,
        process_code=process_code,
        # 钉钉 userid，不是 CRM 的 user.id（见上面那段注释）
        originator_user_id=ding_user_id,
        field_map=field_map,
        resubmit=payload.resubmit,
    )
    await write_audit(
        session,
        operator_id=user.id,
        action="start_oa_approval",
        business_type="oa_instance",
        business_id=row.id,
        after={
            "status": row.status,
            "instance_id": row.instance_id,
            "submit_round": row.submit_round,
            "inquiry_id": inquiry.id,
        },
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize(row), row.error or "已发起钉钉询价审批")


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
RESOLVABLE_STATUSES = ("needs_review", "failed", "not_sent")


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


@router.post("/oa-instances/{oa_id}/resolve")
async def resolve_oa_instance(
    oa_id: int,
    payload: ResolveOa,
    request: Request,
    user: CurrentUser = Depends(require_permission("quote:manage")),
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
    from app.modules.inquiry import service as inquiry_service

    row = await session.get(OaInstance, oa_id)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "审批记录不存在", 404)
    # 数据范围跟需求走：能对别人的需求发起审批的人，同样需要能处理它的异常
    await inquiry_service.get_visible_or_404(session, user, row.inquiry_id)

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
            .where(OaInstance.inquiry_id == inquiry_id)
            .order_by(OaInstance.submit_round.desc())
        )
    ).scalars().all()
    return ok([svc.serialize(row) for row in rows])


@router.post("/dingtalk/oa-sync")
async def trigger_oa_sync(
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    """立刻拉一次审批状态（与定时任务同一个函数，验收时不用等）。"""
    # 这里不需要 Query 装饰器：它是函数参数，不是请求参数
    result = await svc.sync_pending_instances(session, limit=50)
    await session.commit()
    return ok(result, result.get("message") or f"已检查 {result['checked']} 条，状态变更 {result['changed']} 条")


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

    权限与 `/dingtalk/oa-sync` 一致（`settings:manage`）：这是管理员动作，
    而且界面就挂在系统设置里。
    """
    result = await svc.sync_users(session)
    if result.get("disabled"):
        # 登录闸（DINGTALK_LOGIN_OFF）关着：**没去拉通讯录**，也没有数据要提交。
        # ⚠️ 判的是**登录闸**而不是审批总闸（2026-10-14）：这一步做的是"把人对上"，
        # 本身就是为了让员工能扫码进来 —— 只开登录时它必须能用。
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


@router.get("/inquiries/{inquiry_id}/oa-submission")
async def inquiry_submission_options(
    inquiry_id: int,
    user: CurrentUser = Depends(require_permission("quote:manage")),
    session: AsyncSession = Depends(get_db),
):
    """仅准备CRM提交界面，不创建审批、不上传钉钉、不触发通知。"""
    from app.modules.inquiry.service import get_visible_or_404
    from app.modules.settings.service import get_setting
    inquiry = await get_visible_or_404(session, user, inquiry_id)
    cfg = await get_setting(session, "dingtalk_oa") or {}
    owners = [{"user_id": str(item["user_id"]), "name": str(item.get("name") or "报价员")}
              for item in cfg.get("quote_owners", []) if isinstance(item, dict) and item.get("user_id")]
    return ok({
        "configured": bool(cfg.get("process_code")),
        "enabled": not app_settings.dingtalk_push_off,
        "quote_owner_required": bool(cfg.get("quote_owner_component")),
        "quote_owners": owners,
        "selected_quote_owner": inquiry.oa_quote_user_id,
        "image_required": bool(cfg.get("image_component")),
        "file_business_type": cfg.get("inquiry_file_business_type") or "inquiry",
        "fields": await _submission_fields(cfg),
    })
