"""客户原合同与钉钉现有审批关联；读取核验后才记录实际审批状态。"""
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import CurrentUser, require_permission
from app.core.errors import AppError, ErrorCode
from app.modules.contract import service as contracts
from app.modules.dingtalk import service as oa
from app.modules.dingtalk.client import get_client, DingTalkError, DingTalkUnknownOutcome
from app.modules.dingtalk.model import OaInstance
from app.modules.file.storage import absolute_path
from app.modules.settings.service import get_setting

router = APIRouter(tags=["Contract"])
# 2026-10-15 在当前组织只读核对，不能将用印申请模板替换为此流程。
CONTRACT_PROCESS = "PROC-23B3305F-7355-4460-B4BB-62E0B48E78C1"
CONTRACT_CORP = "dingb934b5416a9dfbcaacaaa37764f94726"


def fail(message, status=422):
    raise AppError(ErrorCode.PARAM_ERROR, message, status)


def verified_content(record):
    if record is None:
        fail("合同原文件不存在", 404)
    try:
        content = absolute_path(record.object_key).read_bytes()
    except (OSError, ValueError):
        fail("合同原文件不可读取，请恢复原文件", 410)
    if not content or len(content) != record.size or (record.checksum and hashlib.sha256(content).hexdigest() != record.checksum):
        fail("合同原文件校验失败，请恢复原文件", 410)
    return content


async def verify_original(session, user, file_id):
    from app.modules.file.access import can_access_file
    from app.modules.file.service import lock_file_row
    record = await lock_file_row(session, file_id)
    if record is None or not await can_access_file(session, user, file_id):
        fail("无权使用该合同文件", 403)
    if Path(record.file_name).suffix.lower() not in (".pdf", ".doc", ".docx"):
        fail("客户合同支持 PDF、Word 文件")
    verified_content(record)
    return record


def has_suite(value):
    if isinstance(value, str):
        try:
            return has_suite(json.loads(value))
        except (ValueError, TypeError):
            return False
    if isinstance(value, list):
        return any(has_suite(item) for item in value)
    if isinstance(value, dict):
        return value.get("componentName") == "DDBizSuite" or any(has_suite(item) for item in value.values() if isinstance(item, (dict, list))) or has_suite(value.get("schemaContent", ""))
    return False


async def reject_suite_submission(client, process_code):
    if process_code == CONTRACT_PROCESS or has_suite(await client.get_process_schema(process_code)):
        fail("该智能合同流程需在钉钉页面办理，请使用“钉钉合同审批”入口，提交后关联审批单")


async def visible_doc(session, user, doc_id, lock=False):
    from app.modules.contract.router import _ensure_doc_in_scope
    doc = await contracts.get_doc_or_404(session, doc_id, for_update=lock)
    await _ensure_doc_in_scope(session, user, doc)
    return doc


def status_of(data):
    state = str(data.get("status") or "").upper()
    result = str(data.get("result") or data.get("processInstanceResult") or "").lower()
    if state == "COMPLETED":
        return "approved" if result in ("agree", "approved", "pass") else "rejected" if result in ("refuse", "reject", "refused") else "needs_review"
    if state in ("TERMINATED", "CANCELED", "CANCELLED"):
        return "withdrawn"
    if state in ("RUNNING", "NEW"):
        return "pending"
    return "needs_review"


async def read_instance(instance_id):
    try:
        return await get_client().get_process_instance(instance_id)
    except (DingTalkError, DingTalkUnknownOutcome) as exc:
        raise AppError(ErrorCode.PARAM_ERROR, "无法确认钉钉审批状态，本次未完成操作；请稍后刷新核对", 502) from exc


async def config(session):
    cfg = await get_setting(session, "dingtalk_contract_oa") or {}
    return {**cfg, "process_code": cfg.get("process_code") or CONTRACT_PROCESS,
            "corp_id": cfg.get("corp_id") or CONTRACT_CORP}


