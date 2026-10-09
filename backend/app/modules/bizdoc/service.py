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
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, NamedTuple

from sqlalchemy import Select, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.data_scope import ensure_in_scope, scoped_owner_ids
from app.core.deps import CurrentUser
from app.core.errors import AppError, ErrorCode
from app.modules.bizdoc import tokens as token_rules
from app.modules.bizdoc.model import (
    ARCHIVE_ARCHIVED,
    ARCHIVE_LEGACY,
    ARCHIVE_MISSING,
    DOC_STATUS_LABEL,
    DOC_TYPE_LABEL,
    BizDoc,
    BizDocTemplate,
    permitted_view_doc_types,
)

logger = logging.getLogger("crm.bizdoc")

#: 生成时把实际产出的字节存下来（§8.10）。下载原件读的就是它，不再"下载即重新渲染"。
#: 为什么这不是"多存一份冗余"：`content_sha256` 是对渲染**输入**算的，两次渲染
#: 同一份输入得到的字节并不相同（PDF 里有生成时间、Excel 包里有时间戳），
#: 只有存字节才能回答"当初发出去的就是这一份吗"。
ArchiveRender = Callable[[dict], bytes]

#: 历史值没留存下来时印在对外文件上的文案。
#: 为什么**不能留空、也不能拿当前资料补**：空白与"这一栏本来就没有"分不出来，
#: 而拿当前 SKU 单位 / 客户抬头去补，等于把今天的资料冒充成当时发出去的事实
#: （第八批 §8.7 的明确要求）。
NOT_RETAINED = "未留存（生成时未记录，待核实）"
#: 明细计价单位在报价版本行上确实没有（旧版本行没有 unit_snapshot 字段）时的文案。
UNIT_PENDING = "待核实"

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
        # 这里**不能**引用 order.*：订单草稿出图时还没有正式订单，没有这个来源可填，
        # 而不认得的 token 会按 _fill_tokens 的既定行为**原样印到对外 PDF 上**
        # （曾经就是 {{order.payment_terms}}，每一份下单文件/订单草稿都会印出这串语法）。
        # 付款条件已经在上面的 sections 里逐条渲染，正文不必再重复。
        "3. 付款条件以本文件所列付款条件栏为准。"
    ),
}

DEFAULT_TEMPLATE_NAME = "默认模板（待替换为正式模板）"

#: 下载时给"不是当初存档原件"的两类副本用的纸面文案（§8.10）。
#: 两句话必须分开：一种是"当初就没存"，一种是"存了但现在读不出来/被人另存"，
#: 处置方式完全不同（前者只能重建，后者要查原件去向）。
REBUILD_NOTICE = "由历史快照重建（非存档原件）"
STATE_COPY_NOTICE = "状态副本（依当前状态重出，非存档原件）"

#: 各类单据的出图程序：文件后缀 + 媒体类型 + 用哪个渲染器。
#: 版本号不在这里重复写一遍，而是问渲染器自己要（见 renderer_spec）——
#: 升级渲染器却漏改这里的版本号，就成了"版本号没变但出图变了"这种查不清的事。
_RENDERERS: dict[str, tuple[str, str, str]] = {
    "sample_request": (".pdf", "application/pdf", "pdf"),
    "order_sheet": (".pdf", "application/pdf", "pdf"),
    "quote_sheet": (
        ".xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "xlsx",
    ),
}


def renderer_spec(doc_type: str) -> tuple[str, str, str, str]:
    """出图程序规格：(后缀, 媒体类型, 渲染器种类, 版本号)。

    惰性导入 reportlab / openpyxl：列表、详情这些只读路径不该被出图依赖拖住。
    """
    from app.modules.bizdoc import pdf as pdf_renderer
    from app.modules.bizdoc import xlsx as xlsx_renderer

    suffix, media_type, kind = _RENDERERS.get(doc_type, _RENDERERS["order_sheet"])
    version = (
        pdf_renderer.RENDERER_VERSION
        if kind == "pdf"
        else xlsx_renderer.RENDERER_VERSION
    )
    return suffix, media_type, kind, version


def render_document(doc_type: str, data: dict) -> bytes:
    """按单据类型出图（同步 CPU 活，调用方负责丢线程池）。"""
    from app.modules.bizdoc.pdf import render_biz_doc_pdf
    from app.modules.bizdoc.xlsx import render_quote_xlsx

    _suffix, _media, kind, _version = renderer_spec(doc_type)
    if kind == "xlsx":
        return render_quote_xlsx(data)
    return render_biz_doc_pdf(data)


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _number(value: Any) -> str:
    """数量/金额去掉小数尾巴：5.000 → 5，2.500 → 2.5。"""
    if value is None:
        return ""
    try:
        number = Decimal(str(value))
        if number.is_finite():
            return f"{number.normalize():f}"
    except (InvalidOperation, ValueError):
        pass
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


# ------------------------------------------------- 来源身份 / 并发锁 / 请求幂等


def source_key_for(
    *,
    doc_type: str,
    sample_request_id: int | None = None,
    order_id: int | None = None,
    order_draft_id: int | None = None,
    quote_id: int | None = None,
) -> str | None:
    """一份对外单据的**来源身份键**（§8.9）：版本号只在这个键内连续。

    为什么把来源类型写进键里（`order:34` 与 `order_draft:34` 不同键）：
    订单草稿和它转出来的正式订单是**两条不同的历史链**——草稿 V3 与订单 V1
    互不相干，混成一条链会让正式订单莫名从 V4 起编。同理不同单据类型各自成链
    （打样单和下单文件的号段本来就不一样）。

    取不到来源时返回 None（`_persist` 直接被调用的测试/内部路径）：这类行不参与
    同源唯一约束，也不会被并发分配当成"别人的历史"。
    """
    if doc_type == "sample_request" and sample_request_id is not None:
        return f"sample_request:{sample_request_id}"
    if doc_type == "quote_sheet" and quote_id is not None:
        return f"quote:{quote_id}"
    if doc_type == "order_sheet":
        if order_draft_id is not None:
            return f"order_draft:{order_draft_id}"
        if order_id is not None:
            return f"order:{order_id}"
    return None


def _session_dialect(session: AsyncSession) -> str:
    """会话背后的数据库方言名；拿不到（测试替身）时返回空串。"""
    bind = getattr(session, "bind", None)
    if bind is None:
        return ""
    return getattr(getattr(bind, "dialect", None), "name", "") or ""


async def _lock_sequence(session: AsyncSession, key: str) -> bool:
    """把同一个序列的分配串起来（PostgreSQL 事务级咨询锁）。

    为什么需要它、而不是只靠唯一索引：唯一索引能挡住"同源同版本"落库，但挡住的
    方式是**报错**——用户看到的是 500/409，而本来只要排一下队就该拿到 V2。所以
    锁负责"排队拿到正确的下一号"，唯一索引负责"万一没排上也不许重复"。

    选咨询锁而不是 `SELECT ... FOR UPDATE` 锁某一行：空历史（还没有任何文件）
    时**没有行可锁**，正是并发探针两次都读到 V1 的原因；咨询锁不依赖行存在。

    锁是事务级的：本请求提交/回滚时自动释放，不需要（也不能）手工解锁。
    SQLite 上没有这个函数（单写者，测试替身走不到并发），直接跳过。
    """
    if _session_dialect(session) != "postgresql":
        return False
    digest = hashlib.sha256(f"bizdoc-seq:{key}".encode()).digest()[:8]
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": int.from_bytes(digest, "big", signed=True)},
    )
    return True


