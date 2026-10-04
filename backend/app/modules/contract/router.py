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


@router.get("/contract-documents/{doc_id}/download")
async def download_document(
    doc_id: int,
    user: CurrentUser = Depends(require_permission("order:view")),
    session: AsyncSession = Depends(get_db),
):
    """生成并下载合同/月结协议 PDF（§3.6）。

    正文取生成时的快照——客户资料之后改了，这份文件也不变。
    下载不等于已签：签署状态由 /sign 单独登记。
    """
    import asyncio

    from fastapi import Response

    from app.modules.contract.pdf import render_contract_pdf
    from app.modules.settings import service as settings_service

    doc = await svc.get_doc_or_404(session, doc_id)
    await _ensure_doc_in_scope(session, user, doc)
    from app.modules.customer.model import Customer

    customer = await session.get(Customer, doc.customer_id)
    order_no = None
    if doc.order_id:
        from app.modules.order.model import SalesOrder

        order = await session.get(SalesOrder, doc.order_id)
        order_no = order.order_no if order else None
    quote_no = None
    if doc.quote_id:
        from app.modules.quote.model import Quote

        quote = await session.get(Quote, doc.quote_id)
        quote_no = quote.quote_no if quote else None

    data = {
        "company_name": await settings_service.get_text(session, "company_name", "text", ""),
        "doc_no": doc.doc_no,
        "doc_type_label": svc.DOC_TYPE_LABEL.get(doc.doc_type, doc.doc_type),
        "status_label": svc.DOC_STATUS_LABEL.get(doc.status, doc.status),
        "customer_name": customer.name if customer else None,
        "created_date": doc.created_at.strftime("%Y-%m-%d") if doc.created_at else None,
        "order_no": order_no,
        "quote_no": quote_no,
        "expiry_date": doc.expiry_date,
        "content_snapshot": doc.content_snapshot,
    }
    # reportlab 渲染是同步 CPU 密集操作，丢线程池避免卡住事件循环（与报价 PDF 同）
    pdf_bytes = await asyncio.to_thread(render_contract_pdf, data)
    # 下载留痕：合同是含价格与条款的对客文件，谁在什么时候取了哪一份要能查
    # （作废件也一样取得到，只是纸上必须带"已作废"，所以这里记状态）。
    await write_audit(
        session,
        operator_id=user.id,
        action="download",
        business_type="contract",
        business_id=doc.id,
        after={"doc_no": doc.doc_no, "status": doc.status},
    )
    await session.commit()
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{doc.doc_no}.pdf"'},
    )


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
