"""合同模板填充与文档台账的业务逻辑（§3.6/场景14）。"""

import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.modules.contract.model import (
    DOC_STATUS_LABEL,
    DOC_TYPE_LABEL,
    ContractDocument,
    ContractTemplate,
)
from app.modules.customer.model import Customer
from app.modules.order.model import SalesOrder
from app.modules.quote.model import Quote

#: 占位符：{{customer.name}} / {{order.order_no}} / {{extra.付款方式}} / {{today}}
_TOKEN = re.compile(r"\{\{([^{}]+)\}\}")


def _f(value) -> str:
    return "" if value is None else str(value)


def render_template(
    body: str,
    *,
    customer: Customer,
    order: SalesOrder | None = None,
    quote: Quote | None = None,
    extra_fields: dict[str, str] | None = None,
) -> tuple[str, dict]:
    """填充模板，返回（正文快照，填入值快照）。

    已知 token 按对象字段填；{{extra.*}} 取业务员本次填写的空白项；
    认不出的 token 原样保留——宁可让人看到"这里没填上"，
    也不要静默给一个空值还以为填好了。
    """
    extra = {str(k): str(v) for k, v in (extra_fields or {}).items()}
    filled: dict[str, str] = {}
    sources: dict[str, object] = {"customer": customer, "order": order, "quote": quote}

    def _replace(match: re.Match) -> str:
        token = match.group(1).strip()
        value = ""
        if token == "today":
            value = datetime.now(UTC).date().isoformat()
        elif token.startswith("extra."):
            value = extra.get(token[len("extra."):].strip(), "")
        else:
            prefix, _, field = token.partition(".")
            obj = sources.get(prefix.strip())
            if obj is not None and field:
                value = _f(getattr(obj, field.strip(), None))
        filled[token] = value
        return value

    content = _TOKEN.sub(_replace, body)
    return content, filled


def serialize_template(template: ContractTemplate, *, is_current: bool = False) -> dict:
    return {
        "id": template.id,
        "doc_type": template.doc_type,
        "doc_type_label": DOC_TYPE_LABEL.get(template.doc_type, template.doc_type),
        "name": template.name,
        "version": template.version,
        "body": template.body,
        "enabled": template.enabled,
        "is_current": is_current,
        "created_at": template.created_at,
    }


def serialize_document(doc: ContractDocument, *, customer_name: str | None = None) -> dict:
    return {
        "id": doc.id,
        "doc_no": doc.doc_no,
        "doc_type": doc.doc_type,
        "doc_type_label": DOC_TYPE_LABEL.get(doc.doc_type, doc.doc_type),
        "title": doc.title,
        "customer_id": doc.customer_id,
        "customer_name": customer_name,
        "order_id": doc.order_id,
        "quote_id": doc.quote_id,
        "template_id": doc.template_id,
        "content_snapshot": doc.content_snapshot,
        "filled_data": doc.filled_data,
        "status": doc.status,
        "status_label": DOC_STATUS_LABEL.get(doc.status, doc.status),
        "expiry_date": doc.expiry_date,
        "parent_id": doc.parent_id,
        "signed_at": doc.signed_at,
        "void_reason": doc.void_reason,
        "created_at": doc.created_at,
    }


async def create_template(session: AsyncSession, *, payload, user_id: int) -> ContractTemplate:
    """新增模板=新增版本：同名同类型自动 version+1，旧版本永远保留。"""
    template = ContractTemplate(
        doc_type=payload.doc_type if payload.doc_type in DOC_TYPE_LABEL else "contract",
        name=payload.name.strip(),
        version=1,
        body=payload.body,
        enabled=payload.enabled,
        created_by=user_id,
        created_at=datetime.now(UTC),
    )
    latest = (
        await session.execute(
            select(ContractTemplate)
            .where(
                ContractTemplate.doc_type == template.doc_type,
                ContractTemplate.name == template.name,
                ContractTemplate.deleted_at.is_(None),
            )
            .order_by(ContractTemplate.version.desc())
            .limit(1)
        )
    ).scalars().first()
    if latest is not None:
        template.version = latest.version + 1
    session.add(template)
    await session.flush()
    return template


async def list_templates(session: AsyncSession) -> list[dict]:
    rows = (
        await session.execute(
            select(ContractTemplate)
            .where(ContractTemplate.deleted_at.is_(None))
            .order_by(
                ContractTemplate.doc_type, ContractTemplate.name, ContractTemplate.version.desc()
            )
        )
    ).scalars().all()
    current_keys = {
        (t.doc_type, t.name): max(
            x.version for x in rows if x.doc_type == t.doc_type and x.name == t.name
        )
        for t in rows
    }
    return [
        serialize_template(t, is_current=current_keys.get((t.doc_type, t.name)) == t.version)
        for t in rows
    ]


