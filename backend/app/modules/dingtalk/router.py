"""钉钉 OA 审批接口（文档 §11.3 :152 / 场景11）。

发起与回收都在这里收口；**模板与字段映射读设置项 `dingtalk_oa`**，
所以换模板不用改代码——这正是"业务定了走钉钉"之后还需要的那一步。
"""

from datetime import UTC, datetime
import json

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, PositiveInt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.config import settings as app_settings
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, get_current_user, require_permission
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
    from app.modules.settings import service as settings_service

    # 复用 inquiry 模块的可见性检查（P1 修复）：直接 `session.get` 按 id 取会**绕过数据范围**
    # ——业务员能对别人的定制需求发起审批。同一个文件里就有现成的
    # `get_visible_or_404`，没有理由另写一套（另写必然和列表页的判据分叉）。
    from app.modules.inquiry import service as inquiry_service

    inquiry = await inquiry_service.get_visible_or_404(session, user, inquiry_id)

    # 提交前的必填项检查（2026-10-16 补，后端侧）：判据见 `missing_submit_fields`。
    # 只拦**提交**，不拦保存草稿：草稿本来就允许暂缺（同一份文档的明确要求）。
    missing_required = missing_submit_fields(inquiry)
    if missing_required:
        raise AppError(
            ErrorCode.REQUIRED_FIELD_MISSING,
            f"提交钉钉询价前请先补齐：{'、'.join(missing_required)}。"
            "生产部要按这两项报价，缺了报不出来；也可以先保存草稿。",
            422,
        )

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
    content_component = (cfg or {}).get("content_component")
    if content_component:
        field_map[str(content_component)] = "\n".join(
            f"{label}：{value}"
            for label, value in (
                ("询价编号", inquiry.inquiry_no),
                ("产品名称", inquiry.title),
                ("需求说明", inquiry.description),
                ("数量", inquiry.quantity),
                ("目标价", inquiry.target_price),
                ("版本", inquiry.version),
            )
            if value not in (None, "")
        )
    supplemental_fields = await _submission_fields(cfg or {})
    # 发起接口的 name 必须使用模板真实中文标签，尤其是 DDPhotoField；只传控件 ID
    # 会导致钉钉接受审批单但把图片控件留空。这里保留一份完整字段定义给状态机，
    # 界面仍只展示销售需要填写的那组字段。
    all_field_definitions = await _submission_fields(cfg or {}, include_all=True)
    supplied = payload.extra_fields or {}
    attachment_field = next((f for f in supplemental_fields if f["type"] == "DDAttachment"), None)
    selected_attachments = []
    if payload.attachment_file_ids:
        from app.core.deps import ensure_permission
        from app.modules.file.model import BusinessFile, FileRecord
        from app.modules.file.storage import absolute_path
        ensure_permission(user, "file:view")
        if not attachment_field:
            raise AppError(ErrorCode.PARAM_ERROR, "当前询价模板没有可用的附件字段", 422)
        ids = list(dict.fromkeys(payload.attachment_file_ids))
        rows = (await session.execute(select(FileRecord).join(BusinessFile, BusinessFile.file_id == FileRecord.id).where(
            BusinessFile.business_type == ((cfg or {}).get("inquiry_file_business_type") or "inquiry"),
            BusinessFile.business_id == inquiry.id, FileRecord.id.in_(ids),
        ))).scalars().unique().all()
        by_id = {r.id: r for r in rows}
        if set(by_id) != set(ids):
            raise AppError(ErrorCode.PARAM_ERROR, "所选附件不属于这条询价，请重新选择", 422)
        for file_id in ids:
            file = by_id[file_id]
            path = absolute_path(file.object_key)
            if not path.is_file() or path.stat().st_size != file.size:
                raise AppError(ErrorCode.PARAM_ERROR, f"附件「{file.file_name}」内容缺失或不完整，请重新上传", 422)
            selected_attachments.append((file.file_name, path))
    if attachment_field and attachment_field["id"] in supplied:
        raise AppError(ErrorCode.PARAM_ERROR, "附件只能从当前询价已上传的文件中选择", 422)
    for field in supplemental_fields:
        if field["type"] == "DDAttachment":
            if field["required"] and not selected_attachments:
                raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "请选择附件", 422)
            continue
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
    # 钉钉同时要求：部门控件里的值 + 创建审批实例请求体顶层的 deptId。
    # 之前只填了表单控件，接口仍会报“发起部门 id 错误/发起人不在发起部门”。
    dept_id = None
    if app_settings.dingtalk_push_off:
        if dept_component:
            field_map[dept_component] = "（待查询：钉钉部门编号）"
    else:
        dept_ids = await get_client().get_user_dept_ids(ding_user_id)
        if not dept_ids:
            raise AppError(ErrorCode.PARAM_ERROR, "钉钉未返回你的提报部门，请先核对员工部门资料", 422)
        dept_id = dept_ids[0]
        if dept_component:
            field_map[dept_component] = dept_id
    # 额外字段（业务临时补的）按 componentId 直接给
    for component_id, value in (payload.extra_fields or {}).items():
        if component_id and value not in (None, ""):
            field_map[component_id] = value

    # 只有模板配置了独立的摘要控件时才写完整摘要；不能把摘要回写到
    # “材质及工艺要求”本身，否则钉钉会显示一整段重复内容。
    summary_component = content_component
    if summary_component:
        summary = [f'{label}：{value}' for label, value in (
            ('询价编号', inquiry.inquiry_no), ('版本', inquiry.version),
            ('产品名称', inquiry.title), ('材质及工艺要求', inquiry.description),
            ('预估订购数量', inquiry.quantity), ('目标价', inquiry.target_price),
            ('备注', getattr(inquiry, 'remark', None)),
            ('修订说明', getattr(inquiry, 'revision_note', None)),
        ) if value is not None and value != '']
        summary.extend(f'{field["label"]}：{supplied[field["id"]]}'
                       for field in supplemental_fields
                       if field['type'] != 'DDAttachment' and supplied.get(field['id']))
        if selected_attachments:
            summary.append('随单附件：' + '、'.join(name for name, _ in selected_attachments))
        field_map[str(summary_component)] = '\n'.join(summary)

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
                "钉钉要求必须上传「产品参考图片」，请先上传 PNG、JPG 等普通图片；SVG 等可执行格式不能作为参考图片",
                422,
            )
        image_file_id, filename, content = found
        if app_settings.dingtalk_push_off:
            # 干跑：不传图，只记下会用哪一张（对外零请求）
            field_map[image_component] = f"（待上传：{filename}）"
        else:
            import hashlib, hmac, time
            expires = int(time.time()) + 7 * 24 * 3600
            message = f"{image_file_id}:{expires}".encode()
            signature = hmac.new(app_settings.jwt_secret.encode(), message, hashlib.sha256).hexdigest()
            image_url = f"https://crm.mandla.cn/api/v1/public/inquiry-images/{image_file_id}?expires={expires}&sig={signature}"
            field_map[image_component] = json.dumps(
                [image_url], ensure_ascii=False
            )

    if selected_attachments:
        values = []
        for filename, path in selected_attachments:
            if app_settings.dingtalk_push_off:
                values.append({"fileName": filename, "pendingUpload": True})
            else:
                from app.modules.dingtalk.attachments import upload_approval_attachment
                from app.modules.dingtalk.client import DingTalkError
                import httpx
                try:
                    values.append(await upload_approval_attachment(get_client(), user_id=ding_user_id,
                                                                  filename=filename, content=path.read_bytes()))
                except (DingTalkError, httpx.HTTPError, OSError, ValueError) as error:
                    raise AppError(ErrorCode.PARAM_ERROR, f"附件「{filename}」未成功传入钉钉，本次尚未发起审批。请重试或联系管理员核对附件权限", 422) from error
        field_map[attachment_field["id"]] = json.dumps(values, ensure_ascii=False)

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
        field_definitions=all_field_definitions,
        dept_id=dept_id,
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
    """立即向钉钉查询当前单据；不发起、不撤销、不改变审批决定。"""
    row = await session.get(OaInstance, oa_id, with_for_update=True)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, '审批记录不存在', 404)
    await ensure_oa_type_scope(session, user, row)
    if app_settings.dingtalk_push_off:
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, '钉钉连接当前已关闭，请联系管理员', 422)
    if not row.instance_id:
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, '尚无钉钉审批单，请先核对提交状态', 422)
    # 同一条审批短时间重复点击复用快照；行锁串行化并发请求。
    synced = row.synced_at
    if synced is not None and synced.tzinfo is None:
        synced = synced.replace(tzinfo=UTC)
    if synced is None or (datetime.now(UTC) - synced).total_seconds() >= 3:
        await svc.sync_pending_instances(session, oa_id=row.id)
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
    from app.modules.dingtalk.client import get_client, DingTalkError, DingTalkUnknownOutcome
    from app.modules.user.model import User

    row = await session.get(OaInstance, oa_id, with_for_update=True)
    if row is None or row.oa_type not in ("inquiry", "sample", "contract"):
        raise AppError(ErrorCode.NOT_FOUND, "询价、打样或合同审批记录不存在", 404)
    await ensure_oa_type_scope(session, user, row)
    if row.status != "pending" or not row.instance_id:
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, f"当前审批状态为「{row.status}」，不能撤回", 422)
    if app_settings.dingtalk_push_off:
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "钉钉推送已关闭，无法发送撤回请求", 422)
    account = await session.get(User, user.id)
    ding_user_id = (account.dingtalk_userid if account else None) or await get_client().search_user_id_by_name(user.name)
    if not ding_user_id:
        raise AppError(ErrorCode.PARAM_ERROR, "找不到当前用户的钉钉身份，无法撤回", 422)
    try:
        await get_client().terminate_process_instance(
            instance_id=row.instance_id,
            operating_user_id=ding_user_id,
            remark="CRM 撤回钉钉审批",
        )
    except DingTalkUnknownOutcome as exc:
        row.error = '撤回结果暂未确认，请等待同步或在钉钉核对原单，不要重复发起审批'
        await session.commit()
        raise AppError(ErrorCode.EXTERNAL_ERROR, row.error, 502) from exc
    except DingTalkError as exc:
        raise AppError(ErrorCode.EXTERNAL_ERROR, '钉钉未接受撤回，请确认原单仍在审批中且当前账号有撤回权限', 422) from exc
    row.error = None
    row.status = "withdrawn"
    row.result = {**(row.result or {}), "crm_withdrawn": True, "withdrawn_by": ding_user_id,
                  "withdraw_snapshot_pending": True}
    row.synced_at = datetime.now(UTC)
    row.updated_at = datetime.now(UTC)
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
    inquiry = await get_visible_or_404(session, user, inquiry_id)
    return await _submission_options(session, inquiry.oa_quote_user_id)


@router.get("/inquiries/oa-submission-options")
async def new_inquiry_submission_options(
    user: CurrentUser = Depends(require_permission("quote:manage")),
    session: AsyncSession = Depends(get_db),
):
    """新建表单只读模板；不创建需求、审批或附件。"""
    return await _submission_options(session)


async def _submission_options(session, selected_quote_owner=None):
    from app.modules.settings.service import get_setting
    cfg = await get_setting(session, "dingtalk_oa") or {}
    owners = [{"user_id": str(item["user_id"]), "name": str(item.get("name") or "报价员")}
              for item in cfg.get("quote_owners", []) if isinstance(item, dict) and item.get("user_id")]
    return ok({
        "configured": bool(cfg.get("process_code")),
        "enabled": not app_settings.dingtalk_push_off,
        "quote_owner_required": bool(cfg.get("quote_owner_component")),
        "quote_owners": owners,
        "selected_quote_owner": selected_quote_owner,
        "image_required": bool(cfg.get("image_component")),
        "file_business_type": cfg.get("inquiry_file_business_type") or "inquiry",
        "fields": await _submission_fields(cfg),
    })
