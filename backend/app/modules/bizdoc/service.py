"""对外单据生成：打样需求单 / 下单文件（文档 §3.5、场景12）。

这一层要解决的四件事，正是场景12的四个断言：

1. **从来源单据生成**（"勾选旧询价生成打样和下单文件"）——来源单据号与版本
   抄进文件行，之后来源被改版也不影响这份文件；
2. **原单不变**——生成只读来源，从不写它；来源是从哪条需求/哪版报价来的，
   记在 `source_*` 四个字段里；
3. **本次差异可比**——本次明细与来源明细逐行比对，差异随文件一起落快照；
4. **旧文件不被覆盖**——重新生成是插新版本（version+1、parent_id 指向前一版），
   旧行一个字不动，仍然下载得到。

生成之后，文件正文只认 `input_snapshot`：客户资料、价格、交期之后怎么改，
已出的这一份都不会变——这是"对外承诺过的文件不能被系统悄悄改掉"的底线。
"""

import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.data_scope import ensure_in_scope, scoped_owner_ids
from app.core.deps import CurrentUser
from app.core.errors import AppError, ErrorCode
from app.modules.bizdoc.model import DOC_STATUS_LABEL, DOC_TYPE_LABEL, BizDoc, BizDocTemplate

#: 默认模板正文（业务还没给正式模板前的兜底，与合同模块同样的做法：
#: 名字里写明"待替换"，免得被当成正式条款用出去）
_DEFAULT_BODY = {
    "sample_request": (
        "1. 请按上表规格与数量打样，样品需标注需求编号与版本，便于与后续订单核对。\n"
        "2. 本单为打样需求，不代表订单承诺；打样费用与完成时间以双方确认为准。\n"
        "3. 样品寄出后请回填物流信息并通知对接业务员。"
    ),
    "order_sheet": (
        "1. 规格与数量按上表明细执行；如有变更需书面确认后重新出单。\n"
        "2. 交货时间与地点以本文件为准。\n"
        "3. 付款条件：{{order.payment_terms}}"
    ),
}

DEFAULT_TEMPLATE_NAME = "默认模板（待替换为正式模板）"


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _number(value: Any) -> str:
    """数量/金额去掉小数尾巴：5.000 → 5，2.500 → 2.5。"""
    if value is None:
        return ""
    if isinstance(value, Decimal):
        normalized = value.normalize()
        return f"{normalized:f}"
    return str(value)


