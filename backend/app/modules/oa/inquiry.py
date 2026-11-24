"""CRM 定制询价 → 自研 OA（首段：CRM 提报 + 办理人指定报价方案）。"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import AppError, ErrorCode
from app.modules.dingtalk.model import OaInstance
from app.modules.file.model import BusinessFile, FileRecord
from app.modules.file.storage import absolute_path
from app.modules.inquiry.model import CustomInquiry
from app.modules.oa import client
from app.modules.oa.service import ApprovalUploads, _map_status, review_stuck_submission, send_prepared_approval

PROVIDER = "self_oa"
OA_TYPE = "inquiry"

#: 只包含销售在 CRM 提报页要填的字段；其余由 OA 办理人/审批人处理。
OA_FIELDS = [
    {"id": "quote_owner", "label": "对接报价员", "type": "text", "required": True},
    {"id": "product_reference", "label": "产品名称/编号", "type": "text", "required": True},
    {"id": "quote_scope", "label": "报价员负责范围", "type": "text", "required": True},
    {"id": "reply_date", "label": "期望回复时间", "type": "date", "required": True},
    {"id": "provide_sample", "label": "是否提供样品", "type": "choice", "required": True, "options": ["是", "否"]},
    {"id": "customer_contact", "label": "客户名称、联系人、联系方式", "type": "text", "required": True},
    {"id": "product_model", "label": "产品型号及规格", "type": "text", "required": True},
    {"id": "delivery_date", "label": "大货交付时间", "type": "text", "required": False},
    {"id": "special_note", "label": "特别说明", "type": "text", "required": False, "multiline": True},
    {"id": "attachments", "label": "附件", "type": "attachment", "required": False},
]


NATIVE_INQUIRY_CODE = "key-account-product-inquiry"
#: 同序《产品询价申请》销售段的字段与顺序。线上模板读不到时（OA 未启动、账号没映射、
#: 模板暂时不可用），CRM 新建/编辑表单也照这一组展示，不能退回只剩 7 个 CRM 字段。
SALES_TEMPLATE_FIELDS = [
    {"id": "reporter", "label": "提报人", "type": "user", "required": True},
    {"id": "product_name", "label": "产品名称", "type": "text", "required": True},
    {"id": "report_department", "label": "提报部门", "type": "department", "required": True},
    {"id": "quoter", "label": "对接报价员", "type": "user", "required": True},
    {"id": "quote_scope", "label": "报价员负责范围", "type": "text", "required": True},
    {"id": "reply_date", "label": "期望回复时间", "type": "date", "required": True},
    {"id": "provide_sample", "label": "是否提供样品", "type": "choice", "required": True, "options": ["是", "否"]},
    {"id": "delivery_time", "label": "大货交付时间", "type": "text", "required": False},
    {"id": "customer", "label": "客户名称、联系人、联系方式", "type": "text", "required": True},
    {"id": "product_reference", "label": "产品名称/编号", "type": "text", "required": True},
    {"id": "specification", "label": "产品型号及规格", "type": "text", "required": True},
    {"id": "quantity", "label": "预估订购数量", "type": "text", "required": True},
    {"id": "requirements", "label": "材质及工艺要求", "type": "text", "required": True, "multiline": True},
    {"id": "reference_images", "label": "产品参考图片（高清图片及细节图）", "type": "attachment",
     "required": False, "max_files": 9},
    {"id": "notes", "label": "特别说明", "type": "text", "required": False, "multiline": True},
    {"id": "attachments", "label": "附件", "type": "attachment", "required": False},
]


def fallback_entry_fields() -> list[dict]:
    """静态模板里由 CRM 提交表单填写的那些格（id 已换成前端识别的名字）。"""
    return native_entry_fields({"fields": SALES_TEMPLATE_FIELDS})


NATIVE_FIELDS = {
    "title": "product_name",
    "inquiry_no": "product_reference",
    "description": "requirements",
    "customer_contact": "customer",
    "product_model": "specification",
    "delivery_date": "delivery_time",
    "special_note": "notes",
}
CRM_IDENTITY_FIELDS = {"applicant_name", "department", "quote_owner"}


def uses_native_inquiry() -> bool:
    return not settings.oa_inquiry_template_id.strip() and settings.oa_inquiry_template_code.strip() == NATIVE_INQUIRY_CODE


def saved_quote_owner(inquiry) -> str | None:
    draft = (getattr(inquiry, "extra", None) or {}).get("oa_draft")
    if isinstance(draft, dict) and "quote_owner_user_id" in draft:
        selected = draft["quote_owner_user_id"]
        return selected if isinstance(selected, str) else None
    return inquiry.oa_quote_user_id


def native_quoters(template):
    scope = next((field.get("user_scope", []) for field in template["fields"] if field["id"] == "quoter"), [])
    return [person for person in template.get("people", []) if person["id"] in scope]


def native_entry_fields(template):
    """Expose OA's editable sales fields through the existing CRM form controls."""
    types = {"text": "TextField", "choice": "DDSelectField", "date": "DDDateField", "attachment": "DDAttachment"}
    field_ids = {
        "quote_scope": "quote_scope", "reply_date": "reply_date", "provide_sample": "provide_sample",
        "customer": "customer_contact", "product_reference": "product_reference", "specification": "product_model",
        "delivery_time": "delivery_date", "notes": "special_note", "attachments": "attachments",
    }
    return [{**field, "id": field_ids[field["id"]], "type": types[field["type"]],
             "options": field.get("options", [])}
            for field in template["fields"] if field["id"] in field_ids and field["type"] in types]


