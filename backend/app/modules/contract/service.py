"""合同模板填充与文档台账的业务逻辑（§3.6/场景14）。"""

import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.core.response import paginate
from app.modules.contract.model import (
    DOC_STATUS_LABEL,
    DOC_TYPE_LABEL,
    ContractDocument,
    ContractTemplate,
)
from app.modules.customer.model import Customer
from app.modules.order.model import SalesOrder
from app.modules.quote.model import Quote, QuoteVersion

#: 占位符：{{customer.name}} / {{order.order_no}} / {{extra.付款方式}} / {{today}}
_TOKEN = re.compile(r"\{\{([^{}]+)\}\}")

#: 占位符没填上的四种原因。**必须分开**：混成一个"缺项"会让人以为是"业务员忘了填"，
#: 而实际上很可能是模板把字段名写错了——处理方式完全不同，一个要人补内容，一个要改模板。
MISSING_UNKNOWN_SOURCE = "unknown_source"  # {{foo.bar}}：没有 foo 这个来源
MISSING_UNKNOWN_FIELD = "unknown_field"  # {{customer.不存在的字段}}：来源在，字段不在
MISSING_EMPTY = "empty"  # 字段存在，但这份资料里是空的
MISSING_EXTRA = "extra_missing"  # {{extra.付款方式}} 本次没填

MISSING_REASON_LABEL: dict[str, str] = {
    MISSING_UNKNOWN_SOURCE: "模板里的字段来源不存在",
    MISSING_UNKNOWN_FIELD: "对象上没有这个字段",
    MISSING_EMPTY: "字段存在，但这份资料里是空的",
    MISSING_EXTRA: "本次没有填写这一项",
}


def _f(value) -> str:
    return "" if value is None else str(value)


class _QuoteSource:
    """模板里 `{{quote.x}}` 的解析代理：**先看选定的报价版本，再看报价单本身**。

    为什么要合成一个来源：金额、币种、付款/贸易/交货条款都挂在 `quote_versions`
    上，而单号（quote_no）挂在 `quotes` 上。模板作者不该被逼着记住这个区分，
    所以 `{{quote.total_amount}}` 和 `{{quote.quote_no}}` 都该能用同一个前缀写。

    2026-10-05 之前这里只传报价单本体，而 `quotes` 表上**根本没有金额字段**，
    于是 `{{quote.total_amount}}` 恒被算成"缺项"——合同上的报价金额一直是空的，
    看起来像业务员没填，其实是取错了地方。

    两处都找不到时抛 AttributeError，render_template 会照常报"对象上没有这个字段"。
    """

    def __init__(self, quote, version):
        self._quote = quote
        self._version = version

    def __getattr__(self, name: str):
        # 注意：_quote / _version 存在实例 __dict__ 里，常规查找能命中，
        # 不会进到这里（否则就成了无限递归）。
        for obj in (self._version, self._quote):
            if obj is not None and hasattr(obj, name):
                return getattr(obj, name)
        raise AttributeError(name)


