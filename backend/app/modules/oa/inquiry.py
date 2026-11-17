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
from app.modules.oa.service import _map_status

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


def oa_template_fields() -> list[dict]:
    kind_map = {"text": "TextField", "choice": "DDSelectField", "date": "DDDateField",
                "attachment": "DDAttachment"}
    return [{**field, "type": kind_map.get(field["type"], field["type"]),
             "options": field.get("options") or []} for field in OA_FIELDS]


def _template_id() -> str:
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


async def _attachments(session: AsyncSession, user, inquiry: CustomInquiry, file_ids: list[int], *, images: bool = False, max_count: int = 30) -> list[dict]:
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
            raise AppError(ErrorCode.PARAM_ERROR, "产品参考图片与随单附件合计最多30个，请移除多余文件", 422)
        if any(row.mime_type not in {"image/png", "image/jpeg"} for row in rows):
            raise AppError(ErrorCode.PARAM_ERROR, "OA 产品参考图片只支持 PNG/JPEG，请转换后重新上传", 422)
        file_ids = [row.id for row in rows]
    by_id = {row.id: row for row in rows}
    if set(by_id) != set(file_ids):
        raise AppError(ErrorCode.PARAM_ERROR, "所选附件不属于这条询价，请重新选择", 422)
    uploaded = []
    for file_id in file_ids:
        record = by_id[file_id]
        path = absolute_path(record.object_key)
        if not path.is_file() or path.stat().st_size != record.size:
            raise AppError(ErrorCode.PARAM_ERROR, f"附件「{record.file_name}」缺失或不完整，请重新上传", 422)
        uploaded.append(await client.upload_file(
            user.dingtalk_userid, record.file_name, path.read_bytes(), record.mime_type
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
    if user is None or not user.dingtalk_userid:
        raise AppError(ErrorCode.PARAM_ERROR, "请先绑定本人钉钉账号，OA 才能按你名义发起", 422)
    template_id = _template_id()
    approver = settings.oa_inquiry_approver_dingtalk_id.strip()

    # Lock the business row as well: two first submissions have no approval row to lock.
    await session.get(CustomInquiry, inquiry.id, with_for_update=True, populate_existing=True)
    require_submittable(inquiry)
    latest = await latest_inquiry_row(session, inquiry.id, for_update=True)
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
            process_code=template_id, originator_user_id=user.dingtalk_userid,
            status="submitting", attempt_count=1, created_by=current_user.id,
            created_at=datetime.now(UTC), last_attempt_at=datetime.now(UTC),
        )
        session.add(row)
    await session.flush()
    try:
        extra = {str(k): str(v) for k, v in (getattr(payload, "extra_fields", None) or {}).items()}
        customer = await session.get(Customer, inquiry.customer_id) if inquiry.customer_id else None
        customer_name = customer.name if customer is not None else ""
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
            "customer_contact": extra.get("customer_contact") or customer_name,
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
        missing = [label for key, label in required if not str(fields.get(key) or "").strip()]
        if missing:
            raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "请补齐：" + "、".join(missing), 422)
        selected_files = list(getattr(payload, "attachment_file_ids", []) or [])
        image_ids = [str(item["id"]) for item in await _attachments(
            session, user, inquiry, [], images=True, max_count=30-len(set(selected_files)))]
        file_ids = [str(item["id"]) for item in await _attachments(session, user, inquiry, selected_files)]
        attachment_ids = list(dict.fromkeys(image_ids + file_ids))
        fields["DDPhotoField_1U7CNXCKQNS00"] = image_ids
        fields["attachments"] = file_ids
        payload_data = {
            "external_ref": external_ref,
            "applicant_dingtalk_user_id": user.dingtalk_userid,
            ("template_id" if settings.oa_inquiry_template_id.strip() else "template_code"): template_id,
            "title": inquiry.title,
            "reason": f"CRM 定制询价 {inquiry.inquiry_no or inquiry.id}",
            "amount": str(inquiry.target_price or 0),
            "fields": fields,
            "attachment_ids": attachment_ids,
        }
        if approver:
            payload_data["approver_dingtalk_user_id"] = approver
        row.form_snapshot = fields
        summary = await client.create_approval(payload_data)
    except client.OaError as exc:
        row.status = "failed"
        row.error = exc.message
        await session.commit()
        raise AppError(ErrorCode.EXTERNAL_ERROR, f"OA 发起失败：{exc.message}", 502) from exc
    except client.OaUnknownOutcome as exc:
        row.status = "needs_review"
        row.error = exc.message
        await session.commit()
        raise AppError(ErrorCode.EXTERNAL_ERROR, "OA 发起结果未知，请到 OA 核对后再处理", 502) from exc

    row.instance_id = str(summary["id"])
    row.status = _map_status(summary.get("status"))
    row.result = summary
    row.synced_at = datetime.now(UTC)
    row.updated_at = row.synced_at
    inquiry.extra = {**(inquiry.extra or {}), "oa_approval": {
        "provider": PROVIDER, "oa_instance_id": row.id, "instance_id": row.instance_id,
        "status": row.status, "synced_at": row.synced_at.isoformat(),
    }}
    await session.flush()
    return row, "已发起 OA 定制询价审批"


async def sync_inquiry_row(session: AsyncSession, row: OaInstance) -> OaInstance:
    summary = await client.get_approval(row.instance_id or "")
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
    summary = await client.withdraw_approval(row.instance_id or "", note)
    row.status = _map_status(summary.get("status"))
    row.result = summary
    row.synced_at = datetime.now(UTC)
    row.updated_at = row.synced_at
    row.error = None
    await session.flush()
    return row