async def run_idempotent_generation(
    session: AsyncSession,
    *,
    user_id: int,
    action: str,
    request_key: str | None,
    payload: dict,
    generate: Callable[[], Awaitable[BizDoc]],
) -> tuple[BizDoc, bool]:
    """带请求幂等的生成：同一把请求键重试返回**原来那一份**，不再多出一份。

    返回 `(单据, 是不是回放)`。

    三条边界，缺一条都会出问题：
    1. **不给键 = 不做幂等**：返回 `(新生成, False)`，调用方必须在响应里说明
       "本次未带幂等键，重复提交可能生成多份"——不假装有幂等，那会让人以为
       重试是安全的（`core.idempotency.request_key_from` 的既定口径）；
    2. **同一把键 + 不同内容**：`reserve` 直接报冲突，不静默按新内容再生成一份
       ——那等于把"重复提交"变成"悄悄换了内容"；
    3. **明确要出新版**：前端每次点"生成"换一把新键，所以**内容完全相同也照样
       出新版本**。按内容去重会把"同一批货再出一份给工厂"这种合法需求永久挡掉，
       这是 §8.9 明确不许的。

    生成失败时释放占位：用户改完资料会带着同一把键重试，那次内容必然不同，
    占位不释放就会被误判成"同键不同内容"。
    """
    from app.core import idempotency

    key = (request_key or "").strip() or None
    if key is None:
        return await generate(), False
    reservation = await idempotency.reserve(
        session,
        user_id=user_id,
        action=action,
        request_key=key,
        payload=payload,
        result_type="biz_doc",
    )
    if reservation.should_replay:
        replayed = (
            await session.get(BizDoc, reservation.replay_id)
            if reservation.replay_id
            else None
        )
        if replayed is not None:
            return replayed, True
        # 记着的那份已经不在了（例如被清理）：键不能再复用，明确说清而不是
        # 悄悄按新内容生成——那会让两次请求拿到内容不同的两份"同一件事"。
        raise AppError(
            ErrorCode.VERSION_CONFLICT,
            "该请求键对应的单据已不存在，无法回放；请重新发起一次生成",
            409,
        )
    try:
        doc = await generate()
    except BaseException:
        await idempotency.release(session, reservation)
        raise
    await idempotency.complete(
        session,
        reservation,
        result_payload=serialize_doc(doc),
        result_id=doc.id,
    )
    return doc, False


# ---------------------------------------------------------------- 模板


async def ensure_default_templates(session: AsyncSession, user_id: int | None = None) -> None:
    """保证每种单据类型至少有一版可用模板（幂等）。

    与合同模块一致：业务还没给正式模板时不能因此开不出单，所以先落一份
    "默认模板（待替换为正式模板）"。之后业务模板到位，新增一版即可，
    历史文件仍指向它们当时用的版本。

    §8.9 补的并发纪律：两个请求同时第一次出图会各自判定"还没有默认模板"，
    于是双双插入同类型 V1，撞上 `uq_biz_doc_template_version` 变成 500。
    所以这里**按类型加锁后再复查一次**：锁住的是"这一版该不该由我建"，
    不是"我读到过没有"。SQLite（测试替身）没有咨询锁，退回原行为。
    """
    existing = set(
        (
            await session.execute(select(BizDocTemplate.doc_type).distinct())
        ).scalars().all()
    )
    for doc_type, label in DOC_TYPE_LABEL.items():
        if doc_type in existing:
            continue
        # 冷启动补默认模板与"新增一版模板"抢的是同一个版本号序列，
        # 所以用同一把锁（create_template_version 里是同一个 key）。
        await _lock_sequence(session, f"template:{doc_type}")
        again = (
            await session.execute(
                select(BizDocTemplate.id)
                .where(BizDocTemplate.doc_type == doc_type)
                .limit(1)
            )
        ).first()
        if again is not None:
            # 等锁期间别人已经建好了：不重复插，否则就是那个 500
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


async def create_template_version(
    session: AsyncSession,
    *,
    doc_type: str,
    name: str,
    body: str,
    enabled: bool,
    remark: str | None,
    user_id: int | None,
) -> BizDocTemplate:
    """新增一版模板，**版本号分配加锁**（§8.9）。

    `next_template_version` 单独用是"读最新 +1"，两个管理员同时保存就都会拿到
    同一个版本号，后者撞 `uq_biz_doc_template_version` 直接 500 —— 用户看到的是
    "保存失败"，其实只要排一下队就该存成 V(n+1)。所以：
    先锁同类型序列 → 再读版本 → 插入；万一仍然撞上（锁不可用/非 PostgreSQL），
    把唯一冲突翻成一句能照着处理的 409，而不是 500。

    不 commit：调用方（路由）继续写审计再一起提交，模板与审计同生共死。
    """
    await _lock_sequence(session, f"template:{doc_type}")
    version = await next_template_version(session, doc_type)
    template = BizDocTemplate(
        doc_type=doc_type,
        name=name,
        version=version,
        body=body,
        enabled=enabled,
        remark=remark,
        created_by=user_id,
        created_at=datetime.now(UTC),
    )
    session.add(template)
    try:
        await session.flush()
    except IntegrityError:
        # 回滚掉这一条半成品再报错：不回滚的话本事务已成"失败态"，
        # 路由层连写审计都会失败，用户看到的是另一条看不懂的错。
        await session.rollback()
        raise AppError(
            ErrorCode.VERSION_CONFLICT,
            f"{DOC_TYPE_LABEL.get(doc_type, doc_type)} 的模板 V{version} "
            "刚被另一位管理员保存，请刷新后重新提交（模板不会覆盖，只会新增一版）",
            409,
        ) from None
    return template


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
    """填模板正文，返回渲染结果（未解析的变量换成明确的"未解析"标记）。

    保留这个函数名是为了不打断既有调用方（合同之外只有本模块用）；
    真正的规则在 `tokens.py`：白名单校验、三种未解析原因、结果里不再残留
    `{{...}}`。**对外文件不得印出模板语法**——这是 §8.8 的硬要求，
    所以这里不再"原样保留 token"。
    """
    filled, _ = token_rules.resolve_tokens(body, sources, extra)
    return filled


# ---------------------------------------------------------------- 快照组装


async def _sku_map(session: AsyncSession, sku_ids: set[int]) -> dict[int, Any]:
    if not sku_ids:
        return {}
    from app.modules.product.model import Sku

    rows = (
        await session.execute(select(Sku).where(Sku.id.in_(sku_ids)))
    ).scalars().all()
    return {row.id: row for row in rows}


def _pair_key(row: dict, key: str | tuple[str, ...]):
    """按 `key` 给一行算配对键；取不到就返回 None（不参与配对）。

    支持复合键：定制件（无 SKU）两侧 sku_id 都是 None，只按 sku_id 配对会把
    每条定制行都当成"来源里没有"→客户收到的 PDF 凭空多出一堆差异。改用
    (sku_id, inquiry_id) 这种"哪个有值用哪个"的复合键，现货按 SKU 配、
    定制按需求编号配，两条路各归各。
    """
    if isinstance(key, str):
        value = row.get(key)
        return None if value is None else ((key, value),)
    parts = tuple((k, row.get(k)) for k in key if row.get(k) is not None)
    return parts or None