async def generate_document(session: AsyncSession, *, payload, user_id: int) -> ContractDocument:
    """从模板版本生成草稿：填好的正文与填入值都落快照，之后改资料不影响它。"""
    template = await session.get(ContractTemplate, payload.template_id)
    if template is None or template.deleted_at is not None or not template.enabled:
        raise AppError(ErrorCode.NOT_FOUND, "模板不存在或已停用", 404)
    customer = await session.get(Customer, payload.customer_id)
    if customer is None or customer.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "客户不存在", 404)
    order = await session.get(SalesOrder, payload.order_id) if payload.order_id else None
    if payload.order_id and order is None:
        raise AppError(ErrorCode.NOT_FOUND, "订单不存在", 404)
    quote = await session.get(Quote, payload.quote_id) if payload.quote_id else None
    if payload.quote_id and quote is None:
        raise AppError(ErrorCode.NOT_FOUND, "报价单不存在", 404)

    content, filled = render_template(
        template.body,
        customer=customer,
        order=order,
        quote=quote,
        extra_fields=payload.extra_fields,
    )
    from app.modules.settings import numbering

    doc = ContractDocument(
        doc_no=await numbering.generate_for(
            session, "contract", model=ContractDocument, column=ContractDocument.doc_no
        ),
        doc_type=template.doc_type,
        title=payload.title
        or f"{DOC_TYPE_LABEL.get(template.doc_type, template.doc_type)}-{customer.name}",
        customer_id=customer.id,
        order_id=payload.order_id,
        quote_id=payload.quote_id,
        template_id=template.id,
        content_snapshot=content,
        filled_data=filled,
        expiry_date=payload.expiry_date,
        parent_id=payload.parent_id,
        created_by=user_id,
        created_at=datetime.now(UTC),
    )
    session.add(doc)
    await session.flush()
    return doc


async def get_doc_or_404(session: AsyncSession, doc_id: int) -> ContractDocument:
    doc = await session.get(ContractDocument, doc_id)
    if doc is None or doc.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "合同文档不存在", 404)
    return doc


async def sign_document(
    session: AsyncSession, doc: ContractDocument, *, file_id: int, note: str | None
) -> None:
    """登记签署：签署件按通用附件挂到文档上（business_type=contract，category=signed）。

    签的是扫描件（电子签章不是前置）；草稿状态与签署件在台账上永远分明。
    """
    from app.modules.file.model import BusinessFile, FileRecord

    if doc.status == "signed":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "该文档已登记签署")
    if doc.status == "void":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "已作废的文档不能签署")
    record = await session.get(FileRecord, file_id)
    if record is None:
        raise AppError(ErrorCode.NOT_FOUND, "文件不存在，请先通过 /files/upload 上传", 404)
    session.add(
        BusinessFile(
            business_type="contract",
            business_id=doc.id,
            file_id=file_id,
            category="signed",
        )
    )
    doc.status = "signed"
    doc.signed_at = datetime.now(UTC)
    if note:
        doc.filled_data = {**(doc.filled_data or {}), "sign_note": note}
    await session.flush()


async def void_document(session: AsyncSession, doc: ContractDocument, *, reason: str) -> None:
    if doc.status == "void":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "文档已经是作废状态")
    doc.status = "void"
    doc.void_reason = reason
    await session.flush()


async def list_documents(
    session: AsyncSession,
    *,
    owner_ids: list[int] | None,
    customer_id: int | None,
    status: str | None,
) -> list[dict]:
    """台账：数据范围跟客户负责人**当前**归属走——

    换负责人后新负责人按权限查看历史原件（场景14），不存过期 owner 快照。
    """
    stmt = (
        select(ContractDocument, Customer.name)
        .join(Customer, Customer.id == ContractDocument.customer_id)
        .where(ContractDocument.deleted_at.is_(None))
    )
    if customer_id:
        stmt = stmt.where(ContractDocument.customer_id == customer_id)
    if status:
        stmt = stmt.where(ContractDocument.status == status)
    if owner_ids is not None:
        stmt = stmt.where(Customer.owner_id.in_(owner_ids))
    rows = (
        await session.execute(stmt.order_by(ContractDocument.created_at.desc()).limit(500))
    ).all()
    return [serialize_document(doc, customer_name=name) for doc, name in rows]


async def notify_expiring_monthly(session: AsyncSession) -> int:
    """月结协议到期前提醒负责人（§3.6）。挂进每日自动任务，按标题去重。"""
    from app.modules.settings import service as settings_service
    from app.modules.task.model import Task

    days = int(await settings_service.get_number(session, "contract", "monthly_remind_days", 30))
    today = datetime.now(UTC).date()
    rows = (
        await session.execute(
            select(ContractDocument, Customer.owner_id)
            .join(Customer, Customer.id == ContractDocument.customer_id)
            .where(
                ContractDocument.doc_type == "monthly",
                ContractDocument.status == "signed",
                ContractDocument.deleted_at.is_(None),
                ContractDocument.expiry_date.is_not(None),
                ContractDocument.expiry_date >= today,
                ContractDocument.expiry_date <= today + timedelta(days=days),
            )
        )
    ).all()
    created = 0
    for doc, owner_id in rows:
        if owner_id is None:
            continue
        title = f"月结协议 {doc.doc_no} 将于 {doc.expiry_date} 到期"
        existing = (
            await session.execute(
                select(Task.id).where(Task.title == title, Task.status.in_(["pending", "doing"]))
            )
        ).scalar_one_or_none()
        if existing is not None:
            continue
        session.add(
            Task(
                title=title,
                task_type="followup",
                customer_id=doc.customer_id,
                owner_id=owner_id,
                priority="high",
                status="pending",
                due_at=datetime.now(UTC),
                source="system",
                source_rule_id=None,
            )
        )
        created += 1
    await session.flush()
    return created
