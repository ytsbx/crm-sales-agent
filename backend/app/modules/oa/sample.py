"""CRM 打样申请 → 自研 OA（首段：CRM 提报 + 周星聿/刘帅帅会签）。

只复刻 CRM 当前用到的打样审批段，不搬钉钉后面的物料/采购/生产节点。
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import AppError, ErrorCode
from app.modules.dingtalk.model import OaInstance
from app.modules.file.model import BusinessFile, FileRecord
from app.modules.file.storage import absolute_path
from app.modules.oa import client
from app.modules.oa.service import ApprovalUploads, _map_status, review_stuck_submission, send_prepared_approval
from app.modules.sample.model import SampleItem, SampleRequest

PROVIDER = "self_oa"
OA_TYPE = "sample"

#: OA 模板字段定义（与 `crm-sample-oa` 模板保持一致；前端沿用动态表单）
OA_FIELDS = [
    {"id": "sample_type", "label": "打样类型", "type": "choice", "required": True,
     "options": ["销售客户打样", "设计研发内部打样"]},
    {"id": "applicant_name", "label": "申请人员", "type": "text", "required": True, "system": True},
    {"id": "department", "label": "归属部门", "type": "text", "required": True, "system": True},
    {"id": "product_category", "label": "打样产品大类", "type": "choice", "required": True,
     "options": ["高频类产品", "车缝类产品"]},
    {"id": "maker_unit", "label": "样品制作单位", "type": "choice", "required": True,
     "options": ["义乌打样部", "山东生产部", "外协厂打样"]},
    {"id": "target_date", "label": "要求完成日期", "type": "date", "required": True},
    {"id": "score", "label": "本次打样积分", "type": "number", "required": True},
    {"id": "sample_items", "label": "样品明细明细表", "type": "attachment", "required": True, "system": True},
    {"id": "sample_note", "label": "打样需求说明", "type": "text", "required": True, "multiline": True},
]


def oa_template_fields() -> list[dict]:
    """给 CRM 现有动态表单用的字段结构（类型名沿用前端识别的控件名）。"""
    kind_map = {"text": "TextField", "choice": "DDSelectField", "date": "DDDateField",
                "number": "NumberField", "attachment": "DDAttachment"}
    return [{
        **field,
        "type": kind_map.get(field["type"], field["type"]),
        "options": field.get("options") or [],
        "system": bool(field.get("system")),
    } for field in OA_FIELDS]


SOURCE_CODE = "form-21e21dedad1d410c93e996930d012bb3"
SOURCE_IDS = {"applicant": "applicant_name", "production_unit": "maker_unit", "due_date": "target_date",
              "points": "score", "sample_details": "sample_items", "requirements": "sample_note"}


async def native_form(account):
    applicant = client.applicant_identity(account)
    if not applicant:
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "请先对应同序 OA 申请人员", 422)
    try:
        rows = await client.list_templates(applicant)
    except (client.OaError, client.OaUnknownOutcome) as exc:
        raise AppError(ErrorCode.EXTERNAL_ERROR, "暂时无法读取同序打样模板，请重试", 502) from exc
    selected = _template_id()
    template = max((row for row in rows if (row["id"] if settings.oa_sample_template_id.strip() else row["code"]) == selected),
                   key=lambda row: row["version"], default=None)
    if not template or template["code"] != SOURCE_CODE:
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "当前账号无法发起同序原版打样模板", 422)
    types = {"text": "TextField", "choice": "DDSelectField", "date": "DDDateField", "number": "NumberField",
             "attachment": "DDAttachment"}
    identity = template.get("applicant") or {}
    return {"configured": True, "enabled": bool(identity.get("id") and identity.get("department_id")),
            "provider": "self_oa", "native_form": True,
            "prefill": {"applicant_name": identity.get("name", ""), "department": identity.get("department_name", "")},
            "fields": [{**field, "id": SOURCE_IDS.get(field["id"], field["id"]),
                        "type": types.get(field["type"], field["type"]), "options": field.get("options", []),
                        "required": bool(field.get("required")),
                        "system": field["type"] in {"description", "serial", "user", "department", "attachment"}}
                       for field in template["fields"]]}


def _template_id() -> str:
    template_id = settings.oa_sample_template_id.strip() or settings.oa_sample_template_code.strip()
    if not template_id:
        raise AppError(ErrorCode.PARAM_ERROR, "还没配置打样 OA 审批模板（OA_SAMPLE_TEMPLATE_ID 或 OA_SAMPLE_TEMPLATE_CODE）", 422)
    return template_id


def _approvers() -> list[str]:
    raw = settings.oa_sample_approver_dingtalk_ids.strip()
    return [item.strip() for item in raw.split(",") if item.strip()]


async def latest_sample_row(session: AsyncSession, sample_id: int, *, for_update: bool = False) -> OaInstance | None:
    stmt = (select(OaInstance)
            .where(OaInstance.oa_type == OA_TYPE, OaInstance.inquiry_id == sample_id)
            .order_by(OaInstance.submit_round.desc(), OaInstance.id.desc()).limit(1))
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return (await session.execute(stmt)).scalars().first()


def _field(payload_fields: dict, key: str, fallback=None):
    value = payload_fields.get(key)
    return value if value not in (None, "") else fallback


async def _department_name(session: AsyncSession, user) -> str:
    from app.modules.user.model import Department
    if not user.department_id:
        return ""
    dept = await session.get(Department, user.department_id)
    return dept.name if dept is not None else ""


async def _selected_detail_files(session: AsyncSession, user, sample: SampleRequest,
                                 file_ids: list[int], *, uploads=None) -> list[dict]:
    """上传经办人自己填好并选择的《样品明细明细表》，不再由 CRM 生成 Excel。"""
    ids = list(dict.fromkeys(int(item) for item in file_ids))
    if not ids:
        raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "请上传样品明细明细表", 422)
    rows = (await session.execute(
        select(FileRecord).join(BusinessFile, BusinessFile.file_id == FileRecord.id).where(
            BusinessFile.business_type == "sample", BusinessFile.business_id == sample.id,
            FileRecord.id.in_(ids),
        )
    )).scalars().unique().all()
    by_id = {row.id: row for row in rows}
    if set(by_id) != set(ids):
        raise AppError(ErrorCode.PARAM_ERROR, "所选样品明细表不属于当前打样单，请重新选择", 422)
    upload = uploads.stage if uploads is not None else client.upload_file
    attachments = []
    for file_id in ids:
        row = by_id[file_id]
        path = absolute_path(row.object_key)
        if not path.is_file() or path.stat().st_size != row.size:
            raise AppError(ErrorCode.PARAM_ERROR, f"样品明细表「{row.file_name}」缺失或不完整，请重新上传", 422)
        try:
            attachments.append(await upload(
                client.applicant_identity(user), row.file_name,
                path if uploads is not None else path.read_bytes(), row.mime_type,
            ))
        except (client.OaError, OSError) as exc:
            raise AppError(ErrorCode.EXTERNAL_ERROR, f"样品明细表未能上传到 OA，尚未发起审批：{exc}", 502) from exc
    return attachments


async def _attachments(session: AsyncSession, user, sample: SampleRequest, payload_fields: dict, *, uploads=None) -> list[dict]:
    """把打样申请表 + 随单资料上传到 OA，返回附件元数据。"""
    from app.modules.sample.application_form import render_application_form, TEMPLATE_FILENAME
    from app.modules.sample.oa_content import submission_summary, validated_sample_details

    items = (await session.scalars(select(SampleItem).where(SampleItem.sample_request_id == sample.id))).all()
    attrs = {column.key: getattr(sample, column.key) for column in sample.__table__.columns}
    for key in ("material_spec", "product_size", "printing", "accessories", "packaging", "craft_requirement"):
        value = payload_fields.get("_" + key)
        if value is not None:
            attrs[key] = value
    for key in ("sample_purpose", "urgency", "sample_situation"):
        value = payload_fields.get("_" + key)
        if value is not None:
            attrs[key] = value or None
    attrs.update(validated_sample_details(payload_fields))
    sample_type = _field(payload_fields, "sample_type", sample.sample_type)
    category = _field(payload_fields, "product_category", sample.product_category)
    maker_unit = _field(payload_fields, "maker_unit", sample.maker_unit)
    attrs.update(sample_type=sample_type, product_category=category, maker_unit=maker_unit)
    due = _field(payload_fields, "target_date")
    if due:
        try:
            attrs["target_completion_date"] = date.fromisoformat(str(due)[:10])
        except ValueError as exc:
            raise AppError(ErrorCode.PARAM_ERROR, "要求完成日期格式无效", 422) from exc
    score = _field(payload_fields, "score")
    if score not in (None, ""):
        try:
            attrs["estimated_hours"] = Decimal(str(score)) / 10
        except InvalidOperation as exc:
            raise AppError(ErrorCode.PARAM_ERROR, "本次打样积分格式无效", 422) from exc
    note = _field(payload_fields, "sample_note", sample.sample_purpose or sample.remark or "")
    if not note:
        raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "请填写打样需求说明", 422)
    if not all([sample_type, category, maker_unit, attrs.get("target_completion_date")]):
        raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "请补齐打样类型、产品大类、制作单位、要求完成日期", 422)
    attrs["oa_submission_summary"] = submission_summary(
        SimpleNamespace(**attrs), list(items), note=note, applicant_name=user.name
    )

    linked = (await session.execute(
        select(FileRecord, BusinessFile.category).join(
            BusinessFile, BusinessFile.file_id == FileRecord.id
        ).where(BusinessFile.business_type == "sample", BusinessFile.business_id == sample.id)
        .order_by(BusinessFile.id)
    )).all()
    images = []
    prepared = []
    for file, category_name in linked:
        path = absolute_path(file.object_key)
        if not path.is_file() or path.stat().st_size != file.size:
            raise AppError(ErrorCode.PARAM_ERROR, f"文件「{file.file_name}」缺失或不完整，请重新上传", 422)
        if (file.mime_type or "").startswith("image/") or file.file_name.lower().rsplit(".", 1)[-1] in (
            "png", "jpg", "jpeg", "gif", "bmp", "webp", "tif", "tiff",
        ):
            images.append(path)
        else:
            prepared.append((file.file_name, path))
    try:
        content = render_application_form(
            SimpleNamespace(**attrs), list(items), applicant_name=user.name,
            department_name=maker_unit or "", product_images=images,
            sample_maker=attrs.get("sample_maker") or "",
        )
    except ValueError as exc:
        raise AppError(ErrorCode.PARAM_ERROR, str(exc), 422) from exc
    upload = uploads.stage if uploads is not None else client.upload_file
    try:
        attachments = [await upload(
            client.applicant_identity(user), TEMPLATE_FILENAME, content,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )]
        for filename, path in prepared:
            attachments.append(await upload(client.applicant_identity(user), filename, path if uploads is not None else path.read_bytes()))
    except (client.OaError, OSError) as exc:
        raise AppError(ErrorCode.EXTERNAL_ERROR, f"打样资料未能上传到 OA，尚未发起审批：{exc}", 502) from exc
    return attachments


async def start_sample_approval(
    session: AsyncSession, current_user, sample: SampleRequest, payload_fields: dict,
    *, sample_detail_file_ids: list[int] | None = None,
) -> tuple[OaInstance, str]:
    from app.modules.sample.dingtalk import collect_template_values
    from app.modules.user.model import User

    if not client.configured():
        raise AppError(ErrorCode.PARAM_ERROR, "还没配置自研 OA 对接，请联系管理员", 422)
    user = await session.get(User, current_user.id)
    identity = client.applicant_identity(user)
    if not identity:
        raise AppError(ErrorCode.PARAM_ERROR, "请联系管理员为当前账号绑定同序人员后再提交", 422)
    template_id = _template_id()
    approvers = _approvers() if isinstance(identity, str) else []

    latest = await latest_sample_row(session, sample.id, for_update=True)
    if await review_stuck_submission(session, latest):
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "上一次 OA 发起结果待核对，请先到 OA 核对", 409)
    if latest is not None and latest.status in {"pending", "submitting"}:
        return latest, "这张打样单已有在办 OA 审批，请查看进度，不要重复发起"
    if latest is not None and latest.status == "needs_review":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "上一次 OA 发起结果待核对，请先到 OA 核对", 409)
    if latest is not None and latest.status == "approved":
        return latest, "这张打样单的 OA 审批已经通过"

    native = settings.oa_sample_template_code.strip() == SOURCE_CODE
    native_options = await native_form(user) if native else None
    # 归属部门以同序申请人资料为准：同序里每位申请人都有完整部门，
    # CRM 的部门表可能没有对应项，不能因此挡住提交。
    prefill = (native_options or {}).get("prefill") or {}
    applicant_name = prefill.get("applicant_name") or user.name
    department = prefill.get("department") or await _department_name(session, user)
    for label, value in (("申请人员", applicant_name), ("归属部门", department)):
        if not (value or "").strip():
            raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, f"请补齐申请人的{label}后再提交", 422)
    field_metadata = (native_options["fields"] if native_options else oa_template_fields())
    fields, _ = collect_template_values(field_metadata, {
        "sample_type": _field(payload_fields, "sample_type", sample.sample_type),
        "product_category": _field(payload_fields, "product_category", sample.product_category),
        "maker_unit": _field(payload_fields, "maker_unit", sample.maker_unit),
        "target_date": _field(payload_fields, "target_date", sample.target_completion_date),
        "score": _field(payload_fields, "score"),
        "sample_note": _field(payload_fields, "sample_note", sample.sample_purpose or sample.remark),
    })
    fields.update(applicant_name=applicant_name, department=department)

    round_no = (latest.submit_round + 1) if latest is not None else 1
    external_ref = client.approval_external_ref("sample", sample.id, round_no)
    row = OaInstance(
        customer_id=sample.customer_id, inquiry_id=sample.id, inquiry_version=int(sample.version or 1),
        oa_type=OA_TYPE, provider=PROVIDER, idempotency_key=external_ref, submit_round=round_no,
        process_code=template_id, originator_user_id=str(identity),
        status="submitting", attempt_count=1, created_by=current_user.id,
        created_at=datetime.now(UTC), last_attempt_at=datetime.now(UTC),
    )
    session.add(row)
    await session.flush()
    uploads = ApprovalUploads()
    if native:
        attachments = await _selected_detail_files(
            session, user, sample, sample_detail_file_ids or [], uploads=uploads)
    else:
        attachments = await _attachments(session, user, sample, {**payload_fields, **fields}, uploads=uploads)
    attachment_ids = [str(item["id"]) for item in attachments]
    if not attachment_ids:
        raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "打样申请表未上传成功，请重试", 422)
    fields["sample_items"] = attachment_ids
    payload = {
        "external_ref": external_ref,
        **client.applicant_fields(identity),
        ("template_id" if settings.oa_sample_template_id.strip() else "template_code"): template_id,
        "title": f"打样申请 · {sample.id}",
        "reason": f"CRM 打样申请 {sample.id}",
        "amount": "0",
        "fields": fields,
        "attachment_ids": attachment_ids,
    }
    if approvers:
        payload["approver_dingtalk_user_ids"] = approvers
        payload["approver_mode"] = settings.oa_sample_approver_mode or "any"
    sample, row, summary = await send_prepared_approval(session, sample, row, payload, uploads)

    row.instance_id = str(summary["id"])
    row.status = _map_status(summary.get("status"))
    row.result = summary
    row.synced_at = datetime.now(UTC)
    row.updated_at = row.synced_at
    if sample is not None and sample.deleted_at is None and sample.status in {"pending", "rejected"}:
        sample.status = "pending"
        sample.source_context = {
            **(sample.source_context or {}),
            "dingtalk_oa": {**(sample.source_context or {}).get("dingtalk_oa", {}), "submitted": True,
                            "provider": PROVIDER, "oa_instance_id": row.id, "instance_id": row.instance_id},
        }
    await session.flush()
    return row, "已发起 OA 打样审批"


async def sync_sample_row(session: AsyncSession, row: OaInstance) -> OaInstance | None:
    from app.modules.oa.service import sync_self_oa_row

    return await sync_self_oa_row(session, row)


async def _apply_sample_summary(session, row, summary):
    new_status = _map_status(summary.get("status"))
    row.status = new_status
    row.result = summary
    row.synced_at = datetime.now(UTC)
    row.updated_at = row.synced_at
    row.error = None
    sample = await session.get(SampleRequest, row.inquiry_id, with_for_update=True)
    if sample is not None and sample.deleted_at is None and int(sample.version or 1) == row.inquiry_version:
        milestone = (summary.get("business_stages") or {}).get("sample")
        # 缺打样阶段小条不落成 approved，保持 pending 等同步（B10-01）。
        released = new_status in {"pending", "approved"} and isinstance(milestone, dict) and milestone.get("status") == "approved"
        sample_status = "approved" if released else new_status if new_status in {"rejected", "withdrawn"} else "pending"
        if sample.status == "pending" and sample_status in {"approved", "rejected", "withdrawn"}:
            sample.status = "rejected" if sample_status == "withdrawn" else sample_status
            sample.approved_at = datetime.now(UTC)
            sample.reject_reason = (
                "OA 打样审批已撤回，可重新提交；详情见 OA 审批记录"
                if new_status == "withdrawn" else
                "OA 打样审批已拒绝，处理意见见 OA 审批记录"
                if new_status == "rejected" else None
            )
        sample.source_context = {
            **(sample.source_context or {}),
            "dingtalk_oa": {**(sample.source_context or {}).get("dingtalk_oa", {}), "submitted": True,
                            "provider": PROVIDER, "oa_instance_id": row.id,
                            "instance_id": row.instance_id, "status": row.status},
        }
    await session.flush()
    return row


async def withdraw_sample_row(session: AsyncSession, row: OaInstance, note: str) -> OaInstance:
    from app.modules.oa.service import withdraw_self_oa_row

    return await withdraw_self_oa_row(session, row, note)