def _diff_lines(
    current: list[dict], source: list[dict], key: str | tuple[str, ...]
) -> list[dict]:
    """本次明细 vs 来源明细的差异（场景12「修改本次不同内容并查看差异」）。

    按 `key`（SKU 或需求编号）配对；来源里没有的记"新增"。
    数量比对用字符串化的数值，避免 Decimal('5.000') 与 Decimal('5') 被判成不同。
    """
    source_by_key = {}
    ambiguous = set()
    for row in source:
        pk = _pair_key(row, key)
        if pk is not None:
            if pk in source_by_key:
                ambiguous.add(pk)
            source_by_key[pk] = row
    diffs: list[dict] = []
    for row in current:
        row_key = _pair_key(row, key)
        if row_key in ambiguous:
            diffs.append({"item": row.get("name") or "-", "field": "来源匹配", "before": None,
                          "after": "历史来源有多条同产品明细且缺少原明细编号，无法逐条比较"})
            continue
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
        if origin.get("unit_price") is not None and _number(row.get("unit_price")) != _number(origin.get("unit_price")):
            diffs.append({"item": row.get("name") or "-", "field": "单价",
                          "before": _number(origin.get("unit_price")), "after": _number(row.get("unit_price"))})
        for field, label in (("spec", "规格"), ("remark", "备注")):
            if field in origin and (row.get(field) or "") != (origin.get(field) or ""):
                diffs.append({"item": row.get("name") or "-", "field": label,
                              "before": origin.get(field), "after": row.get(field)})
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

    current = []
    source_rows = []
    for item in items:
        sku = skus.get(item.sku_id)
        snapshot = item.source_snapshot
        current.append({
            "name": item.item_name if snapshot else ((sku.name if sku else None) or item.item_name or "（未命名）"),
            "spec": item.specification if snapshot else (sku.specification if sku else None),
            "quantity": item.quantity, "remark": item.remark,
            "inquiry_id": item.inquiry_id,
            "source_item_id": snapshot.get("source_item_id") if snapshot else None,
            "original_quantity": item.original_quantity,
            # 车间依据逐行不同，所以跟着明细走；PDF/XLSX 渲染时按需拼成一列
            "craft": item.craft,
            "material": item.material,
            "drawing_version": item.drawing_version,
        })
        if snapshot:
            source_rows.append({"name": snapshot.get("name"), "spec": snapshot.get("specification"),
                                "quantity": snapshot.get("original_quantity"), "remark": snapshot.get("remark"),
                                "source_item_id": snapshot.get("source_item_id")})
    source_item = next((i for i in items if i.inquiry_id), None)
    inquiry = await session.get(CustomInquiry, source_item.inquiry_id) if source_item else None
    if not sample.source_context and inquiry:
        source_rows = [{"name": inquiry.title, "quantity": inquiry.quantity, "inquiry_id": inquiry.id}]

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
        "diffs": _diff_lines(current, source_rows, "source_item_id" if sample.source_context else "inquiry_id"),
        "source": sample.source_context or (
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
        # 这里只放**整单属性**：用途、目标完成日、验收标准、费用、责任人。
        # 样品数量在下面的明细里（一单可以多样）；材质 / 工艺 / 图纸版本也在明细里
        # ——它们逐行不同，混进单头就只能写一份，一单多样时车间会照着一份做错。
        "sections": [
            *[{"label": f"{i.item_name or '明细'} 原采购数量 / 本次样品数量",
               "value": f"{_number(i.original_quantity) if i.original_quantity is not None else '未记录'} / {_number(i.quantity)}"}
              for i in items if i.source_snapshot],
            {"label": "用途", "value": sample.purpose or ""},
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
                # **只有 None 才是"未填"**。原写法 `if sample.sample_fee` 会把 0 元
                # （免费打样）判成假值、渲染成空白，与"还没填"混成一个样子——而这一列
                # 设计成可空的用意正是区分"免费"和"未填"（见 sample/model.py 的注释）。
                "value": "" if sample.sample_fee is None else _number(sample.sample_fee),
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
            # 定制件用需求编号配对（sku_id 两侧都是 None）
            "inquiry_id": i.inquiry_id,
            "quote_item_id": i.quote_item_id,
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
                "name": r.sku_name_snapshot or r.sku_code_snapshot or r.inquiry_no_snapshot or "（未命名）",
                "spec": r.spec_snapshot,
                "remark": r.remark,
                "quantity": r.quantity,
                "unit_price": r.quoted_price,
                "quote_item_id": r.id,
                "sku_id": r.sku_id,
                "inquiry_id": r.inquiry_id,
            }
            for r in rows
        ]

    exact_sources = [dict(i.source_snapshot, quote_item_id=i.quote_item_id) for i in items if i.source_snapshot and i.quote_item_id]
    exact_current = [row for row in current if row["quote_item_id"]]
    legacy_current = [row for row in current if not row["quote_item_id"]]
    differences = _diff_lines(exact_current, exact_sources, "quote_item_id") + _diff_lines(legacy_current, quote_items, ("sku_id", "inquiry_id"))
    customer = await session.get(Customer, order.customer_id) if order.customer_id else None
    return {
        "customer": customer,
        "contact_id": None,
        "owner_id": order.owner_id,
        "customer_id": order.customer_id,
        "order_id": order.id,
        "quote_id": order.quote_id,
        "items": current,
        "diffs": differences,
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
            {"label": "币种", "value": order.currency or ""},
            {"label": "客户交期", "value": order.delivery_date.isoformat() if order.delivery_date else ""},
            {"label": "付款条件", "value": order.payment_terms or ""},
            {"label": "备注", "value": order.remark or ""},
        ],
    }


# ---------------------------------------------------------------- 生成

#: 附加费类型 → 对客展示名（与前端报价页的 CHARGE_LABEL 保持同一套说法）
CHARGE_TYPE_LABEL = {
    "logistics": "运费",
    "packaging": "包装费",
    "tax": "税费",
    "discount": "折扣",
    "service": "服务费",
    "other": "其他费用",
}


async def _frozen_header(session: AsyncSession, *, quote, version_row, customer) -> dict:
    """按**版本锁定时点**冻结报价单抬头与条款，缺的记进 gaps（不拿当前资料补）。

    为什么值来自"当时"而不是"现在"：
    - 币种 / 付款条件 / 交付条件 / 贸易条款在 `QuoteVersion` 行上（版本生成时写入），
      读它们天然就是那一版的口径；
    - 有效期 / 客户名 / 联系人名同样取**版本快照**（2026-10-07 修）：这三列现在跟
      币种、条款一样钉在报价版本上，生成时直接读，不再实时查客户资料或报价主单。
      历史版本没有这些列 → 出图写「待核实」，不回填当前值
      （拿今天的客户名填进老版本，等于造一份假的留存证据）。
    - 负责人仍按生成时点取：他不参与对客内容口径，历史报价也追溯不到。
    """
    from app.modules.user.model import User

    gaps: list[dict] = []

    def _need(field: str, label: str, value: Any) -> Any:
        if value is None or (isinstance(value, str) and not value.strip()):
            gaps.append(
                {
                    "field": field,
                    "label": label,
                    "reason": "版本锁定或生成时未记录该字段",
                    "display": NOT_RETAINED,
                }
            )
            return None
        return value

    owner = (
        await session.get(User, quote.owner_id) if getattr(quote, "owner_id", None) else None
    )
    header = {
        # 抬头取**版本快照**（审查 2026-10-07 修）：原来实时读当前客户 / 联系人资料，
        # 客户改名之后同一个版本重出就印成新名字，与当初发给客户的那份对不上。
        # 历史版本没有留存 → `_need` 会记进 gaps、出图写「待核实」，不补当前值。
        "customer_name": _need(
            "customer_name", "客户", version_row.customer_name_snapshot
        ),
        "contact_name": _need(
            "contact_name", "联系人", version_row.contact_name_snapshot
        ),
        "owner_name": owner.name if owner else None,
        "quote_no": quote.quote_no,
        "version_no": version_row.version_no,
    }
    terms = {
        # 币种是 §8.7 的主诉：以前只从报价主单读、且 Excel 不显示，
        # 美元单打印出来和人民币单一模一样。
        "currency": version_row.currency,
        # 有效期同样取版本快照（原来读 `quote.valid_until` 的当前值：
        # 主单改了有效期，旧版本重出会跟着变）。
        "valid_until": (
            version_row.valid_until_snapshot.isoformat()
            if version_row.valid_until_snapshot
            else None
        ),
        "payment_terms": version_row.payment_terms,
        "delivery_terms": version_row.delivery_terms,
        "trade_terms": version_row.trade_terms,
    }
    for field, label in (
        ("valid_until", "有效期至"),
        ("payment_terms", "付款条件"),
        ("delivery_terms", "交付条件"),
        ("trade_terms", "贸易条款"),
    ):
        # 贸易条款内贸本来就空着（外贸才有 FOB/CIF），空不等于没留存，
        # 所以它不进 gaps；其余三项是对客条款，缺了必须显式告诉客户"待核实"。
        if field == "trade_terms":
            continue
        if terms[field] is None or (isinstance(terms[field], str) and not terms[field].strip()):
            gaps.append(
                {
                    "field": field,
                    "label": label,
                    "reason": "报价版本锁定或生成时未记录该字段",
                    "display": NOT_RETAINED,
                }
            )
    return {"header": header, "terms": terms, "gaps": gaps}