def render_template(
    body: str,
    *,
    customer: Customer,
    order: SalesOrder | None = None,
    quote: Quote | None = None,
    quote_version: "QuoteVersion | None" = None,
    extra_fields: dict[str, str] | None = None,
) -> tuple[str, dict, dict[str, str]]:
    """填充模板，返回（正文快照，填入值快照，没填上的占位符->原因）。

    已知 token 按对象字段填；{{extra.*}} 取业务员本次填写的空白项；
    **填不上的 token 连 {{ }} 一起原样留在正文里**，并登记进缺项清单——
    宁可让人看到"这里没填上"，也不要静默给一个空值还以为填好了。

    2026-10-05 修的坑：上面这句承诺在注释里写了好几个月，代码却一直是把填不上的
    换成空串。后果是模板把字段名写错（{{customer.nane}}）时，生成的合同上是**一片
    空白**——看不出是模板错了、还是这个客户确实没填，两种情况的处理方式完全不同。

    同一批还修了第二个坑：`{{quote.total_amount}}` 取的是报价单本体，而金额根本
    不在那张表上，所以这条**永远填不上**。现在 `quote` 由 _QuoteSource 代理，
    先查选定版本、再回落报价单本体。
    """
    extra = {str(k): str(v) for k, v in (extra_fields or {}).items()}
    filled: dict[str, str] = {}
    missing: dict[str, str] = {}
    quote_source = (
        _QuoteSource(quote, quote_version) if (quote is not None or quote_version is not None) else None
    )
    sources: dict[str, object] = {"customer": customer, "order": order, "quote": quote_source}

    def _replace(match: re.Match) -> str:
        token = match.group(1).strip()
        original = match.group(0)  # 连用户写的空格一起保留，缺项时原样放回去
        value: str | None = None

        if token == "today":
            value = datetime.now(UTC).date().isoformat()
        elif token.startswith("extra."):
            key = token[len("extra."):].strip()
            if extra.get(key, "").strip():
                value = extra[key]
            else:
                missing[token] = MISSING_EXTRA
        else:
            prefix, _, field = token.partition(".")
            prefix, field = prefix.strip(), field.strip()
            obj = sources.get(prefix)
            if obj is None or not field:
                missing[token] = MISSING_UNKNOWN_SOURCE
            elif not hasattr(obj, field):
                missing[token] = MISSING_UNKNOWN_FIELD
            else:
                raw = getattr(obj, field)
                if raw is None or not str(raw).strip():
                    missing[token] = MISSING_EMPTY
                else:
                    value = _f(raw)

        if value is None:
            filled[token] = ""
            return original
        filled[token] = value
        return value

    content = _TOKEN.sub(_replace, body)
    return content, filled, missing


async def _header_snapshot(
    session: AsyncSession, *, customer, order, quote, quote_version
) -> dict:
    """生成时的抬头快照（存进 filled_data 的 `_header` 键）。

    含：公司名（系统设置里的抬头）、客户名、关联的订单号 / 报价号 / 报价版本号。

    存它就是为了"历史原件下载出来和当初一模一样"：客户改名、公司换抬头、
    报价后来出了 V2，都不该让已经发出去的那份合同跟着变。
    下载与打印统一走 `_header`；老数据（本批之前生成的）没有这个键，回落到实时查询。
    """
    from app.modules.settings import service as settings_service

    return {
        "company_name": await settings_service.get_text(session, "company_name", "text", ""),
        "customer_name": customer.name,
        "order_no": order.order_no if order is not None else None,
        "quote_no": quote.quote_no if quote is not None else None,
        "quote_version_no": getattr(quote_version, "version_no", None),
    }


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
    # 生成时没填上的占位符（正文里留着 {{...}} 原文），编译成人话给界面提示用。
    # 直接给翻译好的：前端只管显示，不必再维护一份 code->文案的映射（两处映射迟早分叉）。
    filled = doc.filled_data or {}
    missing = {
        token: MISSING_REASON_LABEL.get(reason, reason)
        for token, reason in (filled.get("_missing") or {}).items()
    }
    header = filled.get("_header") or {}
    return {
        "id": doc.id,
        "doc_no": doc.doc_no,
        "doc_type": doc.doc_type,
        "doc_type_label": DOC_TYPE_LABEL.get(doc.doc_type, doc.doc_type),
        "title": doc.title,
        "customer_id": doc.customer_id,
        # 台账上显示**当前**客户名（改了名要在台账里找得到）；
        # 下载出来的原件走 header 快照，两者故意不同，见 service._header_snapshot。
        "customer_name": customer_name or header.get("customer_name"),
        "order_id": doc.order_id,
        "quote_id": doc.quote_id,
        "quote_version_id": doc.quote_version_id,
        "quote_version_no": header.get("quote_version_no"),
        "template_id": doc.template_id,
        "content_snapshot": doc.content_snapshot,
        "filled_data": doc.filled_data,
        "missing_fields": missing,
        # 抬头快照（生成时的公司名/客户名/单号）：详情页据此说明"当时是以谁的名义出的"
        "header_snapshot": header or None,
        # 生成时落盘的那份 PDF；为空说明是本批之前的老数据，下载回落实时渲染
        "generated_file_id": doc.generated_file_id,
        "status": doc.status,
        "status_label": DOC_STATUS_LABEL.get(doc.status, doc.status),
        "expiry_date": doc.expiry_date,
        "parent_id": doc.parent_id,
        "signed_at": doc.signed_at,
        "void_reason": doc.void_reason,
        "created_at": doc.created_at,
    }