async def native_submission_identity(user_id, quoter):
    try:
        templates = await client.list_templates(user_id)
    except (client.OaError, client.OaUnknownOutcome) as exc:
        raise AppError(ErrorCode.EXTERNAL_ERROR, "无法读取同序询价模板或提报账号，请稍后重试", 502) from exc
    template = max((item for item in templates if item["code"] == NATIVE_INQUIRY_CODE),
                   key=lambda item: item["version"], default=None)
    if template is None:
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "当前账号无法发起同序询价，请联系管理员", 422)
    identity = template.get("applicant") or {}
    if not identity.get("id") or not identity.get("department_id"):
        raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "请先在同序完善提报人的所属部门", 422)
    if quoter not in {person["id"] for person in native_quoters(template)}:
        raise AppError(ErrorCode.PARAM_ERROR, "请选择同序模板允许的对接报价员", 422)
    return identity


def native_inquiry_fields(fields, identity, quoter, image_ids, attachment_ids):
    """Translate business data using the identity returned by OA, never user-entered names."""
    return {
        **{NATIVE_FIELDS.get(key, key): value for key, value in fields.items() if key not in CRM_IDENTITY_FIELDS},
        "reporter": identity["id"],
        "report_department": identity["department_id"],
        "quoter": quoter,
        "reference_images": image_ids,
        "attachments": attachment_ids,
    }


def oa_template_fields() -> list[dict]:
    kind_map = {"text": "TextField", "choice": "DDSelectField", "date": "DDDateField",
                "attachment": "DDAttachment"}
    return [{**field, "type": kind_map.get(field["type"], field["type"]),
             "options": field.get("options") or []} for field in OA_FIELDS]


def ensure_submission_enabled():
    if not settings.oa_inquiry_submission_enabled:
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "询价审批暂未启用，可先保存需求和内部报价草稿", 422)


def _template_id() -> str:
    ensure_submission_enabled()
    template_id = settings.oa_inquiry_template_id.strip() or settings.oa_inquiry_template_code.strip()
    if not template_id:
        raise AppError(ErrorCode.PARAM_ERROR, "还没配置询价 OA 审批模板（OA_INQUIRY_TEMPLATE_ID 或 OA_INQUIRY_TEMPLATE_CODE）", 422)
    return template_id


async def latest_inquiry_row(session: AsyncSession, inquiry_id: int, *, for_update: bool = False) -> OaInstance | None:
    stmt = (select(OaInstance)
            .where(OaInstance.oa_type == OA_TYPE, OaInstance.inquiry_id == inquiry_id)
            .order_by(OaInstance.submit_round.desc(), OaInstance.id.desc()).limit(1))
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return (await session.execute(stmt)).scalars().first()


async def _department_name(session: AsyncSession, user) -> str:
    from app.modules.user.model import Department
    if not user.department_id:
        return ""
    dept = await session.get(Department, user.department_id)
    return dept.name if dept is not None else ""