async def build_quote_doc(session: AsyncSession, quote_version_id: int) -> dict:
    """组装对客报价单快照（场景10）。只读，不落库。

    **金额一律取自报价版本的快照**（`quoted_price` / `quantity` 都是版本行上的
    快照字段），不按当前价格规则现算——否则客户手里的表会随价格维护悄悄变，
    "Excel 与对应报价版本金额一致"这条就永远保证不了。

    第八批 §8.7 追加：币种、计价单位、客户/联系人抬头、有效期、付款/交付及
    税运条款一起按版本冻结进快照，并在 Excel 上逐项展示。冻结之外**不新增
    任何隐式副作用**（生成 ≠ 发送，下载更不等于发送）。

    定制项（无 SKU）那一列按业务定下的口径显示**需求编号 + 产品名**：
    客户指着某一行问"这是哪个需求"时能对上号；编号留在内部版的做法被否掉了，
    因为对客沟通里"这一行是哪条需求"才是真正会被追问的。
    """
    from app.modules.customer.model import Customer
    from app.modules.quote.model import Quote, QuoteCharge, QuoteItem, QuoteVersion

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
    charge_rows = (
        await session.execute(
            select(QuoteCharge)
            .where(QuoteCharge.quote_version_id == version_row.id)
            .order_by(QuoteCharge.sort_no.asc(), QuoteCharge.id.asc())
        )
    ).scalars().all()

    customer = await session.get(Customer, quote.customer_id) if quote.customer_id else None
    frozen = await _frozen_header(
        session, quote=quote, version_row=version_row, customer=customer
    )
    gaps = frozen["gaps"]

    items = []
    frozen_items = []
    for row in rows:
        if row.sku_id is None:
            # 口径 (a)：需求编号 + 产品名
            name = " ".join(
                x for x in (row.inquiry_no_snapshot, row.sku_name_snapshot) if x
            ) or "（定制项）"
        else:
            name = row.sku_name_snapshot or row.sku_code_snapshot or "（未命名）"
        # 计价单位取**版本行上的快照**。旧版本行没有这个字段（None）时印"待核实"，
        # 绝不回查当前 SKU 的 unit——那会把今天的单位写成当时发出去的事实。
        unit = getattr(row, "unit_snapshot", None)
        if unit is None or not str(unit).strip():
            unit = UNIT_PENDING
            gaps.append(
                {
                    "field": f"items[{row.id}].unit",
                    "label": f"明细「{name}」计价单位",
                    "reason": "该报价版本行未记录计价单位（历史版本行没有单位快照）",
                    "display": UNIT_PENDING,
                }
            )
        amount = (row.quoted_price or 0) * (row.quantity or 0)
        items.append(
            {
                "name": name,
                "spec": row.spec_snapshot,
                "quantity": row.quantity,
                "unit": unit,
                "unit_price": row.quoted_price,
                # 明细金额由版本快照里的数量×单价得出，两者都取自同一快照，
                # 所以它不会随之后的价格维护变化
                "amount": amount,
                "remark": "",
                "sku_id": row.sku_id,
            }
        )
        frozen_items.append(
            {
                "name": name,
                "spec": row.spec_snapshot,
                "quantity": row.quantity,
                "unit": unit,
                "unit_price": row.quoted_price,
                "amount": amount,
            }
        )

    frozen["items"] = frozen_items
    #: 币种缺失是不可能的（版本行是 NOT NULL），但真出现时也要看得见：
    #: 宁可写"待核实"，也不出一张没写币种的对客金额表。
    if not (frozen["terms"].get("currency") or "").strip():
        frozen["terms"]["currency"] = None
        gaps.append(
            {
                "field": "currency",
                "label": "币种",
                "reason": "报价版本未记录币种",
                "display": NOT_RETAINED,
            }
        )
    frozen["gaps"] = gaps

    sections = [
        {"label": "币种", "value": frozen["terms"].get("currency") or NOT_RETAINED},
        {
            "label": "有效期至",
            "value": frozen["terms"].get("valid_until") or NOT_RETAINED,
        },
        {
            "label": "付款条件",
            "value": frozen["terms"].get("payment_terms") or NOT_RETAINED,
        },
        {
            "label": "交付条件",
            "value": frozen["terms"].get("delivery_terms") or NOT_RETAINED,
        },
    ]
    # 贸易条款内贸为空是正常的，不印一行"未留存"吓人；有值才展示（外贸 FOB/CIF）
    if (frozen["terms"].get("trade_terms") or "").strip():
        sections.append({"label": "贸易条款", "value": frozen["terms"]["trade_terms"]})
    sections.extend(
        [
            {"label": "客户", "value": frozen["header"].get("customer_name") or NOT_RETAINED},
            {
                "label": "联系人",
                "value": frozen["header"].get("contact_name") or NOT_RETAINED,
            },
        ]
    )

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
        # 按版本冻结的抬头/条款/明细单位：渲染器只认这一块，
        # 历史文件（这个键不存在）显示"未留存"，而不是回查当前资料
        "frozen": frozen,
        # 明细相加 ≠ 合计的根源：合计含附加费与优惠，但快照此前只带合计。
        # 把 小计 / 各费用行 / 优惠 一起带出来，对客 Excel 才能逐行列全、
        # 客户拿计算器加一遍正好等于合计（2026-10-04 口径：给客户看）。
        "subtotal_amount": version_row.subtotal_amount,
        "charge_amount": version_row.charge_amount,
        "discount_amount": version_row.discount_amount,
        # 金额拆分（2026-10-09「产品价格与运费分离」）：运费要从"附加费用"里单列，
        # 客户才能一眼看出"产品货款多少、运费多少"。这三个值**都已含在**
        # `total_amount` 里 —— 下面 charges 只是把同一笔钱分行展示，
        # 逐行相加正好等于合计，**不是**在合计之外又加一遍。
        "logistics_amount": version_row.logistics_amount,
        "other_charge_amount": version_row.other_charge_amount,
        "pricing_basis": version_row.pricing_basis,
        #: 产品单价的对外口径说明。运费分离之后，"单价是否含运费"必须在客户
        #: 看得到的表上说清楚，否则客户按旧口径理解会以为运费已经包在价里。
        "unit_price_note": "以上产品单价均不含运费",
        "charges": _quote_doc_charge_lines(charge_rows),
        # 合计取版本行的 total_amount（版本生成时就定死了）
        "total_amount": version_row.total_amount,
        "sections": sections,
    }