@router.get("/contract-documents/{doc_id}/approval-entry")
async def approval_entry(doc_id: int, user: CurrentUser = Depends(require_permission("order:manage")), session: AsyncSession = Depends(get_db)):
    from app.core.response import ok
    doc = await visible_doc(session, user, doc_id)
    if doc.status != "draft":
        fail("仅待签合同可办理审批")
    # 合同审批沿用已验证的 CRM 直发路径：CRM 负责创建钉钉实例，审批过程仍在钉钉完成。
    # 不再把智能合同模板降级成“打开钉钉后手动粘贴实例号”的外部关联流程。
    mode = "api"
    if not settings.dingtalk_app_key:
        fail("未配置 CRM 钉钉应用，请联系管理员")
    return ok({"mode": mode, "url": None, "doc_no": doc.doc_no, "title": doc.title})


class LinkInput(BaseModel):
    instance_id: str = Field(min_length=1, max_length=2048)

    @field_validator("instance_id")
    @classmethod
    def parse_instance_link(cls, value):
        value = value.strip()
        if "://" in value:
            parsed = urlsplit(value)
            parts = parse_qs(parsed.query)
            value = (parts.get("processInstanceId") or parts.get("procInstId") or [""])[0]
        if not value or len(value) > 128 or any(c.isspace() for c in value):
            raise ValueError("未找到审批实例编号，请粘贴包含审批编号的钉钉详情链接或实例编号")
        return value


def verify_instance(data, *, cfg, doc, user_id):
    if data.get("processCode") != cfg["process_code"]:
        fail("该审批不属于当前合同审批流程")
    if (data.get("originatorUserId") or data.get("originatorUserid")) != user_id:
        fail("只能关联当前绑定钉钉账号发起的审批", 403)
    fields = data.get("formComponentValues") or data.get("formValueVOS") or []
    mapping = cfg.get("field_map") or {}
    number_id = mapping.get("contract_number") or "TextField_1VWK1245SNBQ80"
    title_id = mapping.get("title") or "TextField_23HBI7G786FO70G1"
    def value(component, names):
        for item in fields:
            if item.get("id") == component or item.get("componentId") == component or item.get("name") in names or item.get("bizAlias") in names:
                return str(item.get("value") or "").strip()
        return ""
    if value(number_id, ("合同编号", "contract_number")) != doc.doc_no:
        fail(f"审批中的合同编号必须是 {doc.doc_no}；请在钉钉选择手动填写编号")
    if value(title_id, ("合同名称", "contract_name")) != doc.title:
        fail("钉钉合同名称与 CRM 不一致，请核对后再关联")
    attachments = [f for f in fields if f.get("componentType") == "DDAttachment" or f.get("componentName") == "DDAttachment" or f.get("name") in ("合同正文", "上传合同文件") or f.get("id") == "AttachmentField_8LHIG786FO70G14"]
    def has_file(field):
        value = field.get("value")
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (ValueError, TypeError):
                return False
        return isinstance(value, list) and any(isinstance(item, dict) and item.get("fileId") for item in value)
    if not any(has_file(f) for f in attachments):
        fail("钉钉审批没有合同正文附件，请先上传客户原合同")


async def store_snapshot(session, doc, row, data):
    row.status = status_of(data)
    row.result = {"raw_status": data.get("status"), "raw_result": data.get("result"), "payload": data}
    row.synced_at = datetime.now(UTC)
    row.updated_at = row.synced_at
    doc.filled_data = {**(doc.filled_data or {}), "_dingtalk_oa": {
        "oa_instance_id": row.id, "instance_id": row.instance_id, "status": row.status,
        "synced_at": row.synced_at.isoformat(), "result": row.result,
    }}
    marker = f"{row.id}:{row.status}"
    if row.status in ("approved", "rejected") and (doc.filled_data or {}).get("_oa_notified") != marker:
        await oa._notify_contract_review_outcome(session, doc, row.status)
        doc.filled_data = {**doc.filled_data, "_oa_notified": marker}