async def customer_contact_text(session: AsyncSession, inquiry, *, customer=None) -> str:
    """OA「客户名称、联系人、联系方式」一格：CRM 已有的客户名/联系人/电话直接拼好。"""
    from app.modules.customer.model import Contact, Customer

    if customer is None and getattr(inquiry, "customer_id", None):
        customer = await session.get(Customer, inquiry.customer_id)
    if customer is not None and getattr(customer, "deleted_at", None):
        customer = None
    contact = None
    if getattr(inquiry, "contact_id", None):
        contact = await session.get(Contact, inquiry.contact_id)
    if contact is None and customer is not None and getattr(customer, "id", None):
        # 需求没指定联系人时，用该客户的主联系人补「客户名称、联系人、联系方式」。
        result = await session.execute(select(Contact).where(
            Contact.customer_id == customer.id,
            Contact.is_primary.is_(True),
            Contact.deleted_at.is_(None),
        ).limit(1))
        contact = result.scalars().first()
    if contact is not None and getattr(contact, "deleted_at", None):
        contact = None
    parts = [getattr(customer, "name", "") or ""]
    if contact is not None:
        parts += [contact.name or "", contact.mobile or contact.phone or ""]
    return "、".join(part for part in parts if part)


async def submission_prefill(session: AsyncSession, inquiry) -> dict[str, str]:
    """提交 OA 前的预填值：CRM 库里已有的字段不用再手打一遍（仍可在表单里改）。"""
    if inquiry is None:
        return {}
    values = {
        "customer_contact": await customer_contact_text(session, inquiry),
        "product_reference": getattr(inquiry, "inquiry_no", None) or getattr(inquiry, "title", "") or "",
        "special_note": getattr(inquiry, "remark", None) or "",
    }
    return {key: value for key, value in values.items() if value}


async def _attachments(session: AsyncSession, user, inquiry: CustomInquiry, file_ids: list[int], *, images: bool = False, max_count: int = 30, uploads=None) -> list[dict]:
    if not file_ids and not images:
        return []
    stmt = select(FileRecord).join(BusinessFile, BusinessFile.file_id == FileRecord.id).where(
        BusinessFile.business_type == "inquiry", BusinessFile.business_id == inquiry.id)
    if images:
        from sqlalchemy import or_
        stmt = stmt.where(or_(BusinessFile.category.is_(None), BusinessFile.category != "oa_attachment"),
                          FileRecord.mime_type.like("image/%")).order_by(FileRecord.id)
    else:
        stmt = stmt.where(FileRecord.id.in_(file_ids))
    rows = (await session.execute(stmt)).scalars().unique().all()
    if images:
        if not rows:
            raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "请先上传产品参考图片", 422)
        if len(rows) > max_count:
            raise AppError(ErrorCode.PARAM_ERROR, f"本次最多可提交 {max_count} 张产品参考图片，请移除多余图片", 422)
        if any(row.mime_type not in {"image/png", "image/jpeg"} for row in rows):
            raise AppError(ErrorCode.PARAM_ERROR, "OA 产品参考图片只支持 PNG/JPEG，请转换后重新上传", 422)
        file_ids = [row.id for row in rows]
    by_id = {row.id: row for row in rows}
    if set(by_id) != set(file_ids):
        raise AppError(ErrorCode.PARAM_ERROR, "所选附件不属于这条询价，请重新选择", 422)
    upload = uploads.stage if uploads is not None else client.upload_file
    uploaded = []
    for file_id in file_ids:
        record = by_id[file_id]
        path = absolute_path(record.object_key)
        if not path.is_file() or path.stat().st_size != record.size:
            raise AppError(ErrorCode.PARAM_ERROR, f"附件「{record.file_name}」缺失或不完整，请重新上传", 422)
        uploaded.append(await upload(
            client.applicant_identity(user), record.file_name, path if uploads is not None else path.read_bytes(), record.mime_type
        ))
    return uploaded


def require_submittable(inquiry):
    if getattr(inquiry, 'status', 'open') not in {'open', 'developing'}:
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, '只有待评估、开发中的需求可以发起询价审批；已归档或已转商机的需求请查看原记录', 409)