def _quote_doc_charge_lines(charge_rows: list) -> list[dict]:
    """对客文件上的费用行：**运费单独一行、其余归入"其他费用"**。

    只按**费用分类码**认运费（`is_logistics_charge`），不按说明文字里含"运费"
    去猜 —— 说明是自由文本，写成什么都可能。

    多条物流费用**按条累加**成一行（口径：允许多条时累加，不再另加整单运费），
    其余非折扣费用合成一行"其他费用"，折扣各自单列（库里是负数）。
    逐行相加仍等于 `charge_amount + discount_amount`，与合计自洽 ——
    这里只是把同一笔钱**拆开显示**，不是在合计之外又加一遍。

    值为 0 的行**不印**（运费为 0 的报价上多一行"运费 0.00"只是噪声；
    真正要客户看清的是"运费具体多少钱"这件事，有运费时它一定在）。
    """
    from app.modules.quote.service import is_logistics_charge

    discount_lines: list[dict] = []
    logistics_total = Decimal(0)
    logistics_desc: str | None = None
    other_total = Decimal(0)
    for charge in charge_rows:
        if charge.is_discount:
            discount_lines.append(
                {
                    "label": charge.description
                    or CHARGE_TYPE_LABEL.get(charge.charge_type, charge.charge_type),
                    "amount": charge.amount,
                    "is_discount": True,
                }
            )
        elif is_logistics_charge(charge):
            logistics_total += charge.amount or Decimal(0)
            logistics_desc = logistics_desc or charge.description
        else:
            other_total += charge.amount or Decimal(0)

    # 运费用业务员写的说明（如"宁波到苏州运费"）优先，它比一个笼统的"运费"更能对上账；
    # 没写说明才退回类型标签。这样既有明确的一行金额，又能看出这一趟运的是哪里。
    head: list[dict] = []
    if logistics_total:
        head.append(
            {
                "label": logistics_desc or CHARGE_TYPE_LABEL.get("logistics", "运费"),
                "amount": logistics_total,
                "is_discount": False,
            }
        )
    if other_total:
        head.append(
            {"label": "其他费用", "amount": other_total, "is_discount": False}
        )
    return head + discount_lines


