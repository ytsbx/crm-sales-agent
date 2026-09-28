"""合同/月结协议接口：模板维护（管理）、生成/签署/作废与台账（销售/主管）。"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.data_scope import ensure_in_scope, scoped_owner_ids
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.response import ok
from app.modules.contract import service as svc
from app.modules.contract.schema import DocumentCreate, DocumentSign, DocumentVoid, TemplateCreate

router = APIRouter(tags=["Contract"])


@router.get("/contract-templates")
async def list_templates(
    user: CurrentUser = Depends(require_permission("order:view")),
    session: AsyncSession = Depends(get_db),
):
    return ok(await svc.list_templates(session))


@router.post("/contract-templates")
async def create_template(
    payload: TemplateCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    """新增模板=新增版本；旧版本保留，已生成的文档钉死当时的版本。"""
    template = await svc.create_template(session, payload=payload, user_id=user.id)
    await write_audit(
        session,
        operator_id=user.id,
        action="create_template",
        business_type="contract_template",
        business_id=template.id,
        after={"name": template.name, "doc_type": template.doc_type, "version": template.version},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_template(template, is_current=True), "模板版本已保存")


@router.get("/contract-documents")
async def list_documents(
    customer_id: int | None = Query(None),
    status: str | None = Query(None),
    user: CurrentUser = Depends(require_permission("order:view")),
    session: AsyncSession = Depends(get_db),
):
    owner_ids = await scoped_owner_ids(session, user)
    return ok(
        await svc.list_documents(session, owner_ids=owner_ids, customer_id=customer_id, status=status)
    )


@router.post("/contract-documents")
async def generate_document(
    payload: DocumentCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("order:manage")),
    session: AsyncSession = Depends(get_db),
):
    from app.modules.customer import service as customer_service

    customer = await customer_service.get_visible_customer(session, user, payload.customer_id)
    await ensure_in_scope(session, user, owner_id=customer.owner_id, label="客户")
    doc = await svc.generate_document(session, payload=payload, user_id=user.id)
    await write_audit(
        session,
        operator_id=user.id,
        action="generate",
        business_type="contract",
        business_id=doc.id,
        after={"doc_no": doc.doc_no, "template_id": doc.template_id,
               "customer_id": doc.customer_id, "order_id": doc.order_id},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_document(doc, customer_name=customer.name), "合同草稿已生成")


@router.get("/contract-documents/{doc_id}")
async def get_document(
    doc_id: int,
    user: CurrentUser = Depends(require_permission("order:view")),
    session: AsyncSession = Depends(get_db),
):
    doc = await svc.get_doc_or_404(session, doc_id)
    await _ensure_doc_in_scope(session, user, doc)
    from app.modules.customer.model import Customer

    customer = await session.get(Customer, doc.customer_id)
    return ok(svc.serialize_document(doc, customer_name=customer.name if customer else None))


@router.post("/contract-documents/{doc_id}/sign")
async def sign_document(
    doc_id: int,
    payload: DocumentSign,
    request: Request,
    user: CurrentUser = Depends(require_permission("order:manage")),
    session: AsyncSession = Depends(get_db),
):
    doc = await svc.get_doc_or_404(session, doc_id)
    await _ensure_doc_in_scope(session, user, doc)
    await svc.sign_document(session, doc, file_id=payload.file_id, note=payload.note)
    await write_audit(
        session,
        operator_id=user.id,
        action="sign",
        business_type="contract",
        business_id=doc.id,
        after={"doc_no": doc.doc_no, "file_id": payload.file_id},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_document(doc), "已登记签署")


@router.post("/contract-documents/{doc_id}/void")
async def void_document(
    doc_id: int,
    payload: DocumentVoid,
    request: Request,
    user: CurrentUser = Depends(require_permission("order:manage")),
    session: AsyncSession = Depends(get_db),
):
    doc = await svc.get_doc_or_404(session, doc_id)
    await _ensure_doc_in_scope(session, user, doc)
    await svc.void_document(session, doc, reason=payload.reason)
    await write_audit(
        session,
        operator_id=user.id,
        action="void",
        business_type="contract",
        business_id=doc.id,
        after={"doc_no": doc.doc_no, "reason": payload.reason},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_document(doc), "文档已作废")


async def _ensure_doc_in_scope(session: AsyncSession, user, doc) -> None:
    from app.modules.customer.model import Customer

    customer = await session.get(Customer, doc.customer_id)
    await ensure_in_scope(
        session, user, owner_id=customer.owner_id if customer else None, label="合同文档"
    )
