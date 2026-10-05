"""对外单据接口：模板版本 + 打样需求单 / 下单文件的生成与下载（场景12）。"""

import asyncio
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.response import ok
from app.modules.bizdoc import service as svc
from app.modules.bizdoc.model import DOC_TYPE_FORMAT, DOC_TYPE_LABEL

router = APIRouter(tags=["BizDoc"])


class TemplateCreate(BaseModel):
    doc_type: str
    name: str = Field(min_length=1, max_length=120)
    body: str = ""
    enabled: bool = True
    remark: str | None = None


class SampleDocGenerate(BaseModel):
    sample_request_id: int
    template_id: int | None = None
    extra_fields: dict[str, str] | None = None


class OrderDocGenerate(BaseModel):
    order_id: int
    template_id: int | None = None
    extra_fields: dict[str, str] | None = None


class QuoteDocGenerate(BaseModel):
    """对客报价单按**报价版本**生成：金额必须来自那一版，而不是"当前价"。"""

    quote_version_id: int
    template_id: int | None = None
    extra_fields: dict[str, str] | None = None


class VoidPayload(BaseModel):
    reason: str = Field(min_length=1, max_length=255)


@router.get("/biz-doc-templates")
async def list_templates(
    doc_type: str | None = Query(None),
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    """对外单据模板（按类型多版本并存）。"""
    return ok(await svc.list_templates(session, doc_type))


@router.post("/biz-doc-templates")
async def create_template(
    payload: TemplateCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    """新增一版模板：**不覆盖旧版**，已生成的文件仍指向它们当时用的那一版。"""
    from app.core.errors import AppError, ErrorCode
    from app.modules.bizdoc.model import BizDocTemplate

    if payload.doc_type not in DOC_TYPE_LABEL:
        raise AppError(ErrorCode.PARAM_ERROR, "不支持的单据类型", 422)
    template = BizDocTemplate(
        doc_type=payload.doc_type,
        name=payload.name,
        version=await svc.next_template_version(session, payload.doc_type),
        body=payload.body,
        enabled=payload.enabled,
        remark=payload.remark,
        created_by=user.id,
        created_at=datetime.now(UTC),
    )
    session.add(template)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="create",
        business_type="biz_doc_template",
        business_id=template.id,
        after={"doc_type": payload.doc_type, "version": template.version, "name": payload.name},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_template(template), "模板已新增一版")


@router.get("/biz-docs")
async def list_docs(
    doc_type: str | None = Query(None),
    sample_request_id: int | None = Query(None),
    order_id: int | None = Query(None),
    order_draft_id: int | None = Query(None),
    quote_id: int | None = Query(None),
    customer_id: int | None = Query(None),
    limit: int = Query(100, ge=1, le=300),
    user: CurrentUser = Depends(require_permission("order:view")),
    session: AsyncSession = Depends(get_db),
):
    return ok(
        await svc.list_docs(
            session,
            user,
            doc_type=doc_type,
            sample_request_id=sample_request_id,
            order_id=order_id,
            order_draft_id=order_draft_id,
            quote_id=quote_id,
            customer_id=customer_id,
            limit=limit,
        )
    )


@router.post("/biz-docs/sample-request")
async def generate_sample_request_doc(
    payload: SampleDocGenerate,
    request: Request,
    user: CurrentUser = Depends(require_permission("sample:manage")),
    session: AsyncSession = Depends(get_db),
):
    """按打样申请生成一份打样需求单（来源询价与差异一起落快照，原单不变）。"""
    doc = await svc.generate_sample_request_doc(
        session,
        sample_request_id=payload.sample_request_id,
        user=user,
        template_id=payload.template_id,
        extra_fields=payload.extra_fields,
    )
    await write_audit(
        session,
        operator_id=user.id,
        action="generate",
        business_type="biz_doc",
        business_id=doc.id,
        after={
            "doc_no": doc.doc_no,
            "doc_type": doc.doc_type,
            "version": doc.version,
            "source_no": doc.source_no,
            "sample_request_id": doc.sample_request_id,
        },
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_doc(doc), "打样需求单已生成")


@router.post("/biz-docs/order")
async def generate_order_sheet_doc(
    payload: OrderDocGenerate,
    request: Request,
    user: CurrentUser = Depends(require_permission("order:manage")),
    session: AsyncSession = Depends(get_db),
):
    """按订单生成一份下单文件（来源报价与差异一起落快照，订单不变）。"""
    doc = await svc.generate_order_sheet_doc(
        session,
        order_id=payload.order_id,
        user=user,
        template_id=payload.template_id,
        extra_fields=payload.extra_fields,
    )
    await write_audit(
        session,
        operator_id=user.id,
        action="generate",
        business_type="biz_doc",
        business_id=doc.id,
        after={
            "doc_no": doc.doc_no,
            "doc_type": doc.doc_type,
            "version": doc.version,
            "source_no": doc.source_no,
            "order_id": doc.order_id,
        },
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_doc(doc), "下单文件已生成")


@router.post("/biz-docs/quote")
async def generate_quote_doc(
    payload: QuoteDocGenerate,
    request: Request,
    user: CurrentUser = Depends(require_permission("quote:manage")),
    session: AsyncSession = Depends(get_db),
):
    """按报价版本生成对客 Excel 报价单（金额取自那一版，不现算）。"""
    doc = await svc.generate_quote_doc(
        session,
        quote_version_id=payload.quote_version_id,
        user=user,
        template_id=payload.template_id,
        extra_fields=payload.extra_fields,
    )
    await write_audit(
        session,
        operator_id=user.id,
        action="generate",
        business_type="biz_doc",
        business_id=doc.id,
        after={
            "doc_no": doc.doc_no,
            "doc_type": doc.doc_type,
            "version": doc.version,
            "source_no": doc.source_no,
            "source_version": doc.source_version,
        },
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_doc(doc), "对客报价单已生成")


@router.get("/biz-docs/{doc_id}")
async def get_doc(
    doc_id: int,
    user: CurrentUser = Depends(require_permission("order:view")),
    session: AsyncSession = Depends(get_db),
):
    doc = await svc.get_doc_or_404(session, doc_id)
    await _ensure_scope(session, user, doc)
    payload = svc.serialize_doc(doc)
    payload["input_snapshot"] = doc.input_snapshot
    return ok(payload)


@router.get("/biz-docs/{doc_id}/download")
async def download_doc(
    doc_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("order:view")),
    session: AsyncSession = Depends(get_db),
):
    """下载文件。正文只取生成时的快照——之后改业务资料不影响已出的文件。

    格式按单据类型分流：报价单是客户要拿去改/填的 Excel（xlsx），
    打样单与下单文件是正式文件（pdf）。两者共用同一套台账。
    """
    from app.modules.bizdoc.pdf import render_biz_doc_pdf

    doc = await svc.get_doc_or_404(session, doc_id)
    await _ensure_scope(session, user, doc)
    data = await svc.doc_pdf_data(session, doc)
    # 下载留痕：含价格的对客文件谁在什么时候取了哪一份要能查（作废件也一样取得到，
    # 只是纸上带"已作废"，所以这里记状态）。
    await write_audit(
        session,
        operator_id=user.id,
        action="download",
        business_type="biz_doc",
        business_id=doc.id,
        after={"doc_no": doc.doc_no, "status": doc.status},
        ip=client_ip(request),
    )
    await session.commit()
    fmt = DOC_TYPE_FORMAT.get(doc.doc_type, "pdf")
    if fmt == "xlsx":
        from app.modules.bizdoc.xlsx import render_quote_xlsx

        content = await asyncio.to_thread(render_quote_xlsx, data)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        # 渲染是同步 CPU 操作，丢线程池避免卡住事件循环（与报价/合同 PDF 同）
        content = await asyncio.to_thread(render_biz_doc_pdf, data)
        media_type = "application/pdf"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{doc.doc_no}.{fmt}"'},
    )


@router.post("/biz-docs/{doc_id}/void")
async def void_doc(
    doc_id: int,
    payload: VoidPayload,
    request: Request,
    user: CurrentUser = Depends(require_permission("order:manage")),
    session: AsyncSession = Depends(get_db),
):
    """作废：状态改掉，内容与校验值不动（作废 ≠ 删档）。"""
    doc = await svc.get_doc_or_404(session, doc_id)
    await _ensure_scope(session, user, doc)
    await svc.void_doc(session, doc, reason=payload.reason)
    await write_audit(
        session,
        operator_id=user.id,
        action="void",
        business_type="biz_doc",
        business_id=doc.id,
        after={"doc_no": doc.doc_no, "reason": payload.reason},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_doc(doc), "已作废")


async def _ensure_scope(session: AsyncSession, user: CurrentUser, doc) -> None:
    from app.core.data_scope import ensure_in_scope

    await ensure_in_scope(session, user, owner_id=doc.owner_id, label="单据")
