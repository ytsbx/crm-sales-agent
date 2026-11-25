"""合同审批：CRM → 自研 OA 的发起、同步、撤回。

只接手合同这一条链路；询价/打样仍走原有钉钉路径，直到后续单独切换。
所有新行 `oa_instances.provider='self_oa'`，老钉钉行保持只读。
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import AppError, ErrorCode
from app.modules.contract.model import ContractDocument
from app.modules.customer.model import Customer
from app.modules.dingtalk.model import OaInstance
from app.modules.file.model import FileRecord
from app.modules.file.storage import absolute_path
from app.modules.oa import client

PROVIDER = "self_oa"
OA_TYPE = "contract"


class ApprovalUploads:
    def __init__(self):
        self.files = {}

    async def stage(self, owner, name, content, mime=None):
        key = f"crm-staged-{uuid4().hex}"
        self.files[key] = (owner, name, content, mime)
        return {"id": key}

    async def fill(self, payload):
        ids = {}
        for key, (owner, name, content, mime) in self.files.items():
            try:
                data = content.read_bytes() if isinstance(content, Path) else content
            except OSError as exc:
                raise client.OaError(f"无法读取附件「{name}」，请重新上传") from exc
            uploaded = await client.upload_file(owner, name, data, mime)
            if not isinstance(uploaded, dict) or not uploaded.get("id"):
                raise client.OaError(f"附件「{name}」未返回上传编号，请稍后重试")
            ids[key] = str(uploaded["id"])

        def replace(value):
            if isinstance(value, dict):
                return {key: replace(item) for key, item in value.items()}
            if isinstance(value, list):
                return [replace(item) for item in value]
            return ids.get(value, value) if isinstance(value, str) else value

        return replace(payload)


async def send_prepared_approval(session, business, row, payload, uploads: ApprovalUploads):
    """Commit the reservation before crossing the network. Reacquire locks before applying the receipt."""
    model, business_id, row_id, attempt = type(business), business.id, row.id, row.attempt_count
    business_status = business.status
    superseded_at = getattr(business, "superseded_at", None)
    await session.commit()

    async def lock_receipt():
        fresh_business = await session.get(model, business_id, with_for_update=True, populate_existing=True)
        current = await session.get(OaInstance, row_id, with_for_update=True, populate_existing=True)
        if current is None or current.status != "submitting" or current.attempt_count != attempt:
            raise AppError(ErrorCode.VERSION_CONFLICT, "审批发起记录已变化，请到 OA 核对结果", 409)
        return fresh_business, current

    sending = False
    try:
        payload = await uploads.fill(payload)
        fresh_business, current = await lock_receipt()
        if (fresh_business is None or fresh_business.deleted_at is not None
                or fresh_business.status != business_status
                or getattr(fresh_business, "superseded_at", None) != superseded_at):
            current.status = "not_sent"
            current.error = "上传期间原单状态已变化，本次未发起 OA"
            await session.commit()
            raise AppError(ErrorCode.STATUS_NOT_ALLOWED, current.error, 409)
        current.form_snapshot = payload["fields"]
        current.last_attempt_at = datetime.now(UTC)
        await session.commit()
        sending = True
        summary = await client.create_approval(payload)
        if not isinstance(summary, dict) or not summary.get("id"):
            raise client.OaUnknownOutcome("OA 未返回审批单编号")
        if summary.get("external_ref") not in (None, payload["external_ref"]):
            raise client.OaUnknownOutcome("OA 返回的审批单与本轮编号不匹配")
    except client.OaError as exc:
        _, current = await lock_receipt()
        unknown = sending and isinstance(exc, client.OaUnknownOutcome)
        current.status = "needs_review" if unknown else "not_sent" if isinstance(exc, client.OaNotConfigured) else "failed"
        current.error = exc.message
        await session.commit()
        if unknown:
            raise AppError(ErrorCode.EXTERNAL_ERROR, "OA 发起结果未知，请到 OA 核对后再处理", 502) from exc
        if isinstance(exc, client.OaNotConfigured):
            raise AppError(ErrorCode.PARAM_ERROR, exc.message, 422) from exc
        raise AppError(ErrorCode.EXTERNAL_ERROR, f"OA 发起失败：{exc.message}", 502) from exc
    fresh_business, current = await lock_receipt()
    return fresh_business, current, summary


async def review_stuck_submission(session, row):
    from app.modules.dingtalk.service import demote_stuck_submitting

    if row is not None and demote_stuck_submitting(row):
        await session.commit()
        return True
    return False


async def find_receipt_for_adoption(row: OaInstance, instance_id: str | None) -> dict:
    try:
        instance = str(UUID((instance_id or "").strip()))
    except ValueError as exc:
        raise AppError(ErrorCode.PARAM_ERROR, "请填写同序 OA 审批编号", 422) from exc
    try:
        summary = await client.get_approval(instance)
    except client.OaError as exc:
        raise AppError(ErrorCode.EXTERNAL_ERROR, "暂时无法核对同序 OA 审批，请稍后重试", 502) from exc
    if (not isinstance(summary, dict) or summary.get("external_ref") != row.idempotency_key
            or str(summary.get("id")) != instance):
        raise AppError(ErrorCode.PARAM_ERROR, "该同序 OA 审批与这轮发起记录不匹配", 422)
    return summary


async def apply_adopted_receipt(session, row: OaInstance, summary: dict):
    from app.modules.dingtalk.service import get_by_business_key

    latest = await get_by_business_key(
        session, inquiry_id=row.inquiry_id, inquiry_version=row.inquiry_version, oa_type=row.oa_type,
    )
    if latest is not None and latest.id != row.id:
        row.status = _map_status(summary.get("status"))
        row.result = summary
        row.synced_at = row.updated_at = datetime.now(UTC)
        row.error = None
        await session.flush()
        return row
    if row.oa_type == "sample":
        from app.modules.oa.sample import _apply_sample_summary

        return await _apply_sample_summary(session, row, summary)
    if row.oa_type == "inquiry":
        from app.modules.oa.inquiry import apply_inquiry_summary

        return await apply_inquiry_summary(session, row, summary)
    return await apply_contract_summary(session, row, summary)

#: OA 状态 → CRM 口径（与钉钉那套翻译只在这里发生，前端只认 CRM 的五个词）。
_STATUS_MAP = {
    "draft": "pending",
    "pending": "pending",
    "signing": "pending",
    "parallel": "pending",
    "returned": "pending",
    "filling": "pending",
    "approved": "approved",
    "rejected": "rejected",
    "stopped": "rejected",
    "withdrawn": "withdrawn",
}

#: 这些状态说明“上一轮已经结束”，可以开新一轮；其余在办/终态不允许重复发起。
_RESTARTABLE = {"rejected", "withdrawn", "failed", "not_sent", "skipped"}


def _map_status(value: str | None) -> str:
    return _STATUS_MAP.get(str(value or "").lower(), "pending")


def _template_config() -> tuple[str, str]:
    template_id = settings.oa_contract_template_id.strip()
    template_code = settings.oa_contract_template_code.strip()
    if not template_id and not template_code:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            "还没配置合同 OA 审批模板（OA_CONTRACT_TEMPLATE_ID 或 OA_CONTRACT_TEMPLATE_CODE）",
            422,
        )
    return template_id, template_code


async def latest_contract_row(
    session: AsyncSession, doc_id: int, *, for_update: bool = False
) -> OaInstance | None:
    stmt = (
        select(OaInstance)
        .where(OaInstance.oa_type == OA_TYPE, OaInstance.inquiry_id == doc_id)
        .order_by(OaInstance.submit_round.desc(), OaInstance.id.desc())
        .limit(1)
    )
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return (await session.execute(stmt)).scalars().first()


def serialize(row: OaInstance) -> dict:
    from app.modules.dingtalk.service import serialize as serialize_row

    return {**serialize_row(row), "provider": getattr(row, "provider", "dingtalk")}


async def _contract_amount(session: AsyncSession, doc: ContractDocument) -> Decimal | None:
    from app.modules.order.model import SalesOrder
    from app.modules.quote.model import QuoteVersion

    if doc.order_id:
        order = await session.get(SalesOrder, doc.order_id)
        if order is not None and order.total_amount is not None:
            return Decimal(order.total_amount)
    if doc.quote_version_id:
        version = await session.get(QuoteVersion, doc.quote_version_id)
        if version is not None and version.total_amount is not None:
            return Decimal(version.total_amount)
    return None


async def _load_contract_file(session: AsyncSession, doc: ContractDocument) -> tuple[FileRecord, bytes]:
    file_id = (doc.filled_data or {}).get("_original_file_id") or doc.generated_file_id
    if not file_id:
        raise AppError(
            ErrorCode.REQUIRED_FIELD_MISSING,
            "这份合同还没有正文文件，请先生成合同或上传客户原合同",
            422,
        )
    record = await session.get(FileRecord, int(file_id))
    if record is None:
        raise AppError(ErrorCode.NOT_FOUND, "合同正文文件不存在，请重新上传或生成", 422)
    try:
        data = absolute_path(record.object_key).read_bytes()
    except OSError as exc:
        raise AppError(ErrorCode.EXTERNAL_ERROR, "读取合同正文失败，请稍后重试", 502) from exc
    return record, data


async def _build_payload(
    session: AsyncSession,
    doc: ContractDocument,
    user,
    external_ref: str,
    *,
    our_company: str,
    counterparty_name: str,
    extra_fields: dict | None = None,
    uploads: ApprovalUploads | None = None,
    prepared_template: dict | None = None,
) -> dict:
    template_id, template_code = _template_config()
    customer = await session.get(Customer, doc.customer_id)
    if customer is None or customer.deleted_at is not None:
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "合同所属客户不存在或已删除", 422)
    record, data = await _load_contract_file(session, doc)
    upload = uploads.stage if uploads is not None else client.upload_file
    attachment = await upload(
        client.applicant_identity(user),
        record.file_name,
        data,
        record.mime_type,
    )
    attachment_id = str(attachment["id"])
    amount = await _contract_amount(session, doc)
    header = (doc.filled_data or {}).get("_header") or {}
    # 主体由经办人在 CRM 弹窗里填写，这里只做非空校验和写值；不再从
    # `our_company`/客户名自动推断（2026-11-13 用户明确要求“由人填”）。
    our_company = our_company.strip()
    counterparty_name = counterparty_name.strip()
    if not our_company or not counterparty_name:
        raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "请填写我方主体和对方主体", 422)
    description = "\n".join(
        item for item in [
            f"我方主体：{our_company}",
            f"对方主体：{counterparty_name}",
            f"合同编号：{doc.doc_no}",
            "合同来源：" + {"company": "我方合同", "customer": "客户合同"}.get(
                (doc.filled_data or {}).get("_contract_origin"), "历史未标注"
            ),
            f"订单：{header.get('order_no')}" if header.get("order_no") else "",
            f"报价：{header.get('quote_no')}" if header.get("quote_no") else "",
            "合同正文：",
            doc.content_snapshot,
        ] if item
    )
    from app.modules.oa.inquiry import _department_name
    from app.modules.oa.contract_form import SOURCE_CODE, active_template
    template = prepared_template
    if template is None:
        try:
            template = await active_template(user)
        except AppError:
            # 读不到模板不是归属部门的问题：后面 create_approval 会给出准确报错。
            template = None
    applicant = (template or {}).get("applicant") or {}
    fields: dict = {
        "applicant_name": applicant.get("name") or user.name,
        # 归属部门以同序申请人资料为准，CRM 部门表缺项时不再把空值发过去。
        "department": applicant.get("department_name") or await _department_name(session, user),
        "contract_name": doc.title,
        # 合同编号：手动模式用经办人填的（没填用 CRM 编号）；规则模式由 OA 按规则生成，不回传编号
        "contract_number": ((doc.filled_data or {}).get("_contract_number") or doc.doc_no)
            if (doc.filled_data or {}).get("_contract_number_rule_type") != "rule" else "",
        "start_type": "上传文件发起",
        "contract_number_rule_type": ("选择已有合同编号规则"
            if (doc.filled_data or {}).get("_contract_number_rule_type") == "rule" else "手动填写"),
        "contract_amount_type": "无金额" if amount is None else "收入",
        "contract_term_type": "固定期限" if doc.expiry_date else "无固定期限",
        "contract_description": description,
        "contract_classify_cascade": "月结协议" if doc.doc_type == "monthly" else "销售合同",
        "contract_sign_mode": "纸质签署",
        "contract_sign_note": "由 CRM 发起，审批过程在 OA 完成",
        "contract_files": [attachment_id],
        "contract_mine_info": [{"mine_company_name": our_company}],
        "contract_opposite_info": [{"opposite_company_name": counterparty_name}],
    }
    if template and template.get("fields") and template.get("code") != SOURCE_CODE:
        declared_fields = {field["id"] for field in template["fields"]}
        for key in ("applicant_name", "department"):
            if key not in declared_fields:
                fields.pop(key)
    # The supplied OA template has no 月结协议 option; file category is optional.
    if doc.doc_type == 'monthly' and (template or {}).get('code', template_code) == SOURCE_CODE:
        fields.pop("contract_classify_cascade")
    # 钉钉模板「合同总额（元）」为必填：无金额时按 0.00 发送，避免留空被拒
    fields["contract_amount"] = f"{amount:.2f}" if amount is not None else "0.00"
    # 「合同编号规则」：选择已有规则时发规则名（智能合同目前只有“系统默认规则”）
    if (doc.filled_data or {}).get("_contract_number_rule_type") == "rule":
        fields["contract_number_rule_fix"] = ((doc.filled_data or {}).get("_contract_number_rule_fix")
                                              or "系统默认规则")
    if doc.effective_date:
        fields["contract_period_start_date"] = doc.effective_date.isoformat()
    if doc.expiry_date:
        fields["contract_period_end_date"] = doc.expiry_date.isoformat()
    payload = {
        "external_ref": external_ref,
        **client.applicant_fields(client.applicant_identity(user)),
        "title": doc.title,
        "reason": f"合同 {doc.doc_no} 审批",
        "amount": f"{amount:.2f}" if amount is not None else "0",
        "fields": fields,
        "attachment_ids": [attachment_id],
    }
    effective_extra = {
        **((doc.filled_data or {}).get("_oa_fields") or {}),
        **(extra_fields or {}),
    }
    if effective_extra:
        from app.modules.oa.contract_form import apply_extra_fields
        await apply_extra_fields(
            session, user, doc, payload, effective_extra, uploads=uploads, template=prepared_template,
        )
    if template_id:
        payload["template_id"] = template_id
    else:
        payload["template_code"] = template_code
    return payload


async def start_contract_approval(
    session: AsyncSession,
    current_user,
    doc: ContractDocument,
    *,
    our_company: str,
    counterparty_name: str,
    extra_fields: dict | None = None,
    prepared_template: dict | None = None,
) -> tuple[OaInstance, str]:
    """发起合同 OA 审批，返回 (行, 给用户看的结果说明)。"""
    from app.modules.user.model import User

    if not client.configured():
        raise AppError(ErrorCode.PARAM_ERROR, "还没配置自研 OA 对接，请联系管理员", 422)
    # 回表读取当前账号保存的同序身份。
    user = await session.get(User, current_user.id)
    identity = client.applicant_identity(user)
    if not identity:
        raise AppError(ErrorCode.PARAM_ERROR, "请联系管理员为当前账号绑定同序人员后再提交", 422)
    _template_config()

    latest = await latest_contract_row(session, doc.id, for_update=True)
    if await review_stuck_submission(session, latest):
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "上一次 OA 发起结果待核对，请先到 OA 核对", 409)
    if latest is not None and latest.status in {"pending", "submitting"}:
        return latest, "这份合同已有在办 OA 审批，请查看进度，不要重复发起"
    if latest is not None and latest.status == "needs_review":
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            "上一次 OA 发起结果待核对，请先按合同编号到 OA 核对",
            409,
        )
    if latest is not None and latest.status == "approved":
        return latest, "这份合同的 OA 审批已经通过，请直接上传客户回签文件"

    if latest is not None and latest.status in {"failed", "not_sent", "skipped"}:
        row = latest
        row.status = "submitting"
        row.error = None
        row.attempt_count = int(row.attempt_count or 0) + 1
        row.last_attempt_at = datetime.now(UTC)
        external_ref = row.idempotency_key
    else:
        round_no = (latest.submit_round + 1) if latest is not None else 1
        external_ref = client.approval_external_ref("contract", doc.id, round_no)
        row = OaInstance(
            customer_id=doc.customer_id,
            inquiry_id=doc.id,
            inquiry_version=1,
            oa_type=OA_TYPE,
            provider=PROVIDER,
            idempotency_key=external_ref,
            submit_round=round_no,
            process_code=settings.oa_contract_template_id or settings.oa_contract_template_code,
            originator_user_id=str(identity),
            status="submitting",
            attempt_count=1,
            created_by=current_user.id,
            created_at=datetime.now(UTC),
            last_attempt_at=datetime.now(UTC),
        )
        session.add(row)
        await session.flush()

    uploads = ApprovalUploads()
    payload = await _build_payload(
        session, doc, user, external_ref,
        our_company=our_company, counterparty_name=counterparty_name,
        extra_fields=extra_fields, uploads=uploads, prepared_template=prepared_template,
    )
    doc, row, summary = await send_prepared_approval(session, doc, row, payload, uploads)


    row.instance_id = str(summary["id"])
    row.status = _map_status(summary.get("status"))
    row.result = summary
    row.error = None
    row.synced_at = datetime.now(UTC)
    row.updated_at = row.synced_at
    if doc is not None and doc.deleted_at is None:
        doc.filled_data = {
            **(doc.filled_data or {}),
            "_oa_fields": {**(doc.filled_data or {}).get("_oa_fields", {}), **(extra_fields or {})},
            "_dingtalk_oa": {
                "provider": PROVIDER,
                "oa_instance_id": row.id,
                "instance_id": row.instance_id,
                "status": row.status,
                "synced_at": row.synced_at.isoformat(),
                "result": summary,
            },
        }
    await session.flush()
    return row, "已发起 OA 合同审批"


async def sync_contract_row(session: AsyncSession, row: OaInstance) -> OaInstance | None:
    return await sync_self_oa_row(session, row)


async def apply_contract_summary(session: AsyncSession, row: OaInstance, summary: dict) -> OaInstance:
    new_status = _map_status(summary.get("status"))
    row.status = new_status
    row.result = summary
    row.synced_at = datetime.now(UTC)
    row.updated_at = row.synced_at
    row.error = None
    doc = await session.get(ContractDocument, row.inquiry_id, with_for_update=True)
    if doc is not None:
        doc.filled_data = {
            **(doc.filled_data or {}),
            "_dingtalk_oa": {
                "provider": PROVIDER,
                "oa_instance_id": row.id,
                "instance_id": row.instance_id,
                "status": row.status,
                "synced_at": row.synced_at.isoformat(),
                "result": summary,
            },
        }
        marker = f"{row.id}:{row.status}"
        if row.status in {"approved", "rejected"} and (doc.filled_data or {}).get("_oa_notified") != marker:
            from app.modules.dingtalk.service import _notify_contract_review_outcome

            await _notify_contract_review_outcome(session, doc, row.status)
            doc.filled_data = {**doc.filled_data, "_oa_notified": marker}
    await session.flush()
    return row


async def withdraw_contract_row(session: AsyncSession, row: OaInstance, note: str, *, reload_business=None) -> OaInstance:
    return await withdraw_self_oa_row(session, row, note, reload_business=reload_business)


async def contract_approval_approved(session: AsyncSession, doc_id: int) -> bool:
    row = await latest_contract_row(session, doc_id)
    return row is not None and row.status == "approved"


async def sync_self_oa_row(session: AsyncSession, row: OaInstance) -> OaInstance | None:
    from app.modules.dingtalk.service import query_oa_without_locks, settle_withdrawal_snapshot

    current, summary, error = await query_oa_without_locks(session, row, client.get_approval)
    if current is None:
        return None
    if error:
        raise error
    status = _map_status(summary.get("status"))
    if not settle_withdrawal_snapshot(current, status):
        return current
    if current.status in {"approved", "rejected", "withdrawn"} and status == "pending":
        return current
    return await apply_adopted_receipt(session, current, summary)


async def withdraw_self_oa_row(session: AsyncSession, row: OaInstance, note: str, *, reload_business=None) -> OaInstance:
    from app.modules.dingtalk.service import withdraw_oa_without_locks

    async def send(instance_id):
        summary = await client.withdraw_approval(instance_id, note)
        if not isinstance(summary, dict) or _map_status(summary.get("status")) != "withdrawn":
            raise client.OaUnknownOutcome("同序 OA 未确认撤回")
        return summary

    current, summary = await withdraw_oa_without_locks(session, row, send, reload_business=reload_business)
    if summary is None:
        return current
    return await apply_adopted_receipt(session, current, summary)
