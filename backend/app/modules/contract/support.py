"""合同附件核验、续签提醒、审批显示名与业务依据补录。

2026-11-25 主人拍板：旧钉钉合同审批路径（关联/同步/自动认领）整体删除。
这里只保留现在仍有入口、且与审批供应商无关的能力：
- 合同原文件读取与校验（下载/签署/上传前都要过这一道）；
- 作废前撤回自研 OA 审批；
- 客户回签逾期的每日提醒；
- 审批记录里补全已绑定账号的姓名；
- 客户原稿合同补业务依据（订单/报价）。

旧代码里对钉钉实例的读取、状态解析、附件上传、发起审批全部不再保留。
"""

import copy
import hashlib
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import CurrentUser, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.timebase import today_business
from app.modules.contract import service as contracts
from app.modules.dingtalk import service as oa
from app.modules.file.storage import absolute_path

router = APIRouter(tags=["Contract"])


def fail(message, status=422):
    raise AppError(ErrorCode.PARAM_ERROR, message, status)


def verified_content(record):
    """读出合同文件字节并核对大小/校验值；不核对就下载等于把损坏件发给客户。"""
    if record is None:
        fail("合同原文件不存在", 404)
    try:
        content = absolute_path(record.object_key).read_bytes()
    except (OSError, ValueError):
        fail("合同原文件不可读取，请恢复原文件", 410)
    if (
        not content
        or len(content) != record.size
        or (record.checksum and hashlib.sha256(content).hexdigest() != record.checksum)
    ):
        fail("合同原文件校验失败，请恢复原文件", 410)
    return content


async def verify_original(session, user, file_id):
    """校验"客户原合同"文件：可访问、格式支持、字节未损坏。"""
    from app.modules.file.access import can_access_file
    from app.modules.file.service import lock_file_row

    record = await lock_file_row(session, file_id)
    if record is None or not await can_access_file(session, user, file_id):
        fail("无权使用该合同文件", 403)
    if Path(record.file_name).suffix.lower() not in (".pdf", ".doc", ".docx"):
        fail("客户合同支持 PDF、Word 文件")
    verified_content(record)
    return record


async def visible_doc(session, user, doc_id, lock=False):
    from app.modules.contract.router import _ensure_doc_in_scope

    doc = await contracts.get_doc_or_404(session, doc_id, for_update=lock)
    await _ensure_doc_in_scope(session, user, doc)
    return doc


async def withdraw_before_void(session, user, doc):
    """作废合同前先把自研 OA 里的在办审批撤回；撤回结果不明就不许作废。

    旧钉钉审批记录（provider 不是 self_oa）不再调用任何钉钉接口：
    项目未上线，不存在需要 CRM 自动撤回的在途钉钉单。
    """
    row = await oa.get_by_business_key(session, inquiry_id=doc.id, inquiry_version=1, oa_type="contract")
    if row is None or row.status in ("approved", "rejected", "withdrawn", "skipped", "not_sent", "failed"):
        return
    if row.status != "pending" or not row.instance_id:
        fail("OA 申请结果未明，请先核对审批，不能直接作废合同")
    if getattr(row, "provider", "dingtalk") != "self_oa":
        fail("这是历史钉钉审批记录；CRM 已不再对接钉钉审批，请核对原审批结果后再作废")
    from app.modules.oa.client import owns_approval
    from app.modules.user.model import User

    actor = await session.get(User, user.id)
    if not owns_approval(actor, row):
        fail("请由该 OA 审批的发起人先撤回，确认后再作废合同", 403)
    row = await oa.lock_oa_business_then_instance(session, row)
    if row is None or row.status != "pending":
        fail("审批记录正在处理或已变化，请刷新后重试", 409)
    doc_id = doc.id

    async def reload_business():
        return await visible_doc(session, user, doc_id, lock=True)

    from app.modules.oa import service as oa_service

    try:
        await oa_service.withdraw_contract_row(
            session,
            row,
            "CRM 作废合同并撤回申请",
            reload_business=reload_business,
        )
    except AppError as exc:
        if exc.http_status == 502:
            raise AppError(exc.code, f"{exc.message}，本次未作废合同", 502) from exc
        raise


async def remind_countersigns(session):
    """客户回签逾期每日提醒一次；归档后任务结束，自然不再提醒。"""
    from app.modules.contract.model import ContractDocument
    from app.modules.notification.service import notify
    from app.modules.task.model import Task

    day = today_business().isoformat()
    now = datetime.now(UTC)
    docs = (
        await session.execute(
            select(ContractDocument)
            .where(ContractDocument.status == "draft", ContractDocument.deleted_at.is_(None))
            .with_for_update(skip_locked=True)
        )
    ).scalars().all()
    count = 0
    for doc in docs:
        meta = doc.filled_data or {}
        if meta.get("_source_type") != "customer_drafted" or meta.get("_return_reminded_day") == day:
            continue
        task = (
            await session.execute(
                select(Task).where(
                    Task.source_key == f"contract:signed-upload:{doc.id}",
                    Task.status.in_(("pending", "doing")),
                    Task.due_at <= now,
                )
            )
        ).scalars().first()
        if task is None:
            continue
        await notify(
            session,
            user_id=task.owner_id,
            type_="system",
            title=f"合同 {doc.doc_no} 回签逾期",
            content="请跟进客户回签，并上传双方签好的合同归档；归档后待办自动结束。",
            business_type="contract",
            business_id=doc.id,
        )
        doc.filled_data = {**meta, "_return_reminded_day": day}
        count += 1
    return count


async def named_history(session, meta):
    """只使用已绑定的 CRM 姓名补充审批显示，保留原始快照。"""
    from app.modules.user.model import User

    output = copy.deepcopy(meta)
    payload = ((output or {}).get("result") or {}).get("payload") or {}
    entries = [
        item
        for key in ("tasks", "operationRecords")
        for item in (payload.get(key) or [])
        if isinstance(item, dict)
    ]
    ids = {str(item["userId"]) for item in entries if item.get("userId")}
    if ids:
        users = (
            await session.execute(select(User.dingtalk_userid, User.name).where(User.dingtalk_userid.in_(ids)))
        ).all()
        names = dict(users)
        for item in entries:
            if item.get("userId") in names and not item.get("userName"):
                item["userName"] = names[item["userId"]]
    return output


class BasisInput(BaseModel):
    order_id: int | None = Field(default=None, gt=0)
    quote_id: int | None = Field(default=None, gt=0)


@router.post("/contract-documents/{doc_id}/basis")
async def bind_basis(
    doc_id: int,
    payload: BasisInput,
    user: CurrentUser = Depends(require_permission("order:manage")),
    session: AsyncSession = Depends(get_db),
):
    from app.core.audit import write_audit
    from app.core.data_scope import ensure_in_scope
    from app.core.response import ok
    from app.modules.customer.model import Customer
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
    doc.quote_id = order.quote_id if order else quote.id
    doc.quote_version_id = version_id
    await contracts._ensure_signable_source(session, doc)
    customer = await session.get(Customer, doc.customer_id)
    doc.filled_data = {
        **(doc.filled_data or {}),
        "_header": await contracts._header_snapshot(
            session, customer=customer, order=order, quote=quote, quote_version=version
        ),
    }
    await write_audit(
        session,
        operator_id=user.id,
        action="update",
        business_type="contract",
        business_id=doc.id,
        after={"order_id": doc.order_id, "quote_id": doc.quote_id, "quote_version_id": version_id},
    )
    await session.commit()
    return ok(contracts.serialize_document(doc), "已补充正式业务依据")