async def associate_instance(session, user, doc, instance_id, cfg, actor):
    if (await session.execute(select(OaInstance.id).where(OaInstance.instance_id == instance_id))).first():
        fail("该审批已关联其他业务，不能重复关联", 409)
    data = await read_instance(instance_id)
    verify_instance(data, cfg=cfg, doc=doc, user_id=actor.dingtalk_userid)
    latest = await oa.get_by_business_key(session, inquiry_id=doc.id, inquiry_version=1, oa_type="contract")
    row = OaInstance(customer_id=doc.customer_id, inquiry_id=doc.id, inquiry_version=1, oa_type="contract",
        idempotency_key=f"contract:auto-link:{doc.id}:{hashlib.sha256(instance_id.encode()).hexdigest()}",
        submit_round=(latest.submit_round + 1 if latest else 1), process_code=cfg["process_code"],
        originator_user_id=actor.dingtalk_userid, instance_id=instance_id,
        form_snapshot={"mode":"external_auto_link","doc_no":doc.doc_no,"original_file_id":(doc.filled_data or {}).get("_original_file_id")},
        status="pending", created_by=user.id, created_at=datetime.now(UTC), updated_at=datetime.now(UTC))
    session.add(row)
    try: await session.flush()
    except IntegrityError as exc: raise AppError(ErrorCode.PARAM_ERROR,"审批已被其他请求关联，请刷新",409) from exc
    await store_snapshot(session, doc, row, data)
    return row


@router.post("/contract-documents/{doc_id}/approval-auto-link")
async def auto_link_approval(doc_id: int, user: CurrentUser = Depends(require_permission("order:manage")), session: AsyncSession = Depends(get_db)):
    from app.core.response import ok
    from app.modules.user.model import User
    from app.core.audit import write_audit
    doc = await visible_doc(session, user, doc_id, lock=True)
    if doc.status != "draft": fail("仅待签合同可以关联审批")
    actor = await session.get(User, user.id)
    if not actor or not actor.dingtalk_userid: fail("请先绑定本人钉钉账号")
    if settings.dingtalk_push_off: fail("钉钉外部调用已关闭，无法自动查找审批")
    cfg = await config(session)
    latest = await oa.get_by_business_key(session, inquiry_id=doc.id, inquiry_version=1, oa_type="contract")
    if latest and latest.status not in ("rejected", "withdrawn", "failed", "not_sent", "skipped"):
        return ok(oa.serialize(latest), "合同已有审批记录")
    now = datetime.now(UTC); start = int((now - timedelta(days=2)).timestamp() * 1000); end = int((now + timedelta(minutes=2)).timestamp() * 1000)
    ids = await get_client().list_process_instance_ids(process_code=cfg["process_code"], originator_user_id=actor.dingtalk_userid, start_time_ms=start, end_time_ms=end, size=20)
    matches=[]
    for instance_id in ids:
        try:
            data=await read_instance(instance_id); verify_instance(data,cfg=cfg,doc=doc,user_id=actor.dingtalk_userid); matches.append(instance_id)
        except AppError:
            continue
    if len(matches)==0: fail("暂未找到匹配的钉钉审批，请确认已提交；系统会继续自动查找")
    if len(matches)>1: fail("找到多条可能的钉钉审批，请暂时选择要关联的审批",409)
    row=await associate_instance(session,user,doc,matches[0],cfg,actor)
    await write_audit(session,operator_id=user.id,action="start_contract_oa",business_type="contract",business_id=doc.id,after={"instance_id":row.instance_id,"mode":"external_auto_link"})
    await session.commit(); return ok(oa.serialize(row),"已自动找到并关联钉钉审批")