async def generate_quote_doc(
    session: AsyncSession,
    *,
    quote_version_id: int,
    user: CurrentUser,
    template_id: int | None = None,
    extra_fields: dict[str, str] | None = None,
) -> BizDoc:
    """从报价版本生成一份对客 Excel 报价单（不动报价单与版本）。

    §8.14 复审（第三轮）修的"文件入口没接校验"：发送入口挡了主数据未确认，
    文件入口没挡 —— 同一张发不出去的报价，却能生成并归档一份
    `status=active`/「有效」的对客 Excel，用户直接就能拿出去。

    现在生成前执行**同一份**判据（`quote_service.master_confirmation_problems`：
    回查明细引用的那一版主数据快照里有没有名称/规格/单位）。不达标时：

    - **不拒绝**（内部草稿准备本来就该允许）；
    - 落成**草稿**（`allow_draft=True` → `status=draft`、标题带【草稿】、
      列表里有标识、不进正式台账），并把"缺什么"写进文件快照，页面上能解释。

    历史归档的读取不受影响：这里只管**新生成**的文件。
    """
    from app.modules.quote import service as quote_service
    from app.modules.quote.model import QuoteVersion

    version_row = await session.get(QuoteVersion, quote_version_id)
    if version_row is None:
        raise AppError(ErrorCode.NOT_FOUND, "报价版本不存在", 404)
    built = await build_quote_doc(session, quote_version_id)
    await ensure_in_scope(
        session, user, owner_id=built.get("owner_id"), label="报价单"
    )
    template = await current_template(session, "quote_sheet", template_id)

    master_problems = await quote_service.master_confirmation_problems(
        session, version_id=quote_version_id
    )
    draft_reason = None
    if master_problems:
        # **只标草稿原因，不动 `allow_draft`** —— 后者会把模板闸门一起关掉
        draft_reason = "主数据未确认：" + "；".join(master_problems)
    return await _persist(
        session,
        built=built,
        doc_type="quote_sheet",
        template=template,
        user_id=user.id,
        extra_fields=extra_fields,
        source_ref=built.get("source"),
        draft_reason=draft_reason,
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
    frozen = built.get("frozen") or {}
    return _jsonable(
        {
            "customer_id": built.get("customer_id"),
            # 抬头优先取**按版本冻结**的那一份：历史文件（没有 frozen 块）才回退到
            # 生成时抄下的 customer_name，两者都不会去回查当前客户资料
            "customer_name": (frozen.get("header") or {}).get("customer_name")
            or (customer.name if customer else None),
            "contact_id": built.get("contact_id"),
            "contact_name": (frozen.get("header") or {}).get("contact_name"),
            "items": built.get("items") or [],
            "diffs": built.get("diffs") or [],
            "sections": built.get("sections") or [],
            # 按报价版本冻结的币种/条款/单位/抬头（打样单与下单文件没有这一块）
            "frozen": frozen,
            "source": built.get("source"),
            # 合计（报价单用）：取版本的 total_amount，不在这里对明细求和
            "total_amount": built.get("total_amount"),
            # 对客 Excel 的金额区需要：小计 + 各项费用 + 优惠，最后才等于合计
            "subtotal_amount": built.get("subtotal_amount"),
            "charge_amount": built.get("charge_amount"),
            "discount_amount": built.get("discount_amount"),
            # 运费单独一列（2026-10-09 运费分离）：Excel 上要能看出"运费多少、
            # 其他费用多少"，而不是混在"附加费用"一个数里。两个值都已含在
            # `total_amount` 里，只是拆分展示。
            "logistics_amount": built.get("logistics_amount"),
            "other_charge_amount": built.get("other_charge_amount"),
            # 「以上产品单价均不含运费」这句要落进快照：文件一旦生成就冻结，
            # 事后重出（下载存档原件）也还是当时那句话。
            "unit_price_note": built.get("unit_price_note"),
            "charges": built.get("charges") or [],
            "body": body,
            "template": {"id": template.id, "name": template.name, "version": template.version},
        }
    )


async def _next_doc_version(
    session: AsyncSession,
    *,
    doc_type: str,
    sample_request_id: int | None,
    order_id: int | None,
    quote_id: int | None = None,
    order_draft_id: int | None = None,
) -> tuple[int, int | None]:
    stmt: Select = select(BizDoc).where(BizDoc.doc_type == doc_type)
    # **按单据类型选键**，不能用"哪个字段非空就按哪个"：
    # 报价单既没有 sample_request_id 也没有 order_id，那样会落进
    # `order_id == None` 的分支，把所有 order_id 为空的单据都算成它的历史版本
    # ——版本号会一路涨、旧文件链也会挂错（回归抓到过：期望 V2，实际 V5）。
    if doc_type == "sample_request":
        stmt = stmt.where(BizDoc.sample_request_id == sample_request_id)
    elif doc_type == "quote_sheet":
        stmt = stmt.where(BizDoc.quote_id == quote_id)
    elif order_draft_id is not None:
        stmt = stmt.where(BizDoc.order_draft_id == order_draft_id)
    else:
        stmt = stmt.where(BizDoc.order_id == order_id, BizDoc.order_draft_id.is_(None))
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
    allow_draft: bool = False,
    draft_reason: str | None = None,
) -> BizDoc:
    """落一份对外单据。

    §8.8 的闸门在这里：模板里引用的变量**填不出来**时，
    默认拒绝出图（422，并把缺失变量逐条写进报错），
    只有调用方明确 `allow_draft=True` 才落成 `draft` 草稿——
    草稿在列表里标"草稿"、不可作废、也不进正式台账。

    为什么闸门放在 `_persist` 而不是各入口：三个入口（打样/订单/报价）
    加订单草稿路径都走这里，漏一个就等于漏一条合法的"印出模板语法"的路。
    """
    from app.modules.settings import numbering

    rule_code = {
        "sample_request": "sample_doc",
        "order_sheet": "order_doc",
        "quote_sheet": "quote_doc",
    }[doc_type]
    # **先把同一个来源的版本分配串起来，再读"最新版本"**（§8.9）。
    # 顺序反了等于没锁：两个连接会双双读到"空历史"、双双分配 V1。
    # 锁的粒度是"共同来源"而不是整张表：不同来源并行出单互不等待。
    # 咨询锁按（类型 + 来源）取，所以打样单与下单文件、草稿与正式订单各排各的队。
    source_key = source_key_for(
        doc_type=doc_type,
        sample_request_id=built.get("sample_request_id"),
        order_id=built.get("order_id"),
        order_draft_id=built.get("order_draft_id"),
        quote_id=built.get("quote_id") if doc_type == "quote_sheet" else None,
    )
    # 来源确实取不到时（内部直调 `_persist` 的路径）也要按类型串一下：
    # 这类行不参与同源唯一约束，但"同一个类型下谁拿第几号"仍要有序，
    # 否则它们会互相抢同一个 V1 而谁也拦不住。
    await _lock_sequence(session, source_key or f"{doc_type}:unattributed")
    version, parent_id = await _next_doc_version(
        session,
        doc_type=doc_type,
        sample_request_id=built.get("sample_request_id"),
        order_id=built.get("order_id"),
        order_draft_id=built.get("order_draft_id"),
        quote_id=built.get("quote_id") if doc_type == "quote_sheet" else None,
    )
    doc_no = await numbering.generate_for(
        session, rule_code, model=BizDoc, column=BizDoc.doc_no
    )
    extra = {str(k): str(v) for k, v in (extra_fields or {}).items()}
    sources = {
        "customer": built.get("customer"),
    }
    # 自定义模板可以引用 {{order.xxx}}（默认模板此前就引用了 payment_terms，却没人
    # 提供这个来源，于是那串占位符原样印到了对外 PDF 上）。**有正式订单时**才提供它；
    # 订单草稿没有订单，此时引用 order.* 会被下面的闸门拦下（来源不可用）。
    if built.get("order_id"):
        from app.modules.order.model import SalesOrder

        sources["order"] = await session.get(SalesOrder, built["order_id"])
    body, unresolved = token_rules.resolve_tokens(template.body, sources, extra)
    leftover = token_rules.find_unresolved_syntax(body)
    if leftover:
        # resolve_tokens 不该留下语法；留下就是规则有洞，宁可 500 也不能印出去
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"模板正文仍有未替换的占位符 {leftover}，已阻止出图；请修改模板后重试",
            422,
        )
    if unresolved and not allow_draft:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            "模板变量未解析，不能作为有效对外文件：" + _issue_summary(unresolved),
            422,
        )
    # 落草稿的两种来源：
    #   ① 模板变量填不出来（§8.8）—— 此时 `unresolved` 非空；
    #   ② 调用方明确标了原因（§8.14 复审：主数据未确认的报价文件只能落草稿）。
    #
    # ⚠️ **不要用 `allow_draft` 表达第 ② 种**：它的原语义是"模板填不出来也出图"，
    # 一旦为真就同时跳过上面那道模板闸门 —— 实测会把"模板变量未解析"也放行
    # （打红 `test_unresolved_template_variable_blocks_formal_document`：
    # 本该 422 的请求变成 200）。两者必须是**独立**的信号。
    as_draft = bool(unresolved) or bool(draft_reason)
    snapshot = _snapshot_for_storage(built, body, template)
    if draft_reason:
        # 为什么是草稿，跟着文件一起存下来（页面上要能解释，事后也查得到）
        snapshot["draft_reason"] = draft_reason
    snapshot["extra"] = extra
    if unresolved:
        # 草稿的"缺什么"必须留在快照里：前端要展示给用户，事后也查得到
        snapshot["token_issues"] = unresolved
        snapshot["draft"] = True
    title_label = "订单草稿需求单" if built.get("order_draft_id") else DOC_TYPE_LABEL[doc_type]
    doc = BizDoc(
        doc_no=doc_no,
        doc_type=doc_type,
        title=(
            f"【草稿】{title_label}-{built.get('title_suffix') or ''}".rstrip("-")
            if as_draft
            else f"{title_label}-{built.get('title_suffix') or ''}".rstrip("-")
        ),
        version=version,
        parent_id=parent_id,
        # 草稿不是正式对外文件：状态栏/列表一眼能看出来，且不允许当有效件下载使用
        status="draft" if as_draft else "active",
        owner_id=built.get("owner_id"),
        customer_id=built.get("customer_id"),
        contact_id=built.get("contact_id"),
        sample_request_id=built.get("sample_request_id"),
        order_id=built.get("order_id"),
        order_draft_id=built.get("order_draft_id"),
        inquiry_id=built.get("inquiry_id"),
        quote_id=built.get("quote_id"),
        source_type=(source_ref or {}).get("type"),
        source_id=(source_ref or {}).get("id"),
        source_no=(source_ref or {}).get("no"),
        source_version=(source_ref or {}).get("version"),
        source_key=source_key,
        template_id=template.id,
        template_version=template.version,
        input_snapshot=snapshot,
        content_sha256=_content_hash(doc_no, template.version, snapshot),
        created_by=user_id,
        created_at=datetime.now(UTC),
    )
    session.add(doc)
    try:
        await session.flush()
    except IntegrityError:
        # 同源同版本已经存在（唯一索引 `uq_biz_docs_source_version` 拦住）。
        # 正常情况下咨询锁已经排过队，走到这里说明锁没起作用（例如非 PostgreSQL
        # 或锁被绕过）。**不能重试**：这次事务已经失败，继续读版本只会读到脏状态。
        # 明确翻成 409，让人看见"有人在同时出单，请重试"，而不是一个 500。
        await session.rollback()
        raise AppError(
            ErrorCode.VERSION_CONFLICT,
            f"同一来源下已存在 V{version}：可能刚刚有人在并发出单。"
            "请刷新后重新生成（重试会拿到下一个版本号，不会覆盖已出的文件）",
            409,
        ) from None
    # 生成即把**实际产出的字节**存档（§8.10）。放在同一个事务里：
    # 出图或文件登记失败 → 整条业务行一起回滚，不会留下"active 却没有原件"的单据。
    await _archive_original(session, doc, user_id=user_id)
    return doc


