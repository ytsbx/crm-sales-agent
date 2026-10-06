"""外部事实的**定期对账与差异核定**（第八批 §8.13 分段三）。

## 这一层负责什么

    按期间取外部事实（订单 / 发货 / 售后） → 与本地订单对齐 → 算聚合 → 出差异
    → 人工核定（带权限、带审计） → 差异能点回原始证据

## 三条必须守住的口径

1. **拆单 / 合单 / 部分退换货不双算**
   聚合只按"稳定键"走：外部事实在 `external_records` 里已经被唯一键去重；
   发货/售后的归属从**行**上的 `order_key` 读（合单时一张发货单的行分属不同订单，
   只有行级归属说得清），拆单则天然是多张发货单指向同一订单。
   所以"同一批货算两遍"会立刻表现为 `shipped > ordered`，被 `over_shipped` 差异抓住 ——
   不双算不是靠人眼核对，而是有一个能自动报警的不变量。

2. **财务与业绩继续用已确认口径**
   本模块**只读**本地订单金额，只写差异队列；一行都不会写 `payment_records`、
   `receivable_plans`、`order_status_history`、`sales_orders.status`。
   对外部售后事实尤其如此："退货/退款核定的来源与时点"用户还没拍板
   （交接说明 §0.3 第 5 条），所以它只能以 `aftersale_needs_review` 差异的形式
   挂在队列里等人核定，**绝不能**直接冲减本地回款。

3. **差异能点回原始证据**
   每条差异的 `evidence` 存的是原始记录的稳定键 + 内容摘录，
   `diff_evidence()` 再按这些键把 `external_records` 里的**原始报文**取回来。
   没有这一步，"差异"就只是一句结论，无法复核。

## 幂等

`reconciliation_runs.run_key` = (系统, 店铺, 期间)，同一期间重复对账复用同一批次；
差异按 `diff_key` 幂等 upsert，且 `diff_key` 里含**双方值的摘要** ——
同一个差异重复出现不会刷屏，值变了才会生成新的一条（人工已核定的结论不被抹掉）。
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.modules.erp.adapter import get_adapter
from app.modules.integration.model import (
    ExternalObjectMapping,
    ExternalRecord,
    IntegrationDiff,
    ReconciliationRun,
)
from app.modules.integration.vocab import (
    ALL_SHOPS,
    DIFF_AFTERSALE_REVIEW,
    DIFF_AMOUNT_MISMATCH,
    DIFF_CROSS_SHOP_NUMBER,
    DIFF_DOMAIN_ORDER,
    DIFF_IGNORED,
    DIFF_MISSING_EXTERNAL,
    DIFF_MISSING_LOCAL,
    DIFF_NEVER_AUTO_APPLIES,
    DIFF_OPEN,
    DIFF_OVER_RETURNED,
    DIFF_OVER_SHIPPED,
    DIFF_RESOLVED,
    DIFF_UNMATCHED_CUSTOMER,
    DIFF_UNMATCHED_SKU,
    MATCH_CONFLICT,
    MATCH_PENDING,
    OBJECT_AFTERSALE,
    OBJECT_AFTERSALE_ITEM,
    OBJECT_ORDER,
    OBJECT_ORDER_ITEM,
    OBJECT_SHIPMENT,
    OBJECT_SHIPMENT_ITEM,
)
from app.modules.order.model import SalesOrder

#: 金额比较允许的误差（元）。用 Decimal 比，避免二进制浮点把 0.1 变成 0.1000000000000000055。
AMOUNT_TOLERANCE = Decimal("0.01")

#: 差异证据里每条原始报文最多留多少字符（超限只留开头并**明确标出被截断**）。
EVIDENCE_MAX_CHARS = 2000

#: 每种差异允许的核定结论。**收窄到"这一步真的做得了的事"**：
#: 缺本地对象时允许的结论只有"人工处理"——因为系统不能替他凭证建客户；
#: 而外部售后事实只允许"人工处理 + 写清来源"，不允许选"采纳外部值"（口径未定）。
ALLOWED_RESOLUTIONS: dict[str, tuple[str, ...]] = {
    DIFF_MISSING_LOCAL: ("manual",),
    DIFF_MISSING_EXTERNAL: ("manual", "ignore"),
    DIFF_AMOUNT_MISMATCH: ("keep_local", "take_external", "manual", "ignore"),
    DIFF_OVER_SHIPPED: ("manual", "ignore"),
    DIFF_OVER_RETURNED: ("manual", "ignore"),
    DIFF_CROSS_SHOP_NUMBER: ("ignore", "manual"),
    DIFF_UNMATCHED_SKU: ("manual",),
    DIFF_UNMATCHED_CUSTOMER: ("manual",),
    DIFF_AFTERSALE_REVIEW: ("manual",),
}

#: 这些差异**必须写结论说明**才能核定（只点一个按钮不算核定）。
REQUIRE_NOTE = (DIFF_AFTERSALE_REVIEW, DIFF_OVER_SHIPPED, DIFF_OVER_RETURNED)

#: 核定动作的说明文案。写清楚"点了这个按钮之后发生了什么"，
#: 免得有人以为核定完系统会自动去改回款。
RESOLUTION_LABELS = {
    "keep_local": "以本地为准（不采纳外部值）",
    "take_external": "以外部为准（仅记录结论，不自动改本地财务数据）",
    "manual": "人工已处理并留痕",
    "ignore": "不是差异",
}


def _now() -> datetime:
    return datetime.now(UTC)


def _period_bounds(period_start: date, period_end: date) -> tuple[datetime, datetime]:
    """把左闭右开的日期期间转成时间边界（UTC 零点）。"""
    if period_end <= period_start:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"对账期间必须是左闭右开且 end > start：收到 {period_start} → {period_end}",
            422,
        )
    return (
        datetime.combine(period_start, time.min, tzinfo=UTC),
        datetime.combine(period_end, time.min, tzinfo=UTC),
    )


def period_label(period_start: date, period_end: date) -> str:
    """期间标签，用于差异的 period 列与 diff_key（不能只靠自增号）。"""
    return f"{period_start.isoformat()}~{period_end.isoformat()}"


def build_diff_key(
    *,
    domain: str,
    system_type: str,
    shop_id: str | None,
    object_type: str | None,
    diff_type: str,
    subject: str | None,
    period: str | None = None,
    values: Any = None,
) -> str:
    """差异的稳定指纹（幂等 upsert 的依据）。

    `values` 参与哈希是刻意的：同一个对象上"同一种差异、同样的值"重复出现时
    不该再出一条（否则每轮对账都在刷屏，人就不看队列了）；
    而**值变了**必须出新的一条，否则"上次已核定"会把新的问题一起吞掉。
    """
    payload: dict[str, Any] = {
        "domain": domain,
        "system_type": system_type,
        "shop_id": shop_id or "",
        "object_type": object_type or "",
        "diff_type": diff_type,
        "subject": subject or "",
        "period": period or "",
    }
    if values is not None:
        raw_values = json.dumps(
            values, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
        )
        payload["values"] = hashlib.sha1(raw_values.encode("utf-8")).hexdigest()[:12]
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "diff-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:48]


def record_pointer(row: ExternalRecord) -> dict:
    """原始记录的**稳定指针**（差异点到它就算点到了原始证据）。"""
    return {
        "system_type": row.system_type,
        "shop_id": row.shop_id,
        "object_type": row.object_type,
        "dedupe_key": row.dedupe_key,
    }


def _excerpt(row: ExternalRecord) -> dict:
    """证据里带的记录摘要；原样报文超限只留开头，并明确标出被截断。"""
    payload = row.payload if isinstance(row.payload, dict) else None
    excerpt: dict[str, Any] = {
        "object_type": row.object_type,
        "dedupe_key": row.dedupe_key,
        "external_id": row.external_id,
        "external_code": row.external_code,
        "order_key": row.order_key,
        "kind": row.kind,
        "occurred_at": row.occurred_at.isoformat() if row.occurred_at else None,
        "quantity": None if row.quantity is None else str(row.quantity),
        "amount": None if row.amount is None else str(row.amount),
        "currency": row.currency,
        "collected_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
        "batch_id": row.last_batch_id,
        "raw_digest": row.raw_digest,
    }
    if payload is not None:
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        if len(text) <= EVIDENCE_MAX_CHARS:
            excerpt["payload"] = payload
        else:
            excerpt["payload_truncated"] = {
                "size_chars": len(text),
                "head": text[:EVIDENCE_MAX_CHARS],
                "note": "原始报文超过证据留存上限，只保留开头；完整报文见原始事实详情",
            }
    return excerpt


def build_evidence(rows: list[ExternalRecord]) -> dict:
    """把若干原始记录打成证据块（指针 + 摘要 + 涉及哪些采集批次）。"""
    batches = sorted({row.last_batch_id for row in rows if row.last_batch_id})
    return {
        "record_keys": [record_pointer(row) for row in rows],
        "records": [_excerpt(row) for row in rows],
        "collect_batch_ids": batches,
        "note": "证据指向 external_records 的原始报文；点开差异详情会按 record_keys 取回全文",
    }


async def _upsert_diff(
    session: AsyncSession,
    *,
    session_fields: dict[str, Any],
) -> tuple[IntegrationDiff, str]:
    """按 diff_key 幂等写一条差异；**人工已核定的结论不会被新一轮对账抹掉**。"""
    row = (
        await session.execute(
            select(IntegrationDiff).where(
                IntegrationDiff.diff_key == session_fields["diff_key"]
            )
        )
    ).scalars().first()
    if row is None:
        row = IntegrationDiff(**session_fields, status=DIFF_OPEN)
        session.add(row)
        await session.flush()
        return row, "created"

    changed = False
    for key in ("current_value", "incoming_value", "evidence", "period", "internal_id",
                "external_id", "field_name", "object_type", "shop_id"):
        if key in session_fields and getattr(row, key) != session_fields[key]:
            setattr(row, key, session_fields[key])
            changed = True
    await session.flush()
    return row, "updated" if changed else "unchanged"


def serialize_diff(row: IntegrationDiff) -> dict:
    return {
        "id": row.id,
        "domain": row.domain,
        "system_type": row.system_type,
        "shop_id": row.shop_id,
        "object_type": row.object_type,
        "internal_id": row.internal_id,
        "external_id": row.external_id,
        "field_name": row.field_name,
        "diff_type": row.diff_type,
        "period": row.period,
        "current_value": row.current_value,
        "incoming_value": row.incoming_value,
        "status": row.status,
        "resolution": row.resolution,
        "resolution_label": RESOLUTION_LABELS.get(row.resolution or "", row.resolution),
        "resolve_note": row.resolve_note,
        "resolved_by": row.resolved_by,
        "resolved_at": row.resolved_at,
        "allowed_resolutions": list(
            ALLOWED_RESOLUTIONS.get(row.diff_type, ("manual", "ignore"))
        ),
        "requires_note": row.diff_type in REQUIRE_NOTE,
        # 明确告诉调用方"核定不会自动改本地财务数据"：这是 §8.13 的口径，不是缺陷。
        "auto_applies_to_local": row.diff_type not in DIFF_NEVER_AUTO_APPLIES
        and row.domain == DIFF_DOMAIN_ORDER,
        "created_at": row.created_at,
        "evidence_keys": (row.evidence or {}).get("record_keys", []),
    }


def serialize_run(row: ReconciliationRun) -> dict:
    return {
        "id": row.id,
        "run_key": row.run_key,
        "system_type": row.system_type,
        "shop_id": row.shop_id,
        "period_start": row.period_start,
        "period_end": row.period_end,
        "status": row.status,
        "counters": row.counters,
        "last_error": row.last_error,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "started_by": row.started_by,
    }


def _collect_shop_filter(system_type: str, shop_id: str):
    """把 (系统, 店铺) 转成查询条件。

    `shop_id == "*"` 的语义是**全部店铺**（不加店铺条件），不是"店铺恰好叫星号" ——
    否则"跨店铺核对同一外部编号"这件事就永远查不出来，而那正是验收要看的
    "两店同编号不串"的反面证据。
    """
    conditions = [ExternalRecord.system_type == system_type]
    if shop_id and shop_id != ALL_SHOPS:
        conditions.append(ExternalRecord.shop_id == shop_id)
    return conditions


def _line_quantity(row: ExternalRecord) -> Decimal | None:
    return row.quantity


async def load_period_facts(
    session: AsyncSession,
    *,
    system_type: str,
    shop_id: str,
    start: datetime,
    end: datetime,
) -> dict:
    """取本期外部事实并建立归属映射（订单头 / 发货头 / 售后头 / 三类明细）。

    为什么分三步查而不是一次全表扫：这一期有几张订单是已知的（`order_keys`），
    发货/售后的归属键是订单键，由此能**有界地**把相关行取回来，
    不随外部数据总量增长。
    """
    shop_conditions = _collect_shop_filter(system_type, shop_id)
    orders = (
        await session.execute(
            select(ExternalRecord)
            .where(
                *shop_conditions,
                ExternalRecord.object_type == OBJECT_ORDER,
                ExternalRecord.occurred_at.isnot(None),
                ExternalRecord.occurred_at >= start,
                ExternalRecord.occurred_at < end,
            )
            .order_by(ExternalRecord.id.asc())
        )
    ).scalars().all()
    order_keys = {row.dedupe_key for row in orders}

    heads: list[ExternalRecord] = []
    if order_keys:
        heads = list(
            (
                await session.execute(
                    select(ExternalRecord)
                    .where(
                        *shop_conditions,
                        ExternalRecord.object_type.in_((OBJECT_SHIPMENT, OBJECT_AFTERSALE)),
                        func.coalesce(
                            ExternalRecord.order_key,
                            ExternalRecord.parent_key,
                            ExternalRecord.dedupe_key,
                        ).in_(order_keys),
                    )
                    .order_by(ExternalRecord.id.asc())
                )
            ).scalars().all()
        )
    head_by_key = {row.dedupe_key: row for row in heads}

    lines: list[ExternalRecord] = []
    # 归属条件**按需拼**，不用"哨兵值"占位。
    # 踩过的坑：原来用 `["\u0000"]` 当"没有归属条件"的占位串，SQLite 跑得过，
    # 到了 PostgreSQL 直接 `CharacterNotInRepertoireError: invalid byte sequence 0x00`
    # —— PG 的 text 列不接受 NUL 字节，连绑定参数都不行。
    attribution = []
    if order_keys:
        attribution.append(ExternalRecord.order_key.in_(order_keys))
    if head_by_key:
        attribution.append(ExternalRecord.parent_key.in_(list(head_by_key)))
    if attribution:
        lines = list(
            (
                await session.execute(
                    select(ExternalRecord)
                    .where(
                        *shop_conditions,
                        ExternalRecord.object_type.in_(
                            (OBJECT_ORDER_ITEM, OBJECT_SHIPMENT_ITEM, OBJECT_AFTERSALE_ITEM)
                        ),
                        or_(*attribution),
                    )
                    .order_by(ExternalRecord.id.asc())
                )
            ).scalars().all()
        )

    # 没有时间的外部事实**不进本期聚合**：硬塞进某一期会让跨期核对重复计算，
    # 所以只计数并在 counters 里如实报出来（人再决定怎么处理）。
    missing_time = int(
        (
            await session.execute(
                select(func.count())
                .select_from(ExternalRecord)
                .where(
                    *shop_conditions,
                    ExternalRecord.object_type == OBJECT_ORDER,
                    ExternalRecord.occurred_at.is_(None),
                )
            )
        ).scalar_one()
    )
    return {
        "orders": list(orders),
        "heads": heads,
        "head_by_key": head_by_key,
        "lines": lines,
        "missing_time_orders": missing_time,
    }


def _attribute_order_key(line: ExternalRecord, head_by_key: dict[str, ExternalRecord]) -> str | None:
    """一行事实最终属于哪张订单。

    优先用行上的 `order_key`（合单时同一发货单的行分属不同订单，只有行上说得清）；
    退回父单键、再退回父单自己的归属键。
    """
    if line.order_key:
        return line.order_key
    head = head_by_key.get(line.parent_key or "")
    if head is None:
        return None
    return head.order_key or head.parent_key or head.dedupe_key


def build_order_aggregates(facts: dict) -> dict[str, dict]:
    """把外部事实聚合成"每张订单、每个货"的数量表（**不双算的那一步**）。

    聚合键是**我方 SKU 编码**，不是对方的行号。为什么：行号在订单、发货单、售后单
    里各是各的编号，按行号聚合会把"同一批货的退货行"和"订单行"算成两行，
    反而造出假差异。按货聚合以后，拆单（同一 SKU 分多次发货）自动相加、
    部分退货自动落回同一个货上，超发/超退的判断才有意义。

    没有 SKU 编码的行（对方没给）退回用行键/稳定键做桶，**不会被丢掉**。
    """
    aggregates: dict[str, dict] = {}
    for order in facts["orders"]:
        aggregates[order.dedupe_key] = {
            "order": order,
            "lines": {},
        }
    head_by_key = facts["head_by_key"]
    for line in facts["lines"]:
        order_key = _attribute_order_key(line, head_by_key)
        if order_key is None or order_key not in aggregates:
            # 归属不到本期的订单（跨期发货是正常业务）：本期不聚合它，
            # 免得把上一期的订单量算进这一期。
            continue
        line_key = line.external_code or line.line_key or line.dedupe_key
        bucket = aggregates[order_key]["lines"].setdefault(
            line_key,
            {
                "ordered": Decimal(0),
                "shipped": Decimal(0),
                "returned": Decimal(0),
                "exchanged": Decimal(0),
                "missing_quantity": 0,
                "records": [],
            },
        )
        quantity = _line_quantity(line)
        bucket["records"].append(line)
        if quantity is None:
            # 没有数量就不参与"超发/超退"的判断，只如实记数。
            # 默认成 0 会把真实的超发掩盖掉（0 永远不大于订单量）。
            bucket["missing_quantity"] += 1
            continue
        if line.object_type == OBJECT_ORDER_ITEM:
            bucket["ordered"] += quantity
        elif line.object_type == OBJECT_SHIPMENT_ITEM:
            bucket["shipped"] += quantity
        elif line.object_type == OBJECT_AFTERSALE_ITEM:
            # 换货**不是退货**：混在一起会把净交付算少，这正是"部分退货/换货不双算"
            # 要求区分的地方。没给 kind 的售后行按"需要人工核定"处理（记 returned=0
            # 但由 aftersale_needs_review 差异兜住）。
            if line.kind == "return":
                bucket["returned"] += quantity
            elif line.kind == "exchange":
                bucket["exchanged"] += quantity
    return aggregates


async def _find_local_order(
    session: AsyncSession, *, system_type: str, order: ExternalRecord
) -> SalesOrder | None:
    """外部订单 → 本地订单：先按映射，再按我方单号。"""
    mapping = (
        await session.execute(
            select(ExternalObjectMapping).where(
                ExternalObjectMapping.system_type == system_type,
                ExternalObjectMapping.object_type == OBJECT_ORDER,
                ExternalObjectMapping.external_id == (order.external_id or order.dedupe_key),
                ExternalObjectMapping.internal_id.isnot(None),
            )
        )
    ).scalars().first()
    if mapping is not None:
        row = await session.get(SalesOrder, mapping.internal_id)
        if row is not None:
            return row
    if order.external_code:
        return (
            await session.execute(
                select(SalesOrder).where(SalesOrder.order_no == order.external_code)
            )
        ).scalars().first()
    return None


async def run_reconciliation(
    session: AsyncSession,
    *,
    period_start: date,
    period_end: date,
    shop_id: str = ALL_SHOPS,
    system_type: str | None = None,
    adapter: Any | None = None,
    operator_id: int | None = None,
    now: datetime | None = None,
) -> dict:
    """跑一次对账。同一 (系统, 店铺, 期间) 重复跑是**幂等**的。"""
    adapter = adapter or get_adapter()
    system = system_type or getattr(adapter, "system_type", "ERP")
    shop = (shop_id or "").strip() or ALL_SHOPS
    moment = now or _now()
    start, end = _period_bounds(period_start, period_end)
    label = period_label(period_start, period_end)
    run_key = f"{system}:{shop}:{label}"

    run = (
        await session.execute(
            select(ReconciliationRun).where(ReconciliationRun.run_key == run_key)
        )
    ).scalars().first()
    reused = run is not None
    if run is None:
        run = ReconciliationRun(
            run_key=run_key,
            system_type=system,
            shop_id=shop,
            period_start=period_start,
            period_end=period_end,
            status="running",
        )
        session.add(run)
        await session.flush()
    run.status = "running"
    run.last_error = None
    run.started_at = moment
    run.finished_at = None
    run.started_by = operator_id
    await session.flush()

    counts = {
        "external_orders": 0,
        "local_matched": 0,
        "missing_local": 0,
        "missing_external": 0,
        "amount_mismatch": 0,
        "over_shipped": 0,
        "over_returned": 0,
        "cross_shop_same_number": 0,
        "unmatched_sku": 0,
        "unmatched_customer": 0,
        "aftersale_pending_review": 0,
        "orders_with_exchange": 0,
        "skipped_no_time": 0,
        "lines_without_quantity": 0,
        "diffs_created": 0,
        "diffs_updated": 0,
        "diffs_unchanged": 0,
    }

    facts = await load_period_facts(
        session, system_type=system, shop_id=shop, start=start, end=end
    )
    counts["external_orders"] = len(facts["orders"])
    counts["skipped_no_time"] = facts["missing_time_orders"]
    aggregates = build_order_aggregates(facts)

    def tally(outcome: str) -> None:
        counts[f"diffs_{outcome}"] += 1

    # ---- ① 逐张外部订单：匹配本地订单、比金额、查超发/超退 ----------------
    for order_key, bucket in aggregates.items():
        order: ExternalRecord = bucket["order"]
        local = await _find_local_order(session, system_type=system, order=order)
        ordered_qty = sum((line["ordered"] for line in bucket["lines"].values()), Decimal(0))
        shipped_qty = sum((line["shipped"] for line in bucket["lines"].values()), Decimal(0))
        returned_qty = sum((line["returned"] for line in bucket["lines"].values()), Decimal(0))
        exchanged_qty = sum((line["exchanged"] for line in bucket["lines"].values()), Decimal(0))
        counts["lines_without_quantity"] += sum(
            line["missing_quantity"] for line in bucket["lines"].values()
        )
        if exchanged_qty:
            # 换货单独计数、**不冲减净交付**：把它算进退货会让发货量看起来少了一块。
            counts["orders_with_exchange"] += 1

        evidence_rows = [order] + [
            record for line in bucket["lines"].values() for record in line["records"]
        ]
        summary_values = {
            "ordered": str(ordered_qty),
            "shipped": str(shipped_qty),
            "returned": str(returned_qty),
            "exchanged": str(exchanged_qty),
        }

        if local is None:
            counts["missing_local"] += 1
            _, outcome = await _upsert_diff(
                session,
                session_fields={
                    "diff_key": build_diff_key(
                        domain=DIFF_DOMAIN_ORDER,
                        system_type=system,
                        shop_id=order.shop_id,
                        object_type=OBJECT_ORDER,
                        diff_type=DIFF_MISSING_LOCAL,
                        subject=order.dedupe_key,
                        period=label,
                    ),
                    "domain": DIFF_DOMAIN_ORDER,
                    "system_type": system,
                    "shop_id": order.shop_id,
                    "object_type": OBJECT_ORDER,
                    "external_id": order.external_id,
                    "internal_id": None,
                    "diff_type": DIFF_MISSING_LOCAL,
                    "period": label,
                    "current_value": None,
                    "incoming_value": {
                        "external_code": order.external_code,
                        "amount": None if order.amount is None else str(order.amount),
                        "currency": order.currency,
                        **summary_values,
                    },
                    "evidence": build_evidence(evidence_rows),
                },
            )
            tally(outcome)
        else:
            counts["local_matched"] += 1
            if order.amount is not None and abs(Decimal(local.total_amount) - order.amount) > AMOUNT_TOLERANCE:
                counts["amount_mismatch"] += 1
                values = {
                    "local": str(local.total_amount),
                    "external": str(order.amount),
                    "currency": order.currency or local.currency,
                }
                _, outcome = await _upsert_diff(
                    session,
                    session_fields={
                        "diff_key": build_diff_key(
                            domain=DIFF_DOMAIN_ORDER,
                            system_type=system,
                            shop_id=order.shop_id,
                            object_type=OBJECT_ORDER,
                            diff_type=DIFF_AMOUNT_MISMATCH,
                            subject=order.dedupe_key,
                            period=label,
                        ),
                        "domain": DIFF_DOMAIN_ORDER,
                        "system_type": system,
                        "shop_id": order.shop_id,
                        "object_type": OBJECT_ORDER,
                        "external_id": order.external_id,
                        "internal_id": local.id,
                        "diff_type": DIFF_AMOUNT_MISMATCH,
                        "period": label,
                        "current_value": {
                            "order_no": local.order_no,
                            "total_amount": str(local.total_amount),
                            "currency": local.currency,
                        },
                        "incoming_value": {
                            "amount": str(order.amount),
                            "currency": order.currency,
                            **summary_values,
                        },
                        "evidence": build_evidence(evidence_rows),
                    },
                )
                tally(outcome)

        if ordered_qty and shipped_qty > ordered_qty:
            counts["over_shipped"] += 1
            values = {"ordered": str(ordered_qty), "shipped": str(shipped_qty)}
            _, outcome = await _upsert_diff(
                session,
                session_fields={
                    "diff_key": build_diff_key(
                        domain=DIFF_DOMAIN_ORDER,
                        system_type=system,
                        shop_id=order.shop_id,
                        object_type=OBJECT_SHIPMENT_ITEM,
                        diff_type=DIFF_OVER_SHIPPED,
                        subject=order_key,
                        period=label,
                    ),
                    "domain": DIFF_DOMAIN_ORDER,
                    "system_type": system,
                    "shop_id": order.shop_id,
                    "object_type": OBJECT_SHIPMENT_ITEM,
                    "external_id": order.external_id,
                    "internal_id": local.id if local is not None else None,
                    "diff_type": DIFF_OVER_SHIPPED,
                    "period": label,
                    "current_value": {"ordered": str(ordered_qty), **summary_values},
                    "incoming_value": {"shipped": str(shipped_qty)},
                    "evidence": build_evidence(evidence_rows),
                },
            )
            tally(outcome)

        if returned_qty > shipped_qty:
            counts["over_returned"] += 1
            values = {"returned": str(returned_qty), "shipped": str(shipped_qty)}
            _, outcome = await _upsert_diff(
                session,
                session_fields={
                    "diff_key": build_diff_key(
                        domain=DIFF_DOMAIN_ORDER,
                        system_type=system,
                        shop_id=order.shop_id,
                        object_type=OBJECT_AFTERSALE_ITEM,
                        diff_type=DIFF_OVER_RETURNED,
                        subject=order_key,
                        period=label,
                    ),
                    "domain": DIFF_DOMAIN_ORDER,
                    "system_type": system,
                    "shop_id": order.shop_id,
                    "object_type": OBJECT_AFTERSALE_ITEM,
                    "external_id": order.external_id,
                    "internal_id": local.id if local is not None else None,
                    "diff_type": DIFF_OVER_RETURNED,
                    "period": label,
                    "current_value": {"shipped": str(shipped_qty), **summary_values},
                    "incoming_value": {"returned": str(returned_qty)},
                    "evidence": build_evidence(evidence_rows),
                },
            )
            tally(outcome)

    # ---- ② 本地推过、本期对方没有：可能漏采，也可能对方没建单 -------------
    mapping_stmt = select(ExternalObjectMapping).where(
        ExternalObjectMapping.system_type == system,
        ExternalObjectMapping.object_type == OBJECT_ORDER,
        ExternalObjectMapping.internal_id.isnot(None),
    )
    if shop != ALL_SHOPS:
        mapping_stmt = mapping_stmt.where(ExternalObjectMapping.shop_id == shop)
    mappings = (await session.execute(mapping_stmt)).scalars().all()
    seen_external = {order.external_id for order in facts["orders"] if order.external_id}
    for mapping in mappings:
        if not mapping.external_id or mapping.external_id in seen_external:
            continue
        local = await session.get(SalesOrder, mapping.internal_id)
        if local is None:
            continue
        counts["missing_external"] += 1
        values = {"order_no": local.order_no, "external_id": mapping.external_id}
        _, outcome = await _upsert_diff(
            session,
            session_fields={
                "diff_key": build_diff_key(
                    domain=DIFF_DOMAIN_ORDER,
                    system_type=system,
                    shop_id=mapping.shop_id,
                    object_type=OBJECT_ORDER,
                    diff_type=DIFF_MISSING_EXTERNAL,
                    subject=mapping.external_id,
                    period=label,
                ),
                "domain": DIFF_DOMAIN_ORDER,
                "system_type": system,
                "shop_id": mapping.shop_id,
                "object_type": OBJECT_ORDER,
                "external_id": mapping.external_id,
                "internal_id": local.id,
                "diff_type": DIFF_MISSING_EXTERNAL,
                "period": label,
                "current_value": {
                    "order_no": local.order_no,
                    "status": local.status,
                    "owner_id": local.owner_id,
                },
                "incoming_value": None,
                "evidence": {
                    "record_keys": [],
                    "records": [],
                    "collect_batch_ids": [],
                    "note": "本地已登记该外部单号，但本期采集里没有它：要么漏采，要么对方没建单",
                },
            },
        )
        tally(outcome)

    # ---- ③ 跨店铺同编号：本地按店铺分行不串，但必须显式提示 ----------------
    cross_stmt = (
        select(
            ExternalRecord.external_id,
            func.count(func.distinct(ExternalRecord.shop_id)),
        )
        .where(
            ExternalRecord.system_type == system,
            ExternalRecord.object_type == OBJECT_ORDER,
            ExternalRecord.external_id.isnot(None),
        )
        .group_by(ExternalRecord.external_id)
    )
    for external_id, shop_count in (await session.execute(cross_stmt)).all():
        if int(shop_count) <= 1:
            continue
        counts["cross_shop_same_number"] += 1
        rows = list(
            (
                await session.execute(
                    select(ExternalRecord).where(
                        ExternalRecord.system_type == system,
                        ExternalRecord.object_type == OBJECT_ORDER,
                        ExternalRecord.external_id == external_id,
                    )
                )
            ).scalars().all()
        )
        values = {"external_id": external_id, "shops": sorted(row.shop_id for row in rows)}
        _, outcome = await _upsert_diff(
            session,
            session_fields={
                "diff_key": build_diff_key(
                    domain=DIFF_DOMAIN_ORDER,
                    system_type=system,
                    shop_id=ALL_SHOPS,
                    object_type=OBJECT_ORDER,
                    diff_type=DIFF_CROSS_SHOP_NUMBER,
                    subject=external_id,
                ),
                "domain": DIFF_DOMAIN_ORDER,
                "system_type": system,
                "shop_id": ALL_SHOPS,
                "object_type": OBJECT_ORDER,
                "external_id": external_id,
                "internal_id": None,
                "diff_type": DIFF_CROSS_SHOP_NUMBER,
                "period": label,
                "current_value": None,
                "incoming_value": values,
                "evidence": build_evidence(rows),
            },
        )
        tally(outcome)

    # ---- ④ 待匹配的客户 / SKU：未知就待匹配，补齐后可重放 ------------------
    pending_stmt = select(ExternalObjectMapping).where(
        ExternalObjectMapping.system_type == system,
        ExternalObjectMapping.match_status.in_((MATCH_PENDING, MATCH_CONFLICT)),
    )
    if shop != ALL_SHOPS:
        pending_stmt = pending_stmt.where(ExternalObjectMapping.shop_id == shop)
    for mapping in (await session.execute(pending_stmt)).scalars().all():
        is_customer = mapping.object_type == "customer"
        is_sku = mapping.object_type in (
            "sku",
            OBJECT_ORDER_ITEM,
            OBJECT_SHIPMENT_ITEM,
            OBJECT_AFTERSALE_ITEM,
        )
        if not (is_customer or is_sku):
            continue
        diff_type = DIFF_UNMATCHED_CUSTOMER if is_customer else DIFF_UNMATCHED_SKU
        counts["unmatched_customer" if is_customer else "unmatched_sku"] += 1
        rows = list(
            (
                await session.execute(
                    select(ExternalRecord).where(
                        ExternalRecord.system_type == system,
                        ExternalRecord.shop_id == mapping.shop_id,
                        ExternalRecord.object_type == mapping.object_type,
                        ExternalRecord.dedupe_key == mapping.external_id,
                    )
                )
            ).scalars().all()
        )
        values = {
            "external_code": mapping.external_code,
            "match_status": mapping.match_status,
            "note": mapping.match_note,
        }
        _, outcome = await _upsert_diff(
            session,
            session_fields={
                "diff_key": build_diff_key(
                    domain=DIFF_DOMAIN_ORDER,
                    system_type=system,
                    shop_id=mapping.shop_id,
                    object_type=mapping.object_type,
                    diff_type=diff_type,
                    subject=mapping.external_id,
                    period=label,
                ),
                "domain": DIFF_DOMAIN_ORDER,
                "system_type": system,
                "shop_id": mapping.shop_id,
                "object_type": mapping.object_type,
                "external_id": mapping.external_id,
                "internal_id": mapping.internal_id,
                "diff_type": diff_type,
                "period": label,
                "current_value": None,
                "incoming_value": values,
                "evidence": build_evidence(rows),
            },
        )
        tally(outcome)

    # ---- ⑤ 外部售后事实：**只进队列，绝不自动冲减本地回款** ---------------
    aftersales = [row for row in facts["heads"] if row.object_type == OBJECT_AFTERSALE]
    for head in aftersales:
        counts["aftersale_pending_review"] += 1
        # 只带这张售后单**自己的**明细行：按订单把发货行也捞进来会让证据看起来
        # 像"售后改了发货量"，反而不好理解。
        detail_rows = [line for line in facts["lines"] if line.parent_key == head.dedupe_key]
        values = {
            "kind": head.kind,
            "amount": None if head.amount is None else str(head.amount),
            "occurred_at": head.occurred_at.isoformat() if head.occurred_at else None,
        }
        _, outcome = await _upsert_diff(
            session,
            session_fields={
                "diff_key": build_diff_key(
                    domain=DIFF_DOMAIN_ORDER,
                    system_type=system,
                    shop_id=head.shop_id,
                    object_type=OBJECT_AFTERSALE,
                    diff_type=DIFF_AFTERSALE_REVIEW,
                    subject=head.dedupe_key,
                    period=label,
                ),
                "domain": DIFF_DOMAIN_ORDER,
                "system_type": system,
                "shop_id": head.shop_id,
                "object_type": OBJECT_AFTERSALE,
                "external_id": head.external_id,
                "internal_id": None,
                "diff_type": DIFF_AFTERSALE_REVIEW,
                "period": label,
                "current_value": None,
                "incoming_value": {**values, "status": "待核定来源与时点"},
                "evidence": build_evidence([head] + detail_rows),
            },
        )
        tally(outcome)

    run.status = "finished"
    run.finished_at = _now()
    run.counters = counts
    await session.commit()
    return {
        "run": serialize_run(run),
        "counters": counts,
        # 同一期间重复触发会复用批次（差异按 diff_key 幂等）；如实告诉调用方。
        "reused_run": reused,
        "message": (
            f"对账完成（{label}）：外部订单 {counts['external_orders']} 张，"
            f"本地匹配 {counts['local_matched']} 张，"
            f"新增差异 {counts['diffs_created']} 条、更新 {counts['diffs_updated']} 条。"
            "外部售后事实只进待核定队列，不会自动冲减本地回款"
        ),
    }


# ---------------------------------------------------------------- 差异查询与核定


async def list_runs(
    session: AsyncSession, *, system_type: str | None = None, page: int = 1, page_size: int = 20
) -> tuple[list[dict], int]:
    stmt: Select = select(ReconciliationRun)
    if system_type:
        stmt = stmt.where(ReconciliationRun.system_type == system_type)
    total = int(
        (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    )
    rows = (
        await session.execute(
            stmt.order_by(ReconciliationRun.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return [serialize_run(row) for row in rows], total


async def list_diffs(
    session: AsyncSession,
    *,
    domain: str | None = None,
    status: str | None = None,
    diff_type: str | None = None,
    system_type: str | None = None,
    shop_id: str | None = None,
    period: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    stmt: Select = select(IntegrationDiff)
    if domain:
        stmt = stmt.where(IntegrationDiff.domain == domain)
    if status:
        stmt = stmt.where(IntegrationDiff.status == status)
    if diff_type:
        stmt = stmt.where(IntegrationDiff.diff_type == diff_type)
    if system_type:
        stmt = stmt.where(IntegrationDiff.system_type == system_type)
    if shop_id:
        stmt = stmt.where(IntegrationDiff.shop_id == shop_id)
    if period:
        stmt = stmt.where(IntegrationDiff.period == period)
    total = int(
        (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    )
    rows = (
        await session.execute(
            stmt.order_by(IntegrationDiff.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return [serialize_diff(row) for row in rows], total


async def get_diff(session: AsyncSession, diff_id: int) -> IntegrationDiff:
    row = await session.get(IntegrationDiff, diff_id)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, f"差异 #{diff_id} 不存在", 404)
    return row


async def diff_evidence(session: AsyncSession, diff: IntegrationDiff) -> dict:
    """差异详情 + **按证据指针取回的原始报文**（"点回原始证据"的落地）。

    为什么现查而不是把全文存在差异里：原始事实可能后续被重拉更新，
    差异详情要能反映"现在库里的原文"；同时差异里留了摘要与摘要值，
    两边一对比就知道原始记录有没有在核定前后被改过。
    """
    keys = (diff.evidence or {}).get("record_keys") or []
    records: list[dict] = []
    missing: list[dict] = []
    for key in keys:
        if not isinstance(key, dict):
            continue
        row = (
            await session.execute(
                select(ExternalRecord).where(
                    ExternalRecord.system_type == key.get("system_type"),
                    ExternalRecord.shop_id == key.get("shop_id"),
                    ExternalRecord.object_type == key.get("object_type"),
                    ExternalRecord.dedupe_key == key.get("dedupe_key"),
                )
            )
        ).scalars().first()
        if row is None:
            # 原始证据查不到要**明确报出来**，不能静默变成空列表：
            # 那会让人以为"这条差异没有证据"。
            missing.append(key)
            continue
        records.append(
            {
                "dedupe_key": row.dedupe_key,
                "object_type": row.object_type,
                "shop_id": row.shop_id,
                "external_id": row.external_id,
                "external_code": row.external_code,
                "order_key": row.order_key,
                "kind": row.kind,
                "quantity": None if row.quantity is None else str(row.quantity),
                "amount": None if row.amount is None else str(row.amount),
                "currency": row.currency,
                "occurred_at": row.occurred_at,
                "external_updated_at": row.external_updated_at,
                "raw_digest": row.raw_digest,
                "last_batch_id": row.last_batch_id,
                "payload": row.payload,
            }
        )
    return {
        "diff": serialize_diff(diff),
        "evidence_summary": diff.evidence,
        "records": records,
        "missing_records": missing,
    }


def _validate_resolution(diff: IntegrationDiff, resolution: str, note: str | None) -> None:
    allowed = ALLOWED_RESOLUTIONS.get(diff.diff_type)
    if allowed is None:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"差异类型 {diff.diff_type} 还没有定义允许的核定结论，"
            "先在 ALLOWED_RESOLUTIONS 里明确它的口径再核定",
            422,
        )
    if resolution not in allowed:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"{diff.diff_type} 只允许这些结论：{'、'.join(allowed)}；收到 {resolution}",
            422,
        )
    if diff.diff_type in REQUIRE_NOTE and not (note or "").strip():
        raise AppError(
            ErrorCode.REQUIRED_FIELD_MISSING,
            f"{diff.diff_type} 必须写清依据（退货/退款的来源与时点、超发/超退的处理方式）："
            "只点一个按钮不构成核定",
            422,
        )


async def confirm_diff(
    session: AsyncSession,
    diff: IntegrationDiff,
    *,
    resolution: str,
    note: str | None,
    operator_id: int | None,
    now: datetime | None = None,
) -> dict:
    """带权限与审计地核定一条差异。

    **这是唯一能让人工结论生效的入口**，它本身也只改差异行：
    外部事实**不会**因为有人点了"以外部为准"就去改本地回款/应收/业绩
    （§8.13：外部售后事实未经核定不得直接覆盖本地回款；这里连核定完也不覆盖，
    覆盖要走已确认口径的财务流程）。返回值里的 `applied_to_local` 就是这句实话。
    """
    resolution = (resolution or "").strip()
    _validate_resolution(diff, resolution, note)
    moment = now or _now()
    diff.status = DIFF_IGNORED if resolution == "ignore" else DIFF_RESOLVED
    diff.resolution = resolution
    diff.resolve_note = note
    diff.resolved_by = operator_id
    diff.resolved_at = moment
    await session.flush()
    return {
        "diff": serialize_diff(diff),
        "resolution_label": RESOLUTION_LABELS.get(resolution, resolution),
        # 对账差异一律不自动落地到本地财务数据；SKU 主数据差异的落地在 product/master.py
        # （那里有明确的字段白名单与版本快照）。
        "applied_to_local": False,
        "message": (
            f"已记录核定结论：{RESOLUTION_LABELS.get(resolution, resolution)}。"
            "本次核定只改差异行，不会自动改本地回款/应收/业绩；"
            "需要调整财务数据的按已确认口径的流程单独办理"
        ),
    }


__all__ = [
    "ALLOWED_RESOLUTIONS",
    "AMOUNT_TOLERANCE",
    "EVIDENCE_MAX_CHARS",
    "REQUIRE_NOTE",
    "RESOLUTION_LABELS",
    "build_diff_key",
    "build_evidence",
    "build_order_aggregates",
    "confirm_diff",
    "diff_evidence",
    "get_diff",
    "list_diffs",
    "load_period_facts",
    "list_runs",
    "period_label",
    "record_pointer",
    "run_reconciliation",
    "serialize_diff",
    "serialize_run",
]