def _jsonable(value: Any) -> Any:
    """把快照转成 JSON 可序列化的形式。

    两个都不能省：
    - `Decimal` 直接丢给 json.dumps 会抛 TypeError（数量、单价、金额都是它），
      所以统一转字符串；**不转 float**——JSONB 往返会把 12 变成 12.0，
      同一笔数量在文件里出现两种写法，对账时最容易吵起来的就是这个；
    - `date` 转 ISO 字符串，避免不同驱动反序列化出不同类型。
    """
    if isinstance(value, Decimal):
        return _number(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


# ---------------------------------------------------------------- 模板


async def ensure_default_templates(session: AsyncSession, user_id: int | None = None) -> None:
    """保证每种单据类型至少有一版可用模板（幂等）。

    与合同模块一致：业务还没给正式模板时不能因此开不出单，所以先落一份
    "默认模板（待替换为正式模板）"。之后业务模板到位，新增一版即可，
    历史文件仍指向它们当时用的版本。
    """
    existing = set(
        (
            await session.execute(select(BizDocTemplate.doc_type).distinct())
        ).scalars().all()
    )
    for doc_type, label in DOC_TYPE_LABEL.items():
        if doc_type in existing:
            continue
        session.add(
            BizDocTemplate(
                doc_type=doc_type,
                name=DEFAULT_TEMPLATE_NAME,
                version=1,
                body=_DEFAULT_BODY.get(doc_type, ""),
                enabled=True,
                remark=f"{label}默认模板，等业务提供正式样张后新增一版替换",
                created_by=user_id,
                created_at=datetime.now(UTC),
            )
        )
    await session.flush()


async def current_template(
    session: AsyncSession, doc_type: str, template_id: int | None = None
) -> BizDocTemplate:
    """取本次生成要用的模板：指定了就校验，没指定就用该类型最新启用版。"""
    if template_id is not None:
        template = await session.get(BizDocTemplate, template_id)
        if template is None or template.doc_type != doc_type or not template.enabled:
            raise AppError(ErrorCode.NOT_FOUND, "模板不存在、类型不符或已停用", 404)
        return template
    template = (
        await session.execute(
            select(BizDocTemplate)
            .where(BizDocTemplate.doc_type == doc_type, BizDocTemplate.enabled.is_(True))
            .order_by(BizDocTemplate.version.desc())
            .limit(1)
        )
    ).scalars().first()
    if template is None:
        await ensure_default_templates(session)
        template = (
            await session.execute(
                select(BizDocTemplate)
                .where(BizDocTemplate.doc_type == doc_type, BizDocTemplate.enabled.is_(True))
                .order_by(BizDocTemplate.version.desc())
                .limit(1)
            )
        ).scalars().first()
    if template is None:
        raise AppError(ErrorCode.NOT_FOUND, "该类型还没有可用模板", 404)
    return template


async def next_template_version(session: AsyncSession, doc_type: str) -> int:
    latest = (
        await session.execute(
            select(BizDocTemplate.version)
            .where(BizDocTemplate.doc_type == doc_type)
            .order_by(BizDocTemplate.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return int(latest or 0) + 1


async def list_templates(session: AsyncSession, doc_type: str | None = None) -> list[dict]:
    await ensure_default_templates(session)
    stmt = select(BizDocTemplate).order_by(
        BizDocTemplate.doc_type.asc(), BizDocTemplate.version.desc()
    )
    if doc_type:
        stmt = stmt.where(BizDocTemplate.doc_type == doc_type)
    return [serialize_template(row) for row in (await session.execute(stmt)).scalars().all()]


def serialize_template(template: BizDocTemplate) -> dict:
    return {
        "id": template.id,
        "doc_type": template.doc_type,
        "doc_type_label": DOC_TYPE_LABEL.get(template.doc_type, template.doc_type),
        "name": template.name,
        "version": template.version,
        "body": template.body,
        "enabled": template.enabled,
        "remark": template.remark,
        "created_at": template.created_at.isoformat() if template.created_at else None,
    }


def _fill_tokens(body: str, sources: dict[str, Any], extra: dict[str, str]) -> str:
    """填 {{customer.name}} / {{extra.xxx}} / {{today}}。

    认不出的 token **原样保留**——宁可让人看见"这里没填上"，
    也不要静默给个空值还以为填好了（与合同模板同一处理）。
    """
    import re

    def _replace(match: "re.Match[str]") -> str:
        token = match.group(1).strip()
        if token == "today":
            return datetime.now(UTC).date().isoformat()
        if token.startswith("extra."):
            return extra.get(token[len("extra."):].strip(), "")
        prefix, _, field = token.partition(".")
        obj = sources.get(prefix.strip())
        if obj is None or not field:
            return match.group(0)
        value = getattr(obj, field.strip(), None)
        return _text(value) if value is not None else match.group(0)

    return re.sub(r"\{\{([^}]+)\}\}", _replace, body or "")


# ---------------------------------------------------------------- 快照组装


async def _sku_map(session: AsyncSession, sku_ids: set[int]) -> dict[int, Any]:
    if not sku_ids:
        return {}
    from app.modules.product.model import Sku

    rows = (
        await session.execute(select(Sku).where(Sku.id.in_(sku_ids)))
    ).scalars().all()
    return {row.id: row for row in rows}


def _diff_lines(current: list[dict], source: list[dict], key: str) -> list[dict]:
    """本次明细 vs 来源明细的差异（场景12「修改本次不同内容并查看差异」）。

    按 `key`（SKU 或需求编号）配对；来源里没有的记"新增"。
    数量比对用字符串化的数值，避免 Decimal('5.000') 与 Decimal('5') 被判成不同。
    """
    source_by_key = {row.get(key): row for row in source if row.get(key) is not None}
    diffs: list[dict] = []
    for row in current:
        row_key = row.get(key)
        origin = source_by_key.get(row_key) if row_key is not None else None
        if origin is None:
            diffs.append(
                {
                    "item": row.get("name") or "-",
                    "field": "明细",
                    "before": None,
                    "after": f"新增（{_number(row.get('quantity'))}）",
                }
            )
            continue
        if _number(row.get("quantity")) != _number(origin.get("quantity")):
            diffs.append(
                {
                    "item": row.get("name") or "-",
                    "field": "数量",
                    "before": _number(origin.get("quantity")),
                    "after": _number(row.get("quantity")),
                }
            )
        if (row.get("name") or "") != (origin.get("name") or ""):
            diffs.append(
                {
                    "item": row.get("name") or "-",
                    "field": "名称",
                    "before": origin.get("name"),
                    "after": row.get("name"),
                }
            )
    return diffs


async def build_sample_request_doc(
    session: AsyncSession, sample_request_id: int
) -> dict:
    """组装打样需求单的快照（含来源询价与差异）。只读，不落库。"""
    from app.modules.customer.model import Customer
    from app.modules.inquiry.model import CustomInquiry
    from app.modules.sample.model import CONFIRM_STATUS_LABEL, SampleItem, SampleRequest

    sample = await session.get(SampleRequest, sample_request_id)
    if sample is None:
        raise AppError(ErrorCode.NOT_FOUND, "打样申请不存在", 404)
    items = (
        await session.execute(
            select(SampleItem)
            .where(SampleItem.sample_request_id == sample_request_id)
            .order_by(SampleItem.id.asc())
        )
    ).scalars().all()
    skus = await _sku_map(session, {i.sku_id for i in items if i.sku_id})

    current = [
        {
            "name": (skus[i.sku_id].name if i.sku_id in skus and skus[i.sku_id].name else None)
            or i.item_name
            or (skus[i.sku_id].sku_code if i.sku_id in skus else None)
            or "（未命名）",
            "spec": skus[i.sku_id].specification if i.sku_id in skus else None,
            "quantity": i.quantity,
            "remark": i.remark,
            "inquiry_id": i.inquiry_id,
        }
        for i in items
    ]

    # 来源：明细里第一条带着需求编号的——"这张打样单是从哪条需求来的"
    source_item = next((i for i in items if i.inquiry_id), None)
    inquiry = (
        await session.get(CustomInquiry, source_item.inquiry_id) if source_item else None
    )
    source_rows: list[dict] = []
    if inquiry is not None:
        source_rows = [
            {
                "name": inquiry.title,
                "quantity": inquiry.quantity,
                "inquiry_id": inquiry.id,
            }
        ]

    customer = await session.get(Customer, sample.customer_id) if sample.customer_id else None
    # 生产责任人：车间看的是人，不是 id
    from app.modules.user.model import User

    production_owner = (
        await session.get(User, sample.production_owner_id)
        if sample.production_owner_id
        else None
    )
    return {
        "customer": customer,
        "contact_id": sample.contact_id,
        "owner_id": sample.owner_id,
        "customer_id": sample.customer_id,
        "sample_request_id": sample.id,
        "inquiry_id": inquiry.id if inquiry else None,
        "items": current,
        "diffs": _diff_lines(current, source_rows, "inquiry_id"),
        "source": (
            {
                "type": "inquiry",
                "id": inquiry.id,
                "no": inquiry.inquiry_no or source_item.inquiry_no_snapshot,
                "version": inquiry.version,
            }
            if inquiry is not None
            else None
        ),
        "title_suffix": customer.name if customer else "",
        # 文档 §3.5 要求生产打样记录：用途、工艺/材质、图纸版本、样品数量、
        # 目标完成日、验收标准、费用和责任人。**没有这几项，这张单子发给车间
        # 是干不了活的**（不知道用什么材质、按哪版图纸、什么时候要、按什么验收）。
        # 样品数量不在这里重复：它在下面的明细里，一单可以多样。
        "sections": [
            {"label": "用途", "value": sample.purpose or ""},
            {
                "label": "工艺 / 材质",
                "value": " / ".join(x for x in (sample.craft, sample.material) if x),
            },
            {"label": "图纸版本", "value": sample.drawing_version or ""},
            {
                "label": "目标完成日",
                "value": (
                    sample.target_completion_date.isoformat()
                    if sample.target_completion_date
                    else ""
                ),
            },
            {"label": "验收标准", "value": sample.acceptance_criteria or ""},
            {
                "label": "打样费用",
                "value": _number(sample.sample_fee) if sample.sample_fee else "",
            },
            {
                "label": "生产责任人",
                "value": production_owner.name if production_owner else "",
            },
            # 客户确认与签收分开：车间关心的是"这批过没过"，不是"寄到了没有"
            {
                "label": "客户确认",
                "value": CONFIRM_STATUS_LABEL.get(sample.confirm_status, "")
                + (f"（{sample.customer_confirmed_at.date().isoformat()}）"
                   if sample.customer_confirmed_at else ""),
            },
            {"label": "打样要求", "value": sample.remark or ""},
        ],
    }


async def build_order_sheet_doc(session: AsyncSession, order_id: int) -> dict:
    """组装下单文件的快照（含来源报价与差异）。只读，不落库。"""
    from app.modules.customer.model import Customer
    from app.modules.order.model import SalesOrder, SalesOrderItem
    from app.modules.quote.model import Quote, QuoteItem, QuoteVersion

    order = await session.get(SalesOrder, order_id)
    if order is None:
        raise AppError(ErrorCode.NOT_FOUND, "订单不存在", 404)
    items = (
        await session.execute(
            select(SalesOrderItem)
            .where(SalesOrderItem.order_id == order_id)
            .order_by(SalesOrderItem.id.asc())
        )
    ).scalars().all()
    current = [
        {
            "name": i.sku_snapshot or "（未命名）",
            "spec": i.specification,
            "quantity": i.quantity,
            "unit_price": i.unit_price,
            "amount": i.amount,
            "remark": i.remark,
            "sku_id": i.sku_id,
        }
        for i in items
    ]

    # 来源：订单记着它从哪一版报价转过来的
    quote = await session.get(Quote, order.quote_id) if order.quote_id else None
    version_row = (
        await session.get(QuoteVersion, order.quote_version_id)
        if order.quote_version_id
        else None
    )
    quote_items: list[dict] = []
    if version_row is not None:
        rows = (
            await session.execute(
                select(QuoteItem)
                .where(QuoteItem.quote_version_id == version_row.id)
                .order_by(QuoteItem.id.asc())
            )
        ).scalars().all()
        quote_items = [
            {
                "name": r.sku_name_snapshot or r.inquiry_no_snapshot or "（未命名）",
                "quantity": r.quantity,
                "sku_id": r.sku_id,
            }
            for r in rows
        ]

    customer = await session.get(Customer, order.customer_id) if order.customer_id else None
    return {
        "customer": customer,
        "contact_id": None,
        "owner_id": order.owner_id,
        "customer_id": order.customer_id,
        "order_id": order.id,
        "quote_id": order.quote_id,
        "items": current,
        "diffs": _diff_lines(current, quote_items, "sku_id"),
        "source": (
            {
                "type": "quote",
                "id": order.quote_id,
                "no": quote.quote_no if quote else None,
                "version": version_row.version_no if version_row else None,
            }
            if order.quote_id
            else None
        ),
        "title_suffix": customer.name if customer else "",
        "sections": [
            {"label": "客户交期", "value": order.delivery_date.isoformat() if order.delivery_date else ""},
            {"label": "付款条件", "value": order.payment_terms or ""},
            {"label": "备注", "value": order.remark or ""},
        ],
    }


# ---------------------------------------------------------------- 生成


async def build_quote_doc(session: AsyncSession, quote_version_id: int) -> dict:
    """组装对客报价单快照（场景10）。只读，不落库。

    **金额一律取自报价版本的快照**（`quoted_price` / `quantity` 都是版本行上的
    快照字段），不按当前价格规则现算——否则客户手里的表会随价格维护悄悄变，
    "Excel 与对应报价版本金额一致"这条就永远保证不了。

    定制项（无 SKU）那一列按业务定下的口径显示**需求编号 + 产品名**：
    客户指着某一行问"这是哪个需求"时能对上号；编号留在内部版的做法被否掉了，
    因为对客沟通里"这一行是哪条需求"才是真正会被追问的。
    """
    from app.modules.customer.model import Customer
    from app.modules.quote.model import Quote, QuoteItem, QuoteVersion

    version_row = await session.get(QuoteVersion, quote_version_id)
    if version_row is None:
        raise AppError(ErrorCode.NOT_FOUND, "报价版本不存在", 404)
    quote = await session.get(Quote, version_row.quote_id)
    if quote is None:
        raise AppError(ErrorCode.NOT_FOUND, "报价单不存在", 404)

    rows = (
        await session.execute(
            select(QuoteItem)
            .where(QuoteItem.quote_version_id == version_row.id)
            .order_by(QuoteItem.id.asc())
        )
    ).scalars().all()

    items = []
    for row in rows:
        if row.sku_id is None:
            # 口径 (a)：需求编号 + 产品名
            name = " ".join(
                x for x in (row.inquiry_no_snapshot, row.sku_name_snapshot) if x
            ) or "（定制项）"
        else:
            name = row.sku_name_snapshot or row.sku_code_snapshot or "（未命名）"
        items.append(
            {
                "name": name,
                "spec": row.spec_snapshot,
                "quantity": row.quantity,
                "unit": "",
                "unit_price": row.quoted_price,
                # 明细金额由版本快照里的数量×单价得出，两者都取自同一快照，
                # 所以它不会随之后的价格维护变化
                "amount": (row.quoted_price or 0) * (row.quantity or 0),
                "remark": "",
                "sku_id": row.sku_id,
            }
        )

    customer = await session.get(Customer, quote.customer_id) if quote.customer_id else None
    return {
        "customer": customer,
        "contact_id": quote.contact_id,
        "owner_id": quote.owner_id,
        "customer_id": quote.customer_id,
        "inquiry_id": next((r.inquiry_id for r in rows if r.inquiry_id), None),
        "quote_id": quote.id,
        "items": items,
        "diffs": [],
        "source": {
            "type": "quote",
            "id": quote.id,
            "no": quote.quote_no,
            "version": version_row.version_no,
        },
        "title_suffix": customer.name if customer else "",
        # 合计取版本行的 total_amount（版本生成时就定死了）
        "total_amount": version_row.total_amount,
        "sections": [
            {
                "label": "有效期至",
                "value": quote.valid_until.isoformat() if quote.valid_until else "",
            },
        ],
    }


async def generate_quote_doc(
    session: AsyncSession,
    *,
    quote_version_id: int,
    user: CurrentUser,
    template_id: int | None = None,
    extra_fields: dict[str, str] | None = None,
) -> BizDoc:
    """从报价版本生成一份对客 Excel 报价单（不动报价单与版本）。"""
    from app.modules.quote.model import QuoteVersion

    version_row = await session.get(QuoteVersion, quote_version_id)
    if version_row is None:
        raise AppError(ErrorCode.NOT_FOUND, "报价版本不存在", 404)
    built = await build_quote_doc(session, quote_version_id)
    await ensure_in_scope(
        session, user, owner_id=built.get("owner_id"), label="报价单"
    )
    template = await current_template(session, "quote_sheet", template_id)
    return await _persist(
        session,
        built=built,
        doc_type="quote_sheet",
        template=template,
        user_id=user.id,
        extra_fields=extra_fields,
        source_ref=built.get("source"),
    )


def _content_hash(doc_no: str, template_version: int, snapshot: dict) -> str:
    """文件校验值（文档 §四「文件及校验值」）。

    为什么不是 PDF 字节的 SHA-256：reportlab 会把生成时间写进 PDF，
    同一份快照两次渲染出的字节流并不相同，那样的哈希**每次都变**，
    没法用来回答"这份文件的内容有没有被改过"。
    这里对**渲染输入**（单号 + 模板版本 + 快照）取规范化 JSON 的 SHA-256：
    内容一样哈希就一样、随时重算得出来可比对——这是它能起作用的前提。
    """
    payload = json.dumps(
        {"doc_no": doc_no, "template_version": template_version, "snapshot": snapshot},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _snapshot_for_storage(built: dict, body: str, template: BizDocTemplate) -> dict:
    """把组装结果转成可 JSON 序列化的快照（对象引用换成展示用的字段）。"""
    customer = built.get("customer")
    return _jsonable(
        {
            "customer_id": built.get("customer_id"),
            "customer_name": customer.name if customer else None,
            "contact_id": built.get("contact_id"),
            "items": built.get("items") or [],
            "diffs": built.get("diffs") or [],
            "sections": built.get("sections") or [],
            "source": built.get("source"),
            # 合计（报价单用）：取版本的 total_amount，不在这里对明细求和
            "total_amount": built.get("total_amount"),
            "body": body,
            "template": {"id": template.id, "name": template.name, "version": template.version},
        }
    )


async def _next_doc_version(
    session: AsyncSession, *, doc_type: str, sample_request_id: int | None, order_id: int | None
) -> tuple[int, int | None]:
    stmt: Select = select(BizDoc).where(BizDoc.doc_type == doc_type)
    if sample_request_id is not None:
        stmt = stmt.where(BizDoc.sample_request_id == sample_request_id)
    else:
        stmt = stmt.where(BizDoc.order_id == order_id)
    latest = (
        await session.execute(stmt.order_by(BizDoc.version.desc()).limit(1))
    ).scalars().first()
    if latest is None:
        return 1, None
    return int(latest.version) + 1, latest.id


async def _persist(
    session: AsyncSession,
    *,
    built: dict,
    doc_type: str,
    template: BizDocTemplate,
    user_id: int,
    extra_fields: dict[str, str] | None,
    source_ref: dict | None,
) -> BizDoc:
    from app.modules.settings import numbering

    rule_code = {
        "sample_request": "sample_doc",
        "order_sheet": "order_doc",
        "quote_sheet": "quote_doc",
    }[doc_type]
    version, parent_id = await _next_doc_version(
        session,
        doc_type=doc_type,
        sample_request_id=built.get("sample_request_id"),
        order_id=built.get("order_id"),
    )
    doc_no = await numbering.generate_for(
        session, rule_code, model=BizDoc, column=BizDoc.doc_no
    )
    sources = {
        "customer": built.get("customer"),
        "extra": extra_fields or {},
    }
    body = _fill_tokens(template.body, sources, {str(k): str(v) for k, v in (extra_fields or {}).items()})
    snapshot = _snapshot_for_storage(built, body, template)
    snapshot["extra"] = {str(k): str(v) for k, v in (extra_fields or {}).items()}

    doc = BizDoc(
        doc_no=doc_no,
        doc_type=doc_type,
        title=f"{DOC_TYPE_LABEL[doc_type]}-{built.get('title_suffix') or ''}".rstrip("-"),
        version=version,
        parent_id=parent_id,
        status="active",
        owner_id=built.get("owner_id"),
        customer_id=built.get("customer_id"),
        contact_id=built.get("contact_id"),
        sample_request_id=built.get("sample_request_id"),
        order_id=built.get("order_id"),
        inquiry_id=built.get("inquiry_id"),
        quote_id=built.get("quote_id"),
        source_type=(source_ref or {}).get("type"),
        source_id=(source_ref or {}).get("id"),
        source_no=(source_ref or {}).get("no"),
        source_version=(source_ref or {}).get("version"),
        template_id=template.id,
        template_version=template.version,
        input_snapshot=snapshot,
        content_sha256=_content_hash(doc_no, template.version, snapshot),
        created_by=user_id,
        created_at=datetime.now(UTC),
    )
    session.add(doc)
    await session.flush()
    return doc


async def generate_sample_request_doc(
    session: AsyncSession,
    *,
    sample_request_id: int,
    user: CurrentUser,
    template_id: int | None = None,
    extra_fields: dict[str, str] | None = None,
) -> BizDoc:
    """从打样申请生成一份打样需求单（不改动来源单据）。"""
    from app.modules.sample.model import SampleRequest

    sample = await session.get(SampleRequest, sample_request_id)
    if sample is None:
        raise AppError(ErrorCode.NOT_FOUND, "打样申请不存在", 404)
    await ensure_in_scope(session, user, owner_id=sample.owner_id, label="打样申请")
    built = await build_sample_request_doc(session, sample_request_id)
    template = await current_template(session, "sample_request", template_id)
    return await _persist(
        session,
        built=built,
        doc_type="sample_request",
        template=template,
        user_id=user.id,
        extra_fields=extra_fields,
        source_ref=built.get("source"),
    )


async def generate_order_sheet_doc(
    session: AsyncSession,
    *,
    order_id: int,
    user: CurrentUser,
    template_id: int | None = None,
    extra_fields: dict[str, str] | None = None,
) -> BizDoc:
    """从订单生成一份下单文件（不改动订单与来源报价）。"""
    from app.modules.order.model import SalesOrder

    order = await session.get(SalesOrder, order_id)
    if order is None:
        raise AppError(ErrorCode.NOT_FOUND, "订单不存在", 404)
    if order.status == "cancelled":
        raise AppError(ErrorCode.PARAM_ERROR, "已取消的订单不能出下单文件", 422)
    await ensure_in_scope(session, user, owner_id=order.owner_id, label="订单")
    built = await build_order_sheet_doc(session, order_id)
    template = await current_template(session, "order_sheet", template_id)
    return await _persist(
        session,
        built=built,
        doc_type="order_sheet",
        template=template,
        user_id=user.id,
        extra_fields=extra_fields,
        source_ref=built.get("source"),
    )


# ---------------------------------------------------------------- 查询


def serialize_doc(doc: BizDoc) -> dict:
    snapshot = doc.input_snapshot or {}
    return {
        "id": doc.id,
        "doc_no": doc.doc_no,
        "doc_type": doc.doc_type,
        "doc_type_label": DOC_TYPE_LABEL.get(doc.doc_type, doc.doc_type),
        "title": doc.title,
        "version": doc.version,
        "parent_id": doc.parent_id,
        "status": doc.status,
        "status_label": DOC_STATUS_LABEL.get(doc.status, doc.status),
        "owner_id": doc.owner_id,
        "customer_id": doc.customer_id,
        "customer_name": snapshot.get("customer_name"),
        "sample_request_id": doc.sample_request_id,
        "order_id": doc.order_id,
        "inquiry_id": doc.inquiry_id,
        "quote_id": doc.quote_id,
        "source": {
            "type": doc.source_type,
            "id": doc.source_id,
            "no": doc.source_no,
            "version": doc.source_version,
        },
        "template": {
            "id": doc.template_id,
            "version": doc.template_version,
            "name": (snapshot.get("template") or {}).get("name"),
        },
        "content_sha256": doc.content_sha256,
        "item_count": len(snapshot.get("items") or []),
        "diff_count": len(snapshot.get("diffs") or []),
        "void_reason": doc.void_reason,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
    }


async def get_doc_or_404(session: AsyncSession, doc_id: int) -> BizDoc:
    doc = await session.get(BizDoc, doc_id)
    if doc is None:
        raise AppError(ErrorCode.NOT_FOUND, "单据不存在", 404)
    return doc


async def list_docs(
    session: AsyncSession,
    user: CurrentUser,
    *,
    doc_type: str | None = None,
    sample_request_id: int | None = None,
    order_id: int | None = None,
    customer_id: int | None = None,
    limit: int = 100,
) -> list[dict]:
    stmt = select(BizDoc).order_by(BizDoc.id.desc()).limit(max(1, min(limit, 300)))
    owner_ids = await scoped_owner_ids(session, user)
    if owner_ids is not None:
        stmt = stmt.where(BizDoc.owner_id.in_(owner_ids))
    if doc_type:
        stmt = stmt.where(BizDoc.doc_type == doc_type)
    if sample_request_id is not None:
        stmt = stmt.where(BizDoc.sample_request_id == sample_request_id)
    if order_id is not None:
        stmt = stmt.where(BizDoc.order_id == order_id)
    if customer_id is not None:
        stmt = stmt.where(BizDoc.customer_id == customer_id)
    return [serialize_doc(row) for row in (await session.execute(stmt)).scalars().all()]


async def doc_pdf_data(session: AsyncSession, doc: BizDoc) -> dict:
    """把文件行还原成渲染参数——**只读快照**，不回查业务表。"""
    from app.modules.settings import service as settings_service

    snapshot = doc.input_snapshot or {}
    source = doc.source_type and {
        "label": {"inquiry": "来源询价", "quote": "来源报价"}.get(doc.source_type, "来源单据"),
        "no": doc.source_no,
        "version": doc.source_version,
    }
    return {
        "company_name": await settings_service.get_text(session, "company_name", "text", ""),
        "title": doc.title,
        "doc_no": doc.doc_no,
        "version": doc.version,
        "created_date": doc.created_at.strftime("%Y-%m-%d") if doc.created_at else None,
        "status_label": DOC_STATUS_LABEL.get(doc.status, doc.status),
        "customer_name": snapshot.get("customer_name"),
        "contact_name": None,
        "source": source or {},
        "template_version": doc.template_version,
        "items": snapshot.get("items") or [],
        "diffs": snapshot.get("diffs") or [],
        "sections": snapshot.get("sections") or [],
        "body": snapshot.get("body") or "",
        "total_amount": snapshot.get("total_amount"),
        "content_sha256": doc.content_sha256,
    }


async def void_doc(session: AsyncSession, doc: BizDoc, *, reason: str) -> None:
    """作废：状态改掉，**内容与校验值一个字节都不动**（作废不等于删档）。"""
    if doc.status == "void":
        raise AppError(ErrorCode.PARAM_ERROR, "该单据已作废", 422)
    doc.status = "void"
    doc.void_reason = reason
    await session.flush()