async def _archive_original(
    session: AsyncSession, doc: BizDoc, *, user_id: int | None
) -> None:
    """把这份单据**这次真正产出的字节**渲染并落盘，登记成文件后回填到行上。

    为什么必须在生成时做完、而不是等第一次下载：
    1. 下载才渲染的话，"同一编号两次下载内容不同"永远存在（升级渲染器/字体、
       改模板默认值、Excel 打包时间戳都会变），对外文件出这种事说不清谁为准；
    2. `content_sha256` 只能证明"渲染输入没变"，证明不了"发出去的字节没变"；
    3. 生成时就把原件钉死，事后任何渲染器升级都不会改动已出的那一份。

    失败补偿：写盘成功但登记失败（或随后的事务回滚）时删掉本次刚写下的那个对象。
    不删的话盘上会留下谁也认领不了的孤儿原件——`ops/deploy/verify_file_tree.sh`
    会把它们报成"盘上有、库里没有"，长期累积就分不清哪个是垃圾哪个是历史原件。
    """
    import asyncio

    from app.core.config import settings
    from app.modules.file import storage
    from app.modules.file.model import FileRecord

    suffix, media_type, _kind, renderer_version = renderer_spec(doc.doc_type)
    data = await doc_pdf_data(session, doc)
    content = await asyncio.to_thread(render_document, doc.doc_type, data)
    object_key, size, checksum = await storage.save_bytes(content, suffix)
    try:
        record = FileRecord(
            storage_provider=settings.storage_provider,
            object_key=object_key,
            file_name=f"{doc.doc_no}{suffix}",
            mime_type=media_type,
            size=size,
            checksum=checksum,
            uploaded_by=user_id,
        )
        session.add(record)
        await session.flush()
    except BaseException:
        storage.delete_object(object_key)
        raise
    doc.file_id = record.id
    doc.file_sha256 = checksum
    doc.file_size = size
    doc.renderer_version = renderer_version
    # 给路由层留的补偿线索：本请求最终 commit 失败时按它删掉这个对象。
    # 故意不做成数据库列——它只是"本次事务还没提交"的临时记号。
    doc._archived_object_key = object_key  # type: ignore[attr-defined]
    await session.flush()


async def commit_doc_generation(session: AsyncSession, doc: BizDoc) -> None:
    """提交生成结果；**提交失败就把本次落盘的原件删掉**。

    路由层原来直接 `await session.commit()`：commit 抛错（连接断、约束冲突）时
    数据库里没有这条单据，但盘上那份原件已经在 `_archive_original` 里写下了——
    一份没有任何记录指向的文件会一直躺着，事后既查不出来源也不敢删。
    """
    from app.modules.file import storage

    try:
        await session.commit()
    except BaseException:
        key = getattr(doc, "_archived_object_key", None)
        if key:
            storage.delete_object(key)
        raise


def _issue_summary(issues: list[dict]) -> str:
    """把未解析变量拼成一句能照着改的话（不带模板语法，免得被复制进正文）。"""
    parts = []
    for issue in issues[:5]:
        token = issue.get("token")
        reason = issue.get("label") or issue.get("reason")
        parts.append(f"{token}（{reason}）")
    more = f"，另有 {len(issues) - 5} 项" if len(issues) > 5 else ""
    return "；".join(parts) + more


# ---------------------------------------------------------------- 模板校验


def analyze_template_body(body: str) -> dict:
    """模板保存前的校验：变量写法必须受支持（§8.8）。

    为什么在保存时就要拦：模板是**一次写、长期用**的东西，等生成时才发现写错，
    用户面对的是"文件出不来"；正确的时机是"模板存不下去、并告诉他哪个变量写错了"。
    """
    return token_rules.analyze_template(body)


def preview_template_body(body: str, extra_fields: dict[str, str] | None = None) -> dict:
    """模板预览：用示例值填一遍，把"哪些变量填不出来"提前摆出来。

    这里只回 JSON 给界面看，**不产生任何对外文件**——所以示例值填不出来的项
    只作为提示。预览正文同样不残留模板语法。
    """
    sample = {
        "customer": {
            "name": "示例客户",
            "level": "A",
            "phone": "000-00000000",
            "address": "示例地址",
        },
        "order": {
            "order_no": "示例订单号",
            "currency": "CNY",
            "payment_terms": "示例付款条件",
            "delivery_date": "2026-01-01",
            "remark": "示例备注",
        },
    }
    filled, unresolved = token_rules.resolve_tokens(body, sample, extra_fields or {})
    return {
        "body": filled,
        "unresolved": unresolved,
        "analysis": token_rules.analyze_template(body),
        "status": "ok" if not unresolved else "has_unresolved",
    }


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
    allow_draft: bool = False,
) -> BizDoc:
    """从订单生成一份下单文件（不改动订单与来源报价）。

    `allow_draft=True` 时模板变量填不出来也出图，但落成 `draft` 草稿
    （正文里印的是"未解析"标记，绝不印模板语法）。
    """
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
        allow_draft=allow_draft,
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
        "order_draft_id": doc.order_draft_id,
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
        # 存档原件（§8.10）：这几项让"下载到的是原件还是重建副本"在列表/详情上
        # 就能看出来，不用点开下载再猜。`legacy` 的历史件不会因此报错，
        # 但界面必须能提示"这一份是按快照重建的"。
        "archive": {
            "status": archive_status_of(doc),
            "file_id": doc.file_id,
            "file_sha256": doc.file_sha256,
            "file_size": doc.file_size,
            "renderer_version": doc.renderer_version,
        },
        "item_count": len(snapshot.get("items") or []),
        "diff_count": len(snapshot.get("diffs") or []),
        # 草稿与"哪些变量没解析"要能被列表/详情直接读到：用户得知道该改什么
        "is_draft": doc.status == "draft",
        "token_issues": snapshot.get("token_issues") or [],
        # 按版本冻结时没留存下来的栏位（币种/单位/条款/抬头），列表上给出个数，
        # 打开文件能看到逐条"未留存/待核实"
        "frozen_gaps": (snapshot.get("frozen") or {}).get("gaps") or [],
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
    order_draft_id: int | None = None,
    quote_id: int | None = None,
    customer_id: int | None = None,
    limit: int = 100,
) -> list[dict]:
    # 逐类型过滤（§8.5）：以前列表统一按 order:view 授权——有报价权限、没有订单权限
    # 的人生成完报价单后在列表里看不到自己的文件，而只有订单权限的人却能看到报价文件。
    # 过滤放在 SQL 里而不是取回后再筛，否则 limit 会被筛掉的行占满，
    # 出现"明明有 3 份却只显示 1 份"的假空列表。
    permitted = permitted_view_doc_types(user)
    if not permitted:
        return []
    stmt = (
        select(BizDoc)
        .where(BizDoc.doc_type.in_(permitted))
        .order_by(BizDoc.id.desc())
        .limit(max(1, min(limit, 300)))
    )
    owner_ids = await scoped_owner_ids(session, user)
    if owner_ids is not None:
        stmt = stmt.where(BizDoc.owner_id.in_(owner_ids))
    if doc_type:
        # 显式筛了一个自己无权看的类型：返回空，而不是偷偷给别的类型
        if doc_type not in permitted:
            return []
        stmt = stmt.where(BizDoc.doc_type == doc_type)
    if sample_request_id is not None:
        stmt = stmt.where(BizDoc.sample_request_id == sample_request_id)
    if order_id is not None:
        stmt = stmt.where(BizDoc.order_id == order_id)
    if order_draft_id is not None:
        stmt = stmt.where(BizDoc.order_draft_id == order_draft_id)
    # 对客报价单按报价单挂（一单多版本共用一个 quote_id）
    if quote_id is not None:
        stmt = stmt.where(BizDoc.quote_id == quote_id)
    if customer_id is not None:
        stmt = stmt.where(BizDoc.customer_id == customer_id)
    return [serialize_doc(row) for row in (await session.execute(stmt)).scalars().all()]


