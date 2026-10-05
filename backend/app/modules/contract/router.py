"""合同/月结协议接口：模板维护（管理）、生成/签署/作废与台账（销售/主管）。"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.data_scope import ensure_in_scope, scoped_owner_ids
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok, page_data
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
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user: CurrentUser = Depends(require_permission("order:view")),
    session: AsyncSession = Depends(get_db),
):
    owner_ids = await scoped_owner_ids(session, user)
    items, total = await svc.list_documents(
        session,
        owner_ids=owner_ids,
        customer_id=customer_id,
        status=status,
        page=page,
        page_size=page_size,
    )
    return ok(page_data(items, total, page, page_size))


@router.post("/contract-documents")
async def generate_document(
    payload: DocumentCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("order:manage")),
    session: AsyncSession = Depends(get_db),
):
    from app.modules.customer import service as customer_service
    from app.modules.order.model import SalesOrder
    from app.modules.quote.model import Quote

    customer = await customer_service.get_visible_customer(session, user, payload.customer_id)
    await ensure_in_scope(session, user, owner_id=customer.owner_id, label="客户")
    if payload.order_id is not None:
        order = await session.get(SalesOrder, payload.order_id)
        if order is None:
            raise AppError(ErrorCode.NOT_FOUND, "订单不存在", 404)
        if order.customer_id != customer.id:
            raise AppError(ErrorCode.PARAM_ERROR, "所选订单不属于该客户", 422)
        await ensure_in_scope(session, user, owner_id=order.owner_id, label="订单")
    if payload.quote_id is not None:
        quote = await session.get(Quote, payload.quote_id)
        if quote is None:
            raise AppError(ErrorCode.NOT_FOUND, "报价单不存在", 404)
        if quote.customer_id != customer.id:
            raise AppError(ErrorCode.PARAM_ERROR, "所选报价不属于该客户", 422)
        await ensure_in_scope(session, user, owner_id=quote.owner_id, label="报价单")
    if payload.order_id is not None and payload.quote_id is not None:
        if order.quote_id is not None and order.quote_id != quote.id:
            raise AppError(ErrorCode.PARAM_ERROR, "所选报价与订单不匹配", 422)
    doc, replayed = await svc.generate_document(session, payload=payload, user_id=user.id)
    if replayed:
        # 重复请求（重试 / 连点两次）：既不建新单，也不再写一条审计 ——
        # 审计里出现两条「生成」会让人以为真生成了两份。
        return ok(
            svc.serialize_document(doc, customer_name=customer.name),
            "这份合同刚才已经生成过了，返回的是同一份",
        )
    # 生成即把 PDF 渲染一次落盘（带 sha256）。之后无论客户改几次名，
    # 下载拿到的都是这一份——同一编号两次下载内容不同是对外文件的硬伤。
    await svc.attach_generated_pdf(session, doc, user_id=user.id)
    # 补充协议 / 续签是**一次真实的业务动作**，要进客户时间线；
    # 普通草稿生成不记——草稿不代表签约（审查第 9 条明确要求）。
    if doc.parent_id:
        parent_doc = await svc.get_doc_or_404(session, doc.parent_id)
        supersedes = (doc.filled_data or {}).get("_supersedes") or {}
        label = "月结协议续签" if doc.doc_type == "monthly" else "新增补充协议"
        extra = ""
        if supersedes:
            extra = f"；已替代原协议 {supersedes.get('doc_no')}"
            if supersedes.get("cancelled_tasks"):
                extra += f"，同时结束了 {supersedes['cancelled_tasks']} 条在办提醒"
        await _record_contract_event(
            session,
            doc,
            user=user,
            action="amend",
            title=label,
            content=f"{label} {doc.doc_no}，基于 {parent_doc.doc_no}{extra}",
        )
    await write_audit(
        session,
        operator_id=user.id,
        action="generate",
        business_type="contract",
        business_id=doc.id,
        after={"doc_no": doc.doc_no, "template_id": doc.template_id,
               "customer_id": doc.customer_id, "order_id": doc.order_id,
               "quote_id": doc.quote_id, "quote_version_id": doc.quote_version_id,
               "generated_file_id": doc.generated_file_id},
        ip=client_ip(request),
    )
    await session.commit()
    result = svc.serialize_document(doc, customer_name=customer.name)
    missing = result.get("missing_fields") or {}
    if missing:
        # 缺项必须说出来：正文里留着 {{...}}，不报一声用户会以为模板坏了、或者数据丢了。
        # 这里只提示、不拦——"必填项不齐就不许生成对外文件"是模板级配置，属于下一批。
        return ok(result, f"合同草稿已生成；有 {len(missing)} 处没填上（正文里保留了占位符）")
    return ok(result, "合同草稿已生成")


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
    result = svc.serialize_document(doc, customer_name=customer.name if customer else None)
    # 关系链两头都给：往上是"基于哪一份"（补充协议 / 续签），往下是"被哪几份补充过"。
    # 父文档用普通 get 而不是 get_doc_or_404：父件被软删时不该让子件详情也打不开。
    if doc.parent_id:
        from app.modules.contract.model import ContractDocument

        parent = await session.get(ContractDocument, doc.parent_id)
        result["parent_doc_no"] = parent.doc_no if parent else None
    result["amendments"] = await svc.list_amendments(session, doc.id)
    # 签署原件清单：已签状态下前端要有"看签回来的那一份"的入口。
    # 不给的话，用户点「下载」拿到的是生成稿，却以为拿的是签署件。
    result["signed_files"] = await svc.list_signed_files(session, doc.id)
    return ok(result)


@router.post("/contract-documents/{doc_id}/sign")
async def sign_document(
    doc_id: int,
    payload: DocumentSign,
    request: Request,
    user: CurrentUser = Depends(require_permission("order:manage")),
    session: AsyncSession = Depends(get_db),
):
    doc = await svc.get_doc_or_404(session, doc_id, for_update=True)
    await _ensure_doc_in_scope(session, user, doc)
    from app.modules.file.access import can_access_file

    if not await can_access_file(session, user, payload.file_id):
        raise AppError(ErrorCode.DATA_SCOPE_DENIED, "无权将该文件登记为此合同的签署件", 403)
    await svc.sign_document(session, doc, file_id=payload.file_id, note=payload.note)
    # 接入客户时间线与通知（审查第 9 条）：审计回答"谁动了这个接口"，
    # 客户时间线回答"这个客户身上发生了什么"——两者用途不同，只写审计不够。
    # 复用打样/订单在用的同一套机制（写客户留痕 + 排主管通知），不另起一套。
    await _record_contract_event(
        session,
        doc,
        user=user,
        action="sign",
        title="合同已签署",
        content=f"合同 {doc.doc_no}（{doc.title}）已登记签署",
    )
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
    """下载**生成稿**（§3.6）。

    生成的时候就把 PDF 渲染一次落盘了，这里返回当时那一份——客户后来改名、
    公司换抬头、报价出了 V2，都不会让已经发出去的那份合同跟着变。
    只有本批之前生成的老数据（没有 `generated_file_id`）才回落到实时渲染。

    这是**生成稿**，不是签署原件：签回来的扫描件在详情接口的 `signed_files` 里，
    下载不等于已签，签了才算签。
    """
    import asyncio

    from fastapi import Response

    from app.modules.contract.pdf import render_contract_pdf
    from app.modules.file import storage
    from app.modules.file.model import FileRecord

    doc = await svc.get_doc_or_404(session, doc_id)
    await _ensure_doc_in_scope(session, user, doc)

    content: bytes | None = None
    source = "generated"
    if doc.generated_file_id:
        record = await session.get(FileRecord, doc.generated_file_id)
        if record is not None:
            try:
                path = storage.absolute_path(record.object_key)
                content = await asyncio.to_thread(path.read_bytes)
            except OSError:
                # 落盘的文件被清理/搬走了：不抛 500，退回实时渲染，但审计里记明来源，
                # 免得事后以为"下载的就是当初存档的那一份"。
                content = None
    if content is None:
        source = "regenerated" if doc.generated_file_id else "legacy_rendered"
        data = await svc.build_pdf_data(session, doc)
        # reportlab 渲染是同步 CPU 密集操作，丢线程池避免卡住事件循环（与报价 PDF 同）
        content = await asyncio.to_thread(render_contract_pdf, data)

    # 下载留痕：合同是含价格与条款的对客文件，谁在什么时候取了哪一份要能查
    # （作废件也一样取得到，只是纸上必须带"已作废"，所以这里记状态）。
    # `source` 记的是"这份内容从哪来"，用于分辨有没有回落到重新渲染。
    await write_audit(
        session,
        operator_id=user.id,
        action="download",
        business_type="contract",
        business_id=doc.id,
        after={"doc_no": doc.doc_no, "status": doc.status, "source": source},
    )
    await session.commit()
    return Response(
        content=content,
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
    doc = await svc.get_doc_or_404(session, doc_id, for_update=True)
    await _ensure_doc_in_scope(session, user, doc)
    # 已签合同的作废要主管点头（业务方 2026-10-05 定）：未签草稿业务员自己处理，
    # 但「已签」是台账上的既成事实，不该随手被改掉。
    if doc.status == "signed" and not _is_manager(user):
        raise AppError(
            ErrorCode.FORBIDDEN,
            "已签署的合同要作废，需要主管权限；未签草稿你自己处理即可",
            403,
        )
    cancelled = await svc.void_document(session, doc, reason=payload.reason)
    await _record_contract_event(
        session,
        doc,
        user=user,
        action="void",
        title="合同已作废",
        content=(
            f"合同 {doc.doc_no}（{doc.title}）已作废，原因：{payload.reason}"
            + (f"；同时结束了 {cancelled} 条在办的到期待办" if cancelled else "")
        ),
    )
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


#: 主管口径：与新品洞察 / 案例库评审共用同一套判断（见 product_insight/router.py）。
#: 项目里已经有两份同样的常量，这里是第三份——抽成共享常量要动那两个模块，
#: 不属这一批的范围，先就地写清楚出处，别让它悄悄漂移。
_REVIEWER_ROLES = ("sales_manager", "admin")


def _is_manager(user) -> bool:
    """主管或管理员。用于"已签合同作废"这类需要管理动作的场合。"""
    return any(role in _REVIEWER_ROLES for role in user.roles) or user.has("settings:manage")


async def _record_contract_event(
    session: AsyncSession,
    doc,
    *,
    user,
    action: str,
    title: str,
    content: str,
) -> None:
    """把合同上的一次真实动作写进**客户时间线**并推主管通知。

    审计回答"谁动了这个接口"，客户时间线回答"这个客户身上发生了什么"——
    两者用途不同，只写审计是不够的（审查第 9 条）。

    复用订单/打样在用的那一套（`record_and_notify`），不另起一套事件机制；
    幂等键按「动作:文档id」确定性生成，重放/重试命中同一条——
    客户时间线只留一条、主管只收一次。

    注意：**只有真实业务动作才走这里**。生成草稿不记——草稿不代表签约
    （审查原话："草稿生成不能记成已经签约"）。
    """
    from app.modules.customer.model import Customer
    from app.modules.followup import service as followup_service

    customer = await session.get(Customer, doc.customer_id)
    await followup_service.record_and_notify(
        session,
        customer_id=doc.customer_id,
        owner_id=customer.owner_id if customer else None,
        title=title,
        content=content,
        business_type="contract",
        business_id=doc.id,
        operator_id=user.id,
        event_key=f"contract:{action}:{doc.id}",
    )


async def _ensure_doc_in_scope(session: AsyncSession, user, doc) -> None:
    from app.modules.customer.model import Customer

    customer = await session.get(Customer, doc.customer_id)
    owner_id = customer.owner_id if customer else None
    # 客户可能被"超期未跟进自动回收"进公海、或被合并而清空负责人。合同本身是已签
    # 事实，不能因为客户进了公海就**谁都点不开**（口径 A）：有订单就跟订单负责人，
    # 没订单就跟当初创建这份合同的人。都没有才落回"无归属默认拒绝"。
    if owner_id is None and doc.order_id:
        from app.modules.order.model import SalesOrder

        order = await session.get(SalesOrder, doc.order_id)
        owner_id = order.owner_id if order else None
    if owner_id is None:
        owner_id = doc.created_by
    await ensure_in_scope(session, user, owner_id=owner_id, label="合同文档")