@router.post("/contract-documents/{doc_id}/approval-link")
async def link_approval(doc_id: int, payload: LinkInput, user: CurrentUser = Depends(require_permission("order:manage")), session: AsyncSession = Depends(get_db)):
    from app.core.response import ok
    from app.modules.user.model import User
    from app.core.audit import write_audit
    doc = await visible_doc(session, user, doc_id, lock=True)
    if doc.status != "draft":
        fail("仅待签合同可以关联审批")
    actor = await session.get(User, user.id)
    if not actor or not actor.dingtalk_userid:
        fail("请先绑定本人钉钉账号，不能按姓名猜测发起人")
    if settings.dingtalk_push_off:
        fail("钉钉外部调用已关闭，无法核对审批")
    cfg = await config(session)
    latest = await oa.get_by_business_key(session, inquiry_id=doc.id, inquiry_version=1, oa_type="contract")
    if latest and latest.instance_id == payload.instance_id:
        return ok(oa.serialize(latest), "该审批已关联")
    if latest and latest.status not in ("rejected", "withdrawn", "failed", "not_sent", "skipped"):
        fail("当前合同已有在办或结果未明的审批，请先核对现有审批")
    if (await session.execute(select(OaInstance.id).where(OaInstance.instance_id == payload.instance_id))).first():
        fail("该审批已关联其他业务，不能重复关联", 409)
    data = await read_instance(payload.instance_id)
    verify_instance(data, cfg=cfg, doc=doc, user_id=actor.dingtalk_userid)
    row = OaInstance(customer_id=doc.customer_id, inquiry_id=doc.id, inquiry_version=1, oa_type="contract",
                     idempotency_key=f"contract:link:{doc.id}:{hashlib.sha256(payload.instance_id.encode()).hexdigest()}", submit_round=(latest.submit_round + 1 if latest else 1),
                     process_code=cfg["process_code"], originator_user_id=actor.dingtalk_userid, instance_id=payload.instance_id,
                     form_snapshot={"mode": "external_link", "doc_no": doc.doc_no, "original_file_id": (doc.filled_data or {}).get("_original_file_id")},
                     status="pending", created_by=user.id, created_at=datetime.now(UTC), updated_at=datetime.now(UTC))
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise AppError(ErrorCode.PARAM_ERROR, "审批已被其他请求关联，请刷新", 409) from exc
    await store_snapshot(session, doc, row, data)
    await write_audit(session, operator_id=user.id, action="start_contract_oa", business_type="contract", business_id=doc.id,
                      after={"instance_id": row.instance_id, "mode": "external_link"})
    await session.commit()
    return ok(oa.serialize(row), "已核验并关联钉钉审批")


@router.post("/contract-documents/{doc_id}/approval-sync")
async def sync_approval(doc_id: int, user: CurrentUser = Depends(require_permission("order:manage")), session: AsyncSession = Depends(get_db)):
    from app.core.response import ok
    doc = await visible_doc(session, user, doc_id, lock=True)
    row = await oa.get_by_business_key(session, inquiry_id=doc.id, inquiry_version=1, oa_type="contract")
    if row is None or not row.instance_id:
        fail("尚未关联钉钉审批")
    if settings.dingtalk_push_off:
        fail("钉钉外部调用已关闭")
    data = await read_instance(row.instance_id)
    await store_snapshot(session, doc, row, data)
    await session.commit()
    return ok(oa.serialize(row), "已同步钉钉审批")


async def withdraw_before_void(session, user, doc):
    row = await oa.get_by_business_key(session, inquiry_id=doc.id, inquiry_version=1, oa_type="contract")
    if row is None or row.status in ("approved", "rejected", "withdrawn", "skipped", "not_sent", "failed"):
        return
    if row.status != "pending" or not row.instance_id:
        fail("钉钉申请结果未明，请先核对审批，不能直接作废合同")
    if settings.dingtalk_push_off:
        fail("钉钉调用关闭，无法撤回在办审批；本次未作废合同")
    from app.modules.user.model import User
    actor = await session.get(User, user.id)
    if not actor or not actor.dingtalk_userid or actor.dingtalk_userid != row.originator_user_id:
        fail("请由该钉钉审批的发起人先撤回，确认后再作废合同", 403)
    # 先看真实状态：刚完成的审批不能再撤回，也不能假报已撤回。
    data = await read_instance(row.instance_id)
    if status_of(data) != "pending":
        await store_snapshot(session, doc, row, data)
        if row.status == "needs_review":
            fail("钉钉状态无法确认，本次未作废")
        return
    try:
        await get_client().terminate_process_instance(instance_id=row.instance_id, operating_user_id=actor.dingtalk_userid, remark="CRM 作废合同并撤回申请")
    except (DingTalkError, DingTalkUnknownOutcome) as exc:
        raise AppError(ErrorCode.PARAM_ERROR, "钉钉未确认撤回结果，本次未作废合同；请刷新审批进度核对，勿重复建单", 502) from exc
    # 只以回读结果确认撤回，超时/仍在办时保留本地状态让人继续核对。
    data = await read_instance(row.instance_id)
    if status_of(data) != "withdrawn":
        fail("钉钉尚未确认撤回，本次未作废合同；请刷新核对")
    await store_snapshot(session, doc, row, data)