async def doc_pdf_data(
    session: AsyncSession, doc: BizDoc, *, copy_notice: str | None = None
) -> dict:
    """把文件行还原成渲染参数——**只读快照**，不回查业务表。

    `frozen` 是按报价版本冻结的抬头/条款/单位（§8.7）；历史文件没有这一块，
    渲染器据此显示"未留存/待核实"，而不是回查当前客户或 SKU 资料冒充历史事实。

    `copy_notice`（§8.10）：这一份不是生成时存档的原件，而是现在按快照重出的
    （历史无存档 / 状态副本）。渲染器会把它印在纸面上——不印的话，一份"重建副本"
    就会被当成当初发给客户的那一份。
    """
    from app.modules.settings import service as settings_service

    snapshot = doc.input_snapshot or {}
    source = doc.source_type and {
        "label": {"inquiry": "来源询价", "quote": "来源报价", "quote_version": "来源报价"}.get(doc.source_type, "来源单据"),
        "no": doc.source_no,
        "version": doc.source_version,
    }
    frozen = snapshot.get("frozen") or {}
    header = frozen.get("header") or {}
    return {
        "company_name": await settings_service.get_text(session, "company_name", "text", ""),
        "title": doc.title,
        "doc_no": doc.doc_no,
        "version": doc.version,
        "created_date": doc.created_at.strftime("%Y-%m-%d") if doc.created_at else None,
        "status_label": DOC_STATUS_LABEL.get(doc.status, doc.status),
        "customer_name": snapshot.get("customer_name"),
        # 联系人同样只认快照里冻结的那一份（历史文件没有就是 None）
        "contact_name": snapshot.get("contact_name") or header.get("contact_name"),
        "owner_name": header.get("owner_name"),
        "source": source or {},
        "template_version": doc.template_version,
        "items": snapshot.get("items") or [],
        "diffs": snapshot.get("diffs") or [],
        "sections": snapshot.get("sections") or [],
        "frozen": frozen,
        # 草稿标记：草稿文件必须在纸面上就写明"不是正式对外文件"
        "is_draft": doc.status == "draft",
        "token_issues": snapshot.get("token_issues") or [],
        "body": snapshot.get("body") or "",
        "subtotal_amount": snapshot.get("subtotal_amount"),
        "charge_amount": snapshot.get("charge_amount"),
        "discount_amount": snapshot.get("discount_amount"),
        "charges": snapshot.get("charges") or [],
        "total_amount": snapshot.get("total_amount"),
        "content_sha256": doc.content_sha256,
        # 副本标记：渲染器据此在纸面上写明"这不是存档原件"
        "copy_notice": copy_notice,
        # 用 `getattr` 而不是直读：这一列是 §8.10 才加的，历史行/只读替身上可能没有。
        # 缺了它只该少一行元信息，不该让一份**历史文件**下载不出来——与
        # `build_quote_doc` 里 `getattr(row, "unit_snapshot", None)` 同一个理由。
        "renderer_version": getattr(doc, "renderer_version", None),
    }


def archive_status_of(doc: BizDoc) -> str:
    """这份单据的存档状态（只看库里的记录，不碰磁盘）。

    为什么"没有存档"必须是**显式**的一档而不是 None：历史文件与新增文件在
    界面/接口上长得一样，用户按"下载"时以为拿到的是当初那一份，实际是按快照
    重出的。要把这件事说出来，就得先有个字段能表达它。
    """
    return ARCHIVE_ARCHIVED if doc.file_id else ARCHIVE_LEGACY


async def load_archived_file(
    session: AsyncSession, doc: BizDoc
) -> tuple[bytes | None, str, str | None]:
    """读**存档原件**。返回 `(字节, 来源, 读不到的原因)`。

    来源三档（与 model.ARCHIVE_* 同一套）：
    - `archived`：读到了，且**重算的字节 SHA-256 与登记值一致**；
    - `legacy`  ：这份文件当初就没有存档（迁移前的历史行），只能按快照重建；
    - `missing` ：本该有存档，但文件不见了或校验值对不上——**绝不静默改用
      "按当前资料重新渲染"顶上**。那等于把一份内容已经无法证明的文件冒充成
      当初发出去的原件，比直接报错坏得多。

    校验值对不上也要当成读不到：`files.checksum` 与 `biz_docs.file_sha256`
    两处都记了同一个值，任一处被改动都能看出来；只有一致才敢说"这就是当时那份"。
    """
    from app.modules.file import storage
    from app.modules.file.model import FileRecord

    if not doc.file_id:
        return None, ARCHIVE_LEGACY, None
    record = await session.get(FileRecord, doc.file_id)
    if record is None:
        return None, ARCHIVE_MISSING, "文件登记记录不存在（files 表里查不到该编号）"
    import asyncio

    try:
        path = storage.absolute_path(record.object_key)
        content = await asyncio.to_thread(path.read_bytes)
    except (OSError, AppError) as exc:
        # AppError：object_key 非法（越界/穿越）——同样按"读不到原件"处理，
        # 不能因为路径非法就退回去重新渲染。
        return None, ARCHIVE_MISSING, f"存档文件读不到：{exc.__class__.__name__}"
    actual = hashlib.sha256(content).hexdigest()
    expected = doc.file_sha256 or record.checksum
    if expected and actual != expected:
        return None, ARCHIVE_MISSING, "存档文件的字节校验值与登记值不一致（可能被替换或损坏）"
    return content, ARCHIVE_ARCHIVED, None


#: 本次下载给出的到底是哪一份字节。**必须逐份分得开**（§8.10）：
#: - archived：生成时存档的原件，字节一致，就是当初发出去的那一份；
#: - rebuilt-from-snapshot：没有存档，现在按快照重出的（纸面写明"由历史快照重建"）；
#: - state-copy：按**当前状态**重出的副本（作废件要能一眼看到"已作废"），
#:   它不是原件，纸面上写明"状态副本"。
SOURCE_ARCHIVED = "archived"
SOURCE_REBUILT = "rebuilt-from-snapshot"
SOURCE_STATE_COPY = "state-copy"


class DownloadPlan(NamedTuple):
    content: bytes | None
    source: str
    notice: str | None
    blocked_reason: str | None


async def plan_download(
    session: AsyncSession,
    doc: BizDoc,
    *,
    mode: str = "original",
    allow_rebuild: bool = False,
) -> DownloadPlan:
    """决定这次下载给哪一份字节。

    `mode="original"`（默认）：优先给**存档原件**。
      - 有存档且校验通过 → 直接给，不再渲染（这就是"下载原件读存档"）；
      - 历史没有存档 → 按快照重出，但纸面标"由历史快照重建"；
      - 有存档却读不到/校验不符 → **拒绝**（blocked_reason 有值），
        除非调用方明确 `allow_rebuild=True`。不静默用当前资料重渲染顶上：
        那会把一份来源不明的文件冒充成当初发出去的原件。
    `mode="state"`：永远按快照重出并标"状态副本"，用于展示作废/草稿等**当前状态**。
      存档原件里印的是生成当时的"有效"，状态变化不会（也不该）改它。
    """
    if mode == "state":
        return DownloadPlan(None, SOURCE_STATE_COPY, STATE_COPY_NOTICE, None)
    content, source, reason = await load_archived_file(session, doc)
    if content is not None:
        return DownloadPlan(content, SOURCE_ARCHIVED, None, None)
    if source == ARCHIVE_LEGACY:
        return DownloadPlan(None, SOURCE_REBUILT, REBUILD_NOTICE, None)
    if not allow_rebuild:
        return DownloadPlan(None, ARCHIVE_MISSING, None, reason)
    return DownloadPlan(None, SOURCE_REBUILT, REBUILD_NOTICE, None)


async def void_doc(session: AsyncSession, doc: BizDoc, *, reason: str) -> None:
    """作废：状态改掉，**内容与校验值一个字节都不动**（作废不等于删档）。

    草稿不是正式对外文件，没有"作废"这一说：它本来就没进正式台账，
    允许作废只会让草稿看起来像一份正式件被处理过。要正式件就重新生成。
    """
    if doc.status == "draft":
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            "该单据是草稿（模板变量未解析），不是正式对外文件，无需作废；"
            "请修正模板或来源资料后重新生成正式版本",
            422,
        )
    if doc.status == "void":
        raise AppError(ErrorCode.PARAM_ERROR, "该单据已作废", 422)
    doc.status = "void"
    doc.void_reason = reason
    await session.flush()
