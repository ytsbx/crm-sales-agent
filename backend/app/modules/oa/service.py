"""合同审批：CRM → 自研 OA 的发起、同步、撤回。

只接手合同这一条链路；询价/打样仍走原有钉钉路径，直到后续单独切换。
所有新行 `oa_instances.provider='self_oa'`，老钉钉行保持只读。
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

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
) -> dict:
    template_id, template_code = _template_config()
    customer = await session.get(Customer, doc.customer_id)
    if customer is None or customer.deleted_at is not None:
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "合同所属客户不存在或已删除", 422)
    record, data = await _load_contract_file(session, doc)
    attachment = await client.upload_file(
        user.dingtalk_userid,
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
    fields: dict = {
        "contract_name": doc.title,
        "contract_number": doc.doc_no,
        "start_type": "上传文件发起",
        "contract_number_rule_type": "手动填写",
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
    # 钉钉模板「合同总额（元）」为必填：无金额时按 0.00 发送，避免留空被拒
    fields["contract_amount"] = f"{amount:.2f}" if amount is not None else "0.00"
    if doc.effective_date:
        fields["contract_period_start_date"] = doc.effective_date.isoformat()
    if doc.expiry_date:
        fields["contract_period_end_date"] = doc.expiry_date.isoformat()
    payload = {
        "external_ref": external_ref,
        "applicant_dingtalk_user_id": user.dingtalk_userid,
        "title": doc.title,
        "reason": f"合同 {doc.doc_no} 审批",
        "amount": f"{amount:.2f}" if amount is not None else "0",
        "fields": fields,
        "attachment_ids": [attachment_id],
    }
    if template_id:
        payload["template_id"] = template_id
    else:
        payload["template_code"] = template_code
    if settings.oa_contract_approver_dingtalk_id:
        payload["approver_dingtalk_user_id"] = settings.oa_contract_approver_dingtalk_id
    return payload


async def start_contract_approval(
    session: AsyncSession,
    current_user,
    doc: ContractDocument,
    *,
    our_company: str,
    counterparty_name: str,
) -> tuple[OaInstance, str]:
    """发起合同 OA 审批，返回 (行, 给用户看的结果说明)。"""
    from app.modules.user.model import User

    if not client.configured():
        raise AppError(ErrorCode.PARAM_ERROR, "还没配置自研 OA 对接，请联系管理员", 422)
    # `CurrentUser` 只带 id/权限/范围，不带钉钉编号；必须回表取本人身份。
    user = await session.get(User, current_user.id)
    if user is None or not user.dingtalk_userid:
        raise AppError(ErrorCode.PARAM_ERROR, "请先绑定本人钉钉账号，OA 才能按你名义发起", 422)
    _template_config()

    latest = await latest_contract_row(session, doc.id, for_update=True)
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
        external_ref = f"crm-contract:{doc.id}:{round_no}"
        row = OaInstance(
            customer_id=doc.customer_id,
            inquiry_id=doc.id,
            inquiry_version=1,
            oa_type=OA_TYPE,
            provider=PROVIDER,
            idempotency_key=external_ref,
            submit_round=round_no,
            process_code=settings.oa_contract_template_id or settings.oa_contract_template_code,
            originator_user_id=user.dingtalk_userid,
            status="submitting",
            attempt_count=1,
            created_by=current_user.id,
            created_at=datetime.now(UTC),
            last_attempt_at=datetime.now(UTC),
        )
        session.add(row)
        await session.flush()

    try:
        payload = await _build_payload(
            session, doc, user, external_ref,
            our_company=our_company, counterparty_name=counterparty_name,
        )
        row.form_snapshot = payload.get("fields")
        summary = await client.create_approval(payload)
    except client.OaNotConfigured as exc:
        row.status = "not_sent"
        row.error = exc.message
        await session.commit()
        raise AppError(ErrorCode.PARAM_ERROR, exc.message, 422) from exc
    except client.OaError as exc:
        row.status = "failed"
        row.error = exc.message
        await session.commit()
        raise AppError(ErrorCode.EXTERNAL_ERROR, f"OA 发起失败：{exc.message}", 502) from exc
    except client.OaUnknownOutcome as exc:
        row.status = "needs_review"
        row.error = exc.message
        await session.commit()
        raise AppError(
            ErrorCode.EXTERNAL_ERROR,
            "OA 发起结果未知（对方可能已建单），请到 OA 按合同编号核对后再处理",
            502,
        ) from exc

    row.instance_id = str(summary["id"])
    row.status = _map_status(summary.get("status"))
    row.result = summary
    row.error = None
    row.synced_at = datetime.now(UTC)
    row.updated_at = row.synced_at
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
    await session.flush()
    return row, "已发起 OA 合同审批"


async def sync_contract_row(session: AsyncSession, row: OaInstance) -> OaInstance:
    summary = await client.get_approval(row.instance_id or "")
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


async def withdraw_contract_row(session: AsyncSession, row: OaInstance, note: str) -> OaInstance:
    summary = await client.withdraw_approval(row.instance_id or "", note)
    row.status = _map_status(summary.get("status"))
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
    await session.flush()
    return row


async def contract_approval_approved(session: AsyncSession, doc_id: int) -> bool:
    row = await latest_contract_row(session, doc_id)
    return row is not None and row.status == "approved"