async def start_inquiry_approval(session: AsyncSession, current_user, inquiry: CustomInquiry,
                                  payload) -> tuple[OaInstance, str]:
    from app.modules.customer.model import Customer
    from app.modules.user.model import User

    if not client.configured():
        raise AppError(ErrorCode.PARAM_ERROR, "还没配置自研 OA 对接，请联系管理员", 422)
    user = await session.get(User, current_user.id)
    applicant = client.applicant_identity(user)
    if not applicant:
        raise AppError(ErrorCode.PARAM_ERROR, "请联系管理员为当前账号绑定同序人员后再提交", 422)
    template_id = _template_id()
    native = uses_native_inquiry()
    approver = "" if native else settings.oa_inquiry_approver_dingtalk_id.strip()

    # Lock the business row as well: two first submissions have no approval row to lock.
    await session.get(CustomInquiry, inquiry.id, with_for_update=True, populate_existing=True)
    require_submittable(inquiry)
    latest = await latest_inquiry_row(session, inquiry.id, for_update=True)
    if await review_stuck_submission(session, latest):
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "上一次 OA 发起结果待核对，请先到 OA 核对", 409)
    if latest is not None and latest.status in {"pending", "submitting"}:
        return latest, "这条询价已有在办 OA 审批，请查看进度，不要重复发起"
    if latest is not None and latest.status == "needs_review":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "上一次 OA 发起结果待核对，请先到 OA 核对", 409)

    resubmit = bool(getattr(payload, 'resubmit', False))
    if latest is not None and latest.status == 'approved':
        return latest, '这条询价审批已通过，请查看结果；不能重复发起'
    if latest is not None and latest.status in {'rejected', 'withdrawn'} and not resubmit:
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, '请从审批记录选择“修改后重新提交”，明确开启新一轮审批', 409)
    if resubmit and (latest is None or latest.status not in {'rejected', 'withdrawn'}):
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, '只有已驳回或已撤回的审批可以重新提交', 409)
    if latest is not None and latest.status in {'failed', 'not_sent', 'skipped'}:
        row = latest
        row.status = 'submitting'
        row.error = None
        row.attempt_count = int(row.attempt_count or 0) + 1
        row.last_attempt_at = datetime.now(UTC)
        external_ref = row.idempotency_key
    else:
        round_no = (latest.submit_round + 1) if latest is not None else 1
        external_ref = client.approval_external_ref("inquiry", inquiry.id, round_no)
        row = OaInstance(
            customer_id=inquiry.customer_id, inquiry_id=inquiry.id, inquiry_version=int(inquiry.version or 1),
            oa_type=OA_TYPE, provider=PROVIDER, idempotency_key=external_ref, submit_round=round_no,
            process_code=template_id, originator_user_id=str(applicant),
            status="submitting", attempt_count=1, created_by=current_user.id,
            created_at=datetime.now(UTC), last_attempt_at=datetime.now(UTC),
        )
        session.add(row)
    await session.flush()
    uploads = ApprovalUploads()
    extra = {str(k): str(v) for k, v in (getattr(payload, "extra_fields", None) or {}).items()}
    customer = await session.get(Customer, inquiry.customer_id) if inquiry.customer_id else None
    reply = (extra.get("reply_date") or "").strip()
    fields = {
        "title": inquiry.title,
        "inquiry_no": extra.get("product_reference") or inquiry.inquiry_no or "",
        "description": inquiry.description or "",
        "quantity": str(inquiry.quantity) if inquiry.quantity is not None else "",
        "applicant_name": user.name,
        "department": await _department_name(session, user),
        "quote_owner": extra.get("quote_owner") or inquiry.oa_quote_user_name or "待分配",
        "quote_scope": extra.get("quote_scope", ""),
        "reply_date": reply[:10] if reply else "",
        "provide_sample": extra.get("provide_sample", ""),
        "customer_contact": extra.get("customer_contact") or await customer_contact_text(
            session, inquiry, customer=customer
        ),
        "product_model": extra.get("product_model", ""),
        "delivery_date": extra.get("delivery_date", ""),
        "special_note": extra.get("special_note", ""),
    }
    fields = {key: value for key, value in fields.items() if value not in (None, "")}
    if not fields.get("title"):
        raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "询价产品名称不能为空", 422)
    required = [("inquiry_no" if field["id"] == "product_reference" else field["id"], field["label"]) for field in OA_FIELDS if field.get("required")]
    required += [("applicant_name", "提报人"), ("department", "提报部门"),
                 ("inquiry_no", "产品名称/编号"), ("description", "材质及工艺要求"), ("quantity", "预估订购数量")]
    quoter = getattr(payload, "quote_owner_user_id", None)
    if native:
        from uuid import UUID
        if quoter is None:
            quoter = saved_quote_owner(inquiry)
        try:
            quoter = str(UUID(quoter or ""))
        except ValueError as exc:
            raise AppError(ErrorCode.PARAM_ERROR, "请选择同序模板允许的对接报价员", 422) from exc
        required = [(key, label) for key, label in required if key not in CRM_IDENTITY_FIELDS]
    missing = [label for key, label in required if not str(fields.get(key) or "").strip()]
    if missing:
        raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "请补齐：" + "、".join(missing), 422)
    identity = await native_submission_identity(applicant, quoter) if native else None
    selected_files = list(getattr(payload, "attachment_file_ids", []) or [])
    image_ids = [str(item["id"]) for item in await _attachments(
        session, user, inquiry, [], images=True, max_count=min(9 if native else 30, 30-len(set(selected_files))), uploads=uploads)]
    file_ids = [str(item["id"]) for item in await _attachments(session, user, inquiry, selected_files, uploads=uploads)]
    attachment_ids = list(dict.fromkeys(image_ids + file_ids))
    if native:
        fields = native_inquiry_fields(fields, identity, quoter, image_ids, file_ids)
    else:
        fields["DDPhotoField_1U7CNXCKQNS00"] = image_ids
        fields["attachments"] = file_ids
    payload_data = {
        "external_ref": external_ref,
        **client.applicant_fields(applicant),
        ("template_id" if settings.oa_inquiry_template_id.strip() else "template_code"): template_id,
        "title": inquiry.title,
        "reason": f"CRM 定制询价 {inquiry.inquiry_no or inquiry.id}",
        "amount": str(inquiry.target_price or 0),
        "fields": fields,
        "attachment_ids": attachment_ids,
    }
    if approver and isinstance(applicant, str):
        payload_data["approver_dingtalk_user_id"] = approver
    inquiry, row, summary = await send_prepared_approval(session, inquiry, row, payload_data, uploads)

    row.instance_id = str(summary["id"])
    row.status = _map_status(summary.get("status"))
    row.result = summary
    row.synced_at = datetime.now(UTC)
    row.updated_at = row.synced_at
    if inquiry is not None and inquiry.deleted_at is None:
        inquiry.extra = {**(inquiry.extra or {}), "oa_approval": {
            "provider": PROVIDER, "oa_instance_id": row.id, "instance_id": row.instance_id,
            "status": row.status, "synced_at": row.synced_at.isoformat(),
        }}
    await session.flush()
    return row, "已发起 OA 定制询价审批"


async def sync_inquiry_row(session: AsyncSession, row: OaInstance) -> OaInstance | None:
    from app.modules.oa.service import sync_self_oa_row

    return await sync_self_oa_row(session, row)


async def apply_inquiry_summary(session: AsyncSession, row: OaInstance, summary: dict) -> OaInstance:
    row.status = _map_status(summary.get("status"))
    row.result = summary
    row.synced_at = datetime.now(UTC)
    row.updated_at = row.synced_at
    row.error = None
    inquiry = await session.get(CustomInquiry, row.inquiry_id, with_for_update=True)
    if inquiry is not None:
        inquiry.extra = {**(inquiry.extra or {}), "oa_approval": {
            "provider": PROVIDER, "oa_instance_id": row.id, "instance_id": row.instance_id,
            "status": row.status, "synced_at": row.synced_at.isoformat(),
        }}
    await session.flush()
    return row


async def withdraw_inquiry_row(session: AsyncSession, row: OaInstance, note: str) -> OaInstance:
    from app.modules.oa.service import withdraw_self_oa_row

    return await withdraw_self_oa_row(session, row, note)