async def remind_countersigns(session):
    from app.modules.contract.model import ContractDocument
    from app.modules.task.model import Task
    from app.core.timebase import today_business
    from app.modules.notification.service import notify
    day = today_business().isoformat()
    now = datetime.now(UTC)
    docs = (await session.execute(select(ContractDocument).where(ContractDocument.status == "draft", ContractDocument.deleted_at.is_(None)).with_for_update(skip_locked=True))).scalars().all()
    count = 0
    for doc in docs:
        meta = doc.filled_data or {}
        if meta.get("_source_type") != "customer_drafted" or meta.get("_return_reminded_day") == day:
            continue
        task = (await session.execute(select(Task).where(Task.source_key == f"contract:signed-upload:{doc.id}", Task.status.in_(("pending", "doing")), Task.due_at <= now))).scalars().first()
        if task is None:
            continue
        await notify(session, user_id=task.owner_id, type_="system", title=f"合同 {doc.doc_no} 回签逾期",
                     content="请跟进客户回签，并上传双方签好的合同归档；归档后待办自动结束。", business_type="contract", business_id=doc.id)
        doc.filled_data = {**meta, "_return_reminded_day": day}
        count += 1
    return count


async def named_history(session, meta):
    """只使用已绑定的 CRM 姓名补充审批显示，保留原始钉钉快照。"""
    import copy
    from app.modules.user.model import User
    output = copy.deepcopy(meta)
    payload = ((output or {}).get("result") or {}).get("payload") or {}
    entries = [item for key in ("tasks", "operationRecords") for item in (payload.get(key) or []) if isinstance(item, dict)]
    ids = {str(item["userId"]) for item in entries if item.get("userId")}
    if ids:
        users = (await session.execute(select(User.dingtalk_userid, User.name).where(User.dingtalk_userid.in_(ids)))).all()
        names = dict(users)
        for item in entries:
            if item.get("userId") in names and not item.get("userName"):
                item["userName"] = names[item["userId"]]
    return output


class BasisInput(BaseModel):
    order_id: int | None = Field(default=None, gt=0)
    quote_id: int | None = Field(default=None, gt=0)


@router.post("/contract-documents/{doc_id}/basis")
async def bind_basis(doc_id: int, payload: BasisInput, user: CurrentUser = Depends(require_permission("order:manage")), session: AsyncSession = Depends(get_db)):
    from app.core.response import ok
    from app.core.data_scope import ensure_in_scope
    from app.core.audit import write_audit
    from app.modules.order.model import SalesOrder
    from app.modules.quote.model import Quote, QuoteVersion
    doc = await visible_doc(session, user, doc_id, lock=True)
    if doc.status != "draft" or not (doc.filled_data or {}).get("_original_file_id"):
        fail("仅待签客户原稿合同可补充业务依据")
    if doc.order_id or doc.quote_id:
        fail("已有业务依据不能覆盖，请核对原合同")
    if bool(payload.order_id) == bool(payload.quote_id):
        fail("请选择一个正式订单或报价作为依据")
    order = await session.get(SalesOrder, payload.order_id) if payload.order_id else None
    quote = await session.get(Quote, payload.quote_id) if payload.quote_id else None
    source = order or quote
    if source is None or source.customer_id != doc.customer_id or getattr(source, "deleted_at", None):
        fail("该业务依据不存在或不属于本合同客户")
    await ensure_in_scope(session, user, owner_id=source.owner_id, label="合同业务依据")
    version_id = order.quote_version_id if order else quote.current_version_id
    version = await session.get(QuoteVersion, version_id) if version_id else None
    doc.order_id = payload.order_id
    doc.quote_id = (order.quote_id if order else quote.id)
    doc.quote_version_id = version_id
    await contracts._ensure_signable_source(session, doc)
    from app.modules.customer.model import Customer
    customer = await session.get(Customer, doc.customer_id)
    doc.filled_data = {**(doc.filled_data or {}), "_header": await contracts._header_snapshot(session, customer=customer, order=order, quote=quote, quote_version=version)}
    await write_audit(session, operator_id=user.id, action="update", business_type="contract", business_id=doc.id,
                      after={"order_id": doc.order_id, "quote_id": doc.quote_id, "quote_version_id": version_id})
    await session.commit()
    return ok(contracts.serialize_document(doc), "已补充正式业务依据")