async def _next_template_version(session: AsyncSession, doc_type: str, name: str) -> int:
    """下一个版本号 = 同类型同名里最大的版本 + 1。

    **连软删的行一起看**（不加 `deleted_at IS NULL`）：版本号是永久标识，
    软删一行不该把它的号腾出来——否则下一个新建的会顶用同一个号，
    同一个「v2」先后指向两份不同正文，(类型,名称,版本) 的唯一约束也会直接冲突。
    """
    latest = (
        await session.execute(
            select(ContractTemplate.version)
            .where(
                ContractTemplate.doc_type == doc_type,
                ContractTemplate.name == name,
            )
            .order_by(ContractTemplate.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return 1 if latest is None else latest + 1


async def create_template(session: AsyncSession, *, payload, user_id: int) -> ContractTemplate:
    """新增模板=新增版本：同名同类型自动 version+1，旧版本永远保留。

    并发安全分三层：
    1. 先按"最大版本+1"算号（正常路径，一次就成）；
    2. 库上 (doc_type, name, version) 唯一约束兜底——算重了写不进去；
    3. 撞了约束就重算一次再插（最多 3 次）。两个管理员同时建同名模板时，
       后到的那个自动变成下一个版本，而不是给用户抛一个 500。
    """
    doc_type = payload.doc_type if payload.doc_type in DOC_TYPE_LABEL else "contract"
    name = payload.name.strip()
    for _ in range(3):
        version = await _next_template_version(session, doc_type, name)
        try:
            # 保存点：撞约束时只回滚这一次插入，外层事务还能继续用
            async with session.begin_nested():
                template = ContractTemplate(
                    doc_type=doc_type,
                    name=name,
                    version=version,
                    body=payload.body,
                    enabled=payload.enabled,
                    created_by=user_id,
                    created_at=datetime.now(UTC),
                )
                session.add(template)
                await session.flush()
        except IntegrityError:
            continue
        return template
    raise AppError(
        ErrorCode.STATUS_NOT_ALLOWED,
        "模板版本号连续冲突，请重试；若反复出现请检查是否有同名模板在被批量创建",
        409,
    )


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


async def generate_document(
    session: AsyncSession, *, payload, user_id: int
) -> tuple[ContractDocument, bool]:
    """从模板版本生成草稿：填好的正文与填入值都落快照，之后改资料不影响它。

    返回 `(文档, 是不是重复请求)`。`request_key` 命中已有文档时直接返回那一份、
    不再新建——网络重试和连点两次都不该在台账上留下两份一模一样的草稿
    （两份还各有一个 doc_no，事后分不清哪份有效）。
    """
    if payload.request_key:
        replayed = (
            await session.execute(
                select(ContractDocument).where(
                    ContractDocument.request_key == payload.request_key,
                    ContractDocument.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if replayed is not None:
            return replayed, True

    template = await session.get(ContractTemplate, payload.template_id)
    if template is None or template.deleted_at is not None or not template.enabled:
        raise AppError(ErrorCode.NOT_FOUND, "模板不存在或已停用", 404)
    customer = await session.get(Customer, payload.customer_id)
    if customer is None or customer.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "客户不存在", 404)
    order = await session.get(SalesOrder, payload.order_id) if payload.order_id else None
    if payload.order_id and order is None:
        raise AppError(ErrorCode.NOT_FOUND, "订单不存在", 404)
    if order is not None and order.customer_id != customer.id:
        raise AppError(ErrorCode.PARAM_ERROR, "所选订单不属于该客户", 422)
    quote = await session.get(Quote, payload.quote_id) if payload.quote_id else None
    if payload.quote_id and quote is None:
        raise AppError(ErrorCode.NOT_FOUND, "报价单不存在", 404)
    if quote is not None and quote.customer_id != customer.id:
        raise AppError(ErrorCode.PARAM_ERROR, "所选报价不属于该客户", 422)
    # 报价版本：合同据此钉死金额与条款。不传也能生成（提前备合同的口径），
    # 但那样金额就是空的——登记签署前必须补上正式依据，见 sign_document。
    quote_version = None
    if payload.quote_version_id is not None:
        if quote is None:
            raise AppError(ErrorCode.PARAM_ERROR, "选了报价版本就必须同时选报价单", 422)
        quote_version = await session.get(QuoteVersion, payload.quote_version_id)
        if quote_version is None:
            raise AppError(ErrorCode.NOT_FOUND, "报价版本不存在", 404)
        if quote_version.quote_id != quote.id:
            raise AppError(ErrorCode.PARAM_ERROR, "所选报价版本不属于该报价单", 422)

    # 只选了订单、没指定版本：跟订单走。订单本身是从某个报价版本转过来的
    # （`sales_orders.quote_version_id`），合同依据的理应就是那一版——
    # 让用户再选一次纯属多此一举，还容易选岔成"合同按 V1 签、车间按 V2 生产"。
    if quote_version is None and order is not None and order.quote_version_id is not None:
        auto_version = await session.get(QuoteVersion, order.quote_version_id)
        if auto_version is not None and (quote is None or auto_version.quote_id == quote.id):
            quote_version = auto_version

    # 由版本反推报价单：只给了版本号（或上面从订单带出来的）时把报价单补上
    if quote is None and quote_version is not None:
        quote = await session.get(Quote, quote_version.quote_id)

    if order is not None and quote is not None and order.quote_id not in (None, quote.id):
        raise AppError(ErrorCode.PARAM_ERROR, "所选报价与订单不匹配", 422)
    parent = None
    if payload.parent_id is not None:
        parent = await session.get(ContractDocument, payload.parent_id)
        if parent is None or parent.deleted_at is not None:
            raise AppError(ErrorCode.NOT_FOUND, "被补充的合同文档不存在", 404)
        if parent.customer_id != customer.id:
            raise AppError(ErrorCode.PARAM_ERROR, "被补充的合同文档不属于该客户", 422)
        # 「替代旧协议」只对月结协议有意义：销售合同之间的衍生件是**补充协议**，
        # 不存在"替代"——两份合同可以同时有效，靠条款说明相互关系。
        if payload.supersede_parent and parent.doc_type != "monthly":
            raise AppError(
                ErrorCode.PARAM_ERROR,
                "只有月结协议才有「替代旧协议」这个动作；销售合同的衍生件请用补充协议",
                422,
            )

    content, filled, missing = render_template(
        template.body,
        customer=customer,
        order=order,
        quote=quote,
        quote_version=quote_version,
        extra_fields=payload.extra_fields,
    )
    if missing:
        # 缺项存进快照的内部键：「这份合同生成时就有哪几处没填上」是事后要查得出的
        # 事实——只在当时弹个提示、过后无据可查，等于没修。
        # 用 `_` 开头是跟项目里"内部键"的约定走，序列化时不会被当成业务字段。
        filled = {**filled, "_missing": missing}
    # 抬头快照：下载历史原件时用**当时**的客户名 / 公司名 / 单号。
    # 不存的话，客户改名之后同一份合同再下载，正文还是老名字（走快照）、
    # 抬头却已经是新名字——同一编号两次下载内容不同，对外文件出这种事说不清。
    filled["_header"] = await _header_snapshot(
        session, customer=customer, order=order, quote=quote, quote_version=quote_version
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
        # 存**解析后**的报价与版本：上面从订单自动带出来的那一份也要落库，
        # 否则台账上会显示"无来源"，白丢了追溯关系。
        quote_id=quote.id if quote is not None else None,
        quote_version_id=quote_version.id if quote_version is not None else None,
        template_id=template.id,
        content_snapshot=content,
        filled_data=filled,
        expiry_date=payload.expiry_date,
        effective_date=payload.effective_date,
        parent_id=payload.parent_id,
        request_key=payload.request_key,
        created_by=user_id,
        created_at=datetime.now(UTC),
    )
    session.add(doc)
    await session.flush()

    # 续签替代旧协议（业务口径 2026-10-05「精细」）：**勾了才动**。
    # 不勾就什么都不做——提前续签、旧协议还在适用期是很常见的情况，
    # 一登记就把旧协议掐掉会让还在生效的协议没人管。
    if payload.supersede_parent and parent is not None:
        cancelled = await cancel_auto_tasks(session, parent.id)
        doc.filled_data = {
            **filled,
            "_supersedes": {
                "id": parent.id,
                "doc_no": parent.doc_no,
                "cancelled_tasks": cancelled,
            },
        }
        # 旧协议上留痕，但**不改状态、不动作废**（那是另一回事，要主管点头）：
        # 只让后人能回答"这份协议为什么不再提醒了"。
        parent.filled_data = {
            **(parent.filled_data or {}),
            "_superseded_by": {"id": doc.id, "doc_no": doc.doc_no},
        }
        await session.flush()

    return doc, False


async def get_doc_or_404(
    session: AsyncSession, doc_id: int, *, for_update: bool = False
) -> ContractDocument:
    """取合同文档；`for_update=True` 时加行锁。

    签署 / 作废这种"改了就回不去"的动作必须走 `for_update=True`：
    否则两个人同时点「登记签署」，两边都读到 `draft`、都通过检查，
    结果挂上两份签署件、发出两条签署通知——台账上签字页有两张，
    事后说不清哪张才算数。行锁让后来者读到已改的状态，被既有的状态检查挡掉。
    """
    if for_update:
        doc = (
            await session.execute(
                select(ContractDocument)
                .where(ContractDocument.id == doc_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
    else:
        doc = await session.get(ContractDocument, doc_id)
    if doc is None or doc.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "合同文档不存在", 404)
    return doc


async def build_pdf_data(session: AsyncSession, doc: ContractDocument) -> dict:
    """组装合同 PDF 的渲染数据（生成时落盘与下载共用这一份）。

    抬头（公司名 / 客户名 / 订单号 / 报价号）**优先取生成时的快照**，
    只有本批之前生成的老数据（快照里没有 `_header`）才回落到实时查询。

    这是"同一编号任何时候下载都是同一份"的关键：落盘那次和下载这次
    拿到的是同一组抬头，不会因为客户改了名而对不上。
    """
    from app.modules.settings import service as settings_service

    header = (doc.filled_data or {}).get("_header") or {}

    company_name = header.get("company_name")
    if company_name is None:
        company_name = await settings_service.get_text(session, "company_name", "text", "")

    customer_name = header.get("customer_name")
    if customer_name is None:
        customer = await session.get(Customer, doc.customer_id)
        customer_name = customer.name if customer else None

    order_no = header.get("order_no")
    if order_no is None and doc.order_id:
        order = await session.get(SalesOrder, doc.order_id)
        order_no = order.order_no if order else None

    quote_no = header.get("quote_no")
    if quote_no is None and doc.quote_id:
        quote = await session.get(Quote, doc.quote_id)
        quote_no = quote.quote_no if quote else None

    return {
        "company_name": company_name,
        "doc_no": doc.doc_no,
        "doc_type_label": DOC_TYPE_LABEL.get(doc.doc_type, doc.doc_type),
        "status_label": DOC_STATUS_LABEL.get(doc.status, doc.status),
        "customer_name": customer_name,
        "created_date": doc.created_at.strftime("%Y-%m-%d") if doc.created_at else None,
        "order_no": order_no,
        "quote_no": quote_no,
        "quote_version_no": header.get("quote_version_no"),
        "expiry_date": doc.expiry_date,
        "content_snapshot": doc.content_snapshot,
    }


async def attach_generated_pdf(
    session: AsyncSession, doc: ContractDocument, *, user_id: int | None
) -> int:
    """把这份合同的 PDF 渲染一次落盘，登记成文件 + 附件，回填 `generated_file_id`。

    为什么要存下来：此前每次下载都是拿**当前资料**重新渲染。客户名从 A 改成 B 之后，
    同一份合同再下载，正文还是 A（正文走快照），抬头已经变成 B——同一个编号
    两次下载内容不同，给客户/工厂的纸质件就说不清哪份为准了。
    现在生成即固定：下载直接返回这一份，校验值能自证没被换过。

    附件 category 用 `generated`（生成稿），与签署原件的 `signed` 分开——
    两者用途不同，混在一起就会出现"下载到的到底是草稿还是签回来的件"的歧义。
    """
    import asyncio

    from app.core.config import settings
    from app.modules.contract.pdf import render_contract_pdf
    from app.modules.file import storage
    from app.modules.file.model import BusinessFile, FileRecord

    data = await build_pdf_data(session, doc)
    # reportlab 是同步 CPU 活，丢线程池，别卡住事件循环（与下载路径一致）
    pdf_bytes = await asyncio.to_thread(render_contract_pdf, data)
    object_key, size, checksum = await storage.save_bytes(pdf_bytes, ".pdf")
    record = FileRecord(
        storage_provider=settings.storage_provider,
        object_key=object_key,
        file_name=f"{doc.doc_no}.pdf",
        mime_type="application/pdf",
        size=size,
        checksum=checksum,
        uploaded_by=user_id,
    )
    session.add(record)
    await session.flush()
    session.add(
        BusinessFile(
            business_type="contract",
            business_id=doc.id,
            file_id=record.id,
            category="generated",
        )
    )
    doc.generated_file_id = record.id
    await session.flush()
    return record.id


async def list_signed_files(session: AsyncSession, doc_id: int) -> list[dict]:
    """这份合同的**签署原件**清单（category=signed，按登记时间倒序）。

    详情接口带上它，前端才能在已签状态下给出"看签署原件"的入口——
    否则用户点「下载」拿到的是生成稿，却以为拿的是自己签回来的那一份。
    """
    from app.modules.file.model import BusinessFile, FileRecord

    rows = (
        await session.execute(
            select(
                FileRecord.id,
                FileRecord.file_name,
                FileRecord.size,
                FileRecord.checksum,
                BusinessFile.created_at,
            )
            .join(BusinessFile, BusinessFile.file_id == FileRecord.id)
            .where(
                BusinessFile.business_type == "contract",
                BusinessFile.business_id == doc_id,
                BusinessFile.category == "signed",
            )
            .order_by(BusinessFile.created_at.desc(), BusinessFile.id.desc())
        )
    ).all()
    return [
        {
            "file_id": file_id,
            "file_name": file_name,
            "size": size,
            "checksum": checksum,
            "attached_at": attached_at,
        }
        for file_id, file_name, size, checksum, attached_at in rows
    ]


#: 报价里"客户手里已经有这份东西"的状态。草稿 / 待审批不算——
#: 拿一份客户还没见过的报价去签合同，等于没有依据。
_QUOTE_SIGNABLE_STATUSES = ("sent", "accepted")


async def _ensure_signable_source(session: AsyncSession, doc: ContractDocument) -> None:
    """「提前备合同」口径（业务方 2026-10-05 定）：条款可以先备，签署前必须有正式依据。

    - **销售合同**：挂上了正式订单（未被取消），或挂上了**已发送 / 已接受**的报价；
    - **月结协议**：只关联客户即可——它本来就是跟客户约定的结算方式，未必有单。

    为什么卡在"登记签署"这一步、而不是"生成"：先备条款是真实业务需求
    （客户要求先把合同发过去、签了才下单）。但若一路签完字都没有依据，
    这份合同签的到底是什么、按哪个价，就没有出处了。
    """
    if doc.doc_type == "monthly":
        return
    if doc.order_id is not None:
        order = await session.get(SalesOrder, doc.order_id)
        if order is not None and order.status != "cancelled":
            return
    if doc.quote_id is not None:
        quote = await session.get(Quote, doc.quote_id)
        if (
            quote is not None
            and quote.deleted_at is None
            and quote.status in _QUOTE_SIGNABLE_STATUSES
        ):
            return
    raise AppError(
        ErrorCode.STATUS_NOT_ALLOWED,
        "登记签署前要先绑定正式依据：关联一张正式订单，或关联一份已发送/已接受的报价"
        "（月结协议不受此限）",
        422,
    )


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
    await _ensure_signable_source(session, doc)
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


async def void_document(session: AsyncSession, doc: ContractDocument, *, reason: str) -> int:
    """作废文档，并结束它带出来的在办待办，返回被取消的待办数。

    作废之后就不该再提醒了——否则负责人会收到"某某协议即将到期"的待办，
    点进去发现那份协议早就作废了（审查第 7 条）。
    """
    if doc.status == "void":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "文档已经是作废状态")
    doc.status = "void"
    doc.void_reason = reason
    cancelled = await cancel_auto_tasks(session, doc.id)
    await session.flush()
    return cancelled


async def list_documents(
    session: AsyncSession,
    *,
    owner_ids: list[int] | None,
    customer_id: int | None,
    status: str | None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    """台账（分页）：数据范围跟客户负责人**当前**归属走——

    换负责人后新负责人按权限查看历史原件（场景14），不存过期 owner 快照。

    2026-10-05 之前这里是 `.limit(500)` 硬顶：第 501 份合同在页面上永远不出现，
    而且没有任何提示——用户只会以为"一共就这么些"。
    """
    stmt = (
        select(ContractDocument)
        .join(Customer, Customer.id == ContractDocument.customer_id)
        .where(ContractDocument.deleted_at.is_(None))
    )
    if customer_id:
        stmt = stmt.where(ContractDocument.customer_id == customer_id)
    if status:
        stmt = stmt.where(ContractDocument.status == status)
    if owner_ids is not None:
        stmt = stmt.where(Customer.owner_id.in_(owner_ids))
    docs, total = await paginate(
        session, stmt.order_by(ContractDocument.created_at.desc()), page, page_size
    )
    # 客户名单独批量取：paginate 内部走 scalars()，双列查询会把第二列丢掉
    names: dict[int, str] = {}
    customer_ids = {doc.customer_id for doc in docs}
    if customer_ids:
        names = dict(
            (
                await session.execute(
                    select(Customer.id, Customer.name).where(Customer.id.in_(customer_ids))
                )
            ).all()
        )
    return [
        serialize_document(doc, customer_name=names.get(doc.customer_id)) for doc in docs
    ], total


def _monthly_remind_key(doc_id: int, expiry_date) -> str:
    """月结到期待办的**去重身份键**：`contract:monthly:{文档id}:{到期日}`。

    到期日必须进键里：续签换了到期日就是一个新的周期，该提醒还得提醒；
    而同一份协议、同一个到期日，永远只该对应一张待办。
    """
    return f"contract:monthly:{doc_id}:{expiry_date}"


async def cancel_auto_tasks(session: AsyncSession, doc_id: int) -> int:
    """结束这份协议带出来的自动待办（作废、或被续签替代时调用）。

    只动**还没结束**的（待处理 / 处理中）。已经完成的原样保留——
    那是历史记录，当时的处理人和完成时间都不该被改写（审查第 7 条的要求）。

    为什么是"取消"而不是"删除"：一条待办曾经存在过、后来被作废终止，
    这件事本身有追溯价值；删掉就成了"从来没提醒过"，与事实不符。
    """
    from app.modules.task.model import Task

    rows = (
        await session.execute(
            select(Task).where(
                Task.source_business_type == "contract",
                Task.source_business_id == doc_id,
                Task.status.in_(("pending", "doing")),
            )
        )
    ).scalars().all()
    for task in rows:
        task.status = "cancelled"
    return len(rows)


async def notify_expiring_monthly(session: AsyncSession) -> int:
    """月结协议到期前提醒负责人（§3.6）。挂进每日自动任务，返回本轮新建条数。

    2026-10-05 重写。改之前是**按标题去重、且只看"待处理 / 处理中"的任务**，于是：
    - 任务一旦完成或被取消，下次扫描找不到它 → 又建一条同样的待办，天天冒出来；
    - 标题被人改一个字，就成了"另一条"；
    - 待办上没记是哪份协议，点进去不知道要处理谁。

    现在：
    - 去重靠 `source_key`（含到期日），**不论任务当前是什么状态**——
      同一协议同一到期周期永远只对应一张待办；
    - 完成后**不自动重建**（业务口径 2026-10-05：要接着办就重新打开原来那张）；
    - 待办上记 `source_business_type / source_business_id`，界面可跳转到协议；
    - 协议作废 / 被续签替代时，它的待办由 `cancel_auto_tasks` 结束；
    - 提前提醒天数继续用现有配置 `contract.monthly_remind_days`，不另写固定值。
    """
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
        source_key = _monthly_remind_key(doc.id, doc.expiry_date)
        existing = (
            await session.execute(select(Task.id).where(Task.source_key == source_key))
        ).scalar_one_or_none()
        if existing is not None:
            # 已经有过了（不管是待处理、已完成还是已取消）：不再新建。
            # 这一句就是"完成后又冒出来"的解药。
            continue
        session.add(
            Task(
                title=f"月结协议 {doc.doc_no} 将于 {doc.expiry_date} 到期",
                task_type="followup",
                customer_id=doc.customer_id,
                owner_id=owner_id,
                priority="high",
                status="pending",
                due_at=datetime.now(UTC),
                source="system",
                source_rule_id=None,
                source_business_type="contract",
                source_business_id=doc.id,
                source_key=source_key,
            )
        )
        created += 1
    await session.flush()
    return created
