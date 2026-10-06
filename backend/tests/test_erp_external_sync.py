"""外部只读采集与对账（第八批 §8.13）：幂等、断点续拉、两店不串、拆合单不双算。

这些用例守的是交接说明 §8.13 点名的验收项，全部走**真函数 + 内存 SQLite**：

1. 同一外部订单重复拉取不重复（唯一键 + 内容摘要）；
2. 分页中断后续拉不漏，且重拉的那一页不会变成第二份数据；
3. 两店同编号不串（店铺进唯一键、进映射键）；
4. 拆单 / 合单 / 部分退货 / 换货不双算（行级归属 + 换货不计进退货）；
5. 未知客户/SKU 可待匹配，补齐后可重放（不重新取数）；
6. 跨期对账差异能点回**原始报文**；
7. 外部售后事实绝不自动写本地回款/应收/业绩；
8. 未验收的适配器**一次请求都不发**，并且如实落成 not_verified。

不连数据库、不连网络：外部系统一律用进程内替身，`fetch_records` 显式打桩。
真 PostgreSQL 上的并发租约与唯一约束见 `scripts/check_erp_external_sync.py`。
"""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from conftest import SyncSessionAsAsync, make_engine

from app.core.base import Base
from app.core.errors import AppError
from app.modules.customer.model import Customer
from app.modules.erp import collection as col
from app.modules.erp import reconcile as rec
from app.modules.erp.adapter import ErpNotVerified, ExternalPage, JushuitanAdapter
from app.modules.erp.adapter import settings as adapter_settings
from app.modules.integration.model import (
    ExternalMapping,
    ExternalObjectMapping,
    ExternalRecord,
    ExternalSourceRegistry,
    ExternalSyncWatermark,
    IntegrationDiff,
    IntegrationLog,
    ReconciliationRun,
)
from app.modules.integration.vocab import (
    CURSOR_FAILED,
    CURSOR_NOT_VERIFIED,
    MATCH_MATCHED,
    MATCH_PENDING,
)
from app.modules.order.model import SalesOrder, SalesOrderItem
from app.modules.payment.model import PaymentRecord, ReceivablePlan
from app.modules.product.model import Product, Sku

ADAPTER_SYSTEM = "ERP"
PERIOD_START = datetime(2026, 9, 1, tzinfo=UTC)
PERIOD_END = datetime(2026, 10, 1, tzinfo=UTC)


# ---------------------------------------------------------------- 夹具


def _engine():
    """内存库：conftest 的表 + 本项新增的承载表与产品/订单明细表。"""
    engine = make_engine()
    Base.metadata.create_all(
        engine,
        tables=[
            ExternalSyncWatermark.__table__,
            ExternalRecord.__table__,
            ExternalObjectMapping.__table__,
            ExternalSourceRegistry.__table__,
            ReconciliationRun.__table__,
            IntegrationDiff.__table__,
            # 采集留痕写在既有的集成日志表里；推单映射表也一起建，
            # 免得"共用集成基础设施"的用例因为少一张表而失败。
            IntegrationLog.__table__,
            ExternalMapping.__table__,
            Product.__table__,
            Sku.__table__,
            SalesOrderItem.__table__,
        ],
    )
    return engine


def _session():
    return SyncSessionAsAsync(Session(_engine()))


def _add(session, rows) -> None:
    """替身会话只实现了 `add`（故意不实现 add_all：缺了会立刻报错，不静默）。"""
    for row in rows:
        session.add(row)


def _order(
    order_no: str,
    *,
    amount: str = "100",
    owner_id: int = 1,
    customer_id: int = 1,
    erp_order_id: str | None = None,
) -> SalesOrder:
    return SalesOrder(
        order_no=order_no,
        customer_id=customer_id,
        owner_id=owner_id,
        sales_owner_id=owner_id,
        status="pending",
        total_amount=Decimal(amount),
        currency="CNY",
        erp_order_id=erp_order_id,
        created_at=datetime(2026, 9, 5, tzinfo=UTC),
    )


class FakeCollectAdapter:
    """只读采集替身：**按游标**给页，可指定"请求某个游标时抛一次异常"。

    为什么按游标而不是按调用次数：按次数的话，断点续拉时替身会跳过中间那页，
    于是"漏页"看起来像服务层的 bug，其实是替身没实现游标语义。
    """

    system_type = ADAPTER_SYSTEM
    label = "测试采集ERP"

    def __init__(self, pages):
        # pages: list[list[dict]] —— 游标 N 对应 pages[N-1]（None 对应第 0 页）
        self.pages = list(pages)
        self.calls: list[dict] = []
        #: 请求这个游标时抛一次异常（模拟分页中断）。
        self.fail_on_cursor: str | None = None
        #: 首次拉取（还没有游标）就抛一次异常。
        self.fail_first_call = False
        self.error: Exception | None = None
        self._failed = False

    def collection_object_types(self):
        return ("order", "shipment", "aftersale")

    async def fetch_records(self, *, object_type, shop_id, cursor=None, page_size=50):
        self.calls.append(
            {"object_type": object_type, "shop_id": shop_id, "cursor": cursor}
        )
        if not self._failed and (
            (self.fail_first_call and cursor is None)
            or (self.fail_on_cursor is not None and cursor == self.fail_on_cursor)
        ):
            self._failed = True
            raise self.error or RuntimeError("模拟分页中断")
        index = 0 if cursor is None else int(cursor.split("-")[1]) - 1
        items = self.pages[index] if 0 <= index < len(self.pages) else []
        has_more = index + 1 < len(self.pages)
        return ExternalPage(
            items=items,
            next_page_token=f"cursor-{index + 2}" if has_more else None,
            has_more=has_more,
            watermark=f"2026-09-30T00:00:0{index}",
        )


def _order_item(key: str, line: str, sku_code: str, qty: str, **extra) -> dict:
    item = {
        "object_type": "order_item",
        "external_id": f"{key}-L{line}",
        "external_code": sku_code,
        "parent_key": key,
        "order_key": key,
        "line_key": line,
        "quantity": qty,
        "occurred_at": "2026-09-05T10:00:00",
    }
    item.update(extra)
    return item


def _order_record(key: str, crm_no: str, amount: str = "100") -> dict:
    return {
        "object_type": "order",
        "external_id": key,
        "external_code": crm_no,
        "amount": amount,
        "currency": "CNY",
        "occurred_at": "2026-09-05T09:00:00",
        "payload": {"o_id": key, "so_id": crm_no, "pay_amount": amount},
    }


def _shipment(key: str, order_key: str, occurred: str = "2026-09-10T09:00:00") -> dict:
    return {
        "object_type": "shipment",
        "external_id": key,
        "parent_key": order_key,
        "order_key": order_key,
        "occurred_at": occurred,
        "payload": {"s_id": key},
    }


def _shipment_item(key: str, line: str, order_key: str, sku_code: str, qty: str) -> dict:
    return {
        "object_type": "shipment_item",
        "external_id": f"{key}-L{line}",
        "external_code": sku_code,
        "parent_key": key,
        "order_key": order_key,
        "line_key": line,
        "quantity": qty,
        "occurred_at": "2026-09-10T09:10:00",
    }


def _aftersale(key: str, order_key: str, kind: str, **extra) -> dict:
    item = {
        "object_type": "aftersale",
        "external_id": key,
        "parent_key": order_key,
        "order_key": order_key,
        "kind": kind,
        "occurred_at": "2026-09-20T09:00:00",
        "payload": {"as_id": key, "type": kind},
    }
    item.update(extra)
    return item


def _aftersale_item(key: str, line: str, order_key: str, kind: str, qty: str) -> dict:
    return {
        "object_type": "aftersale_item",
        "external_id": f"{key}-L{line}",
        "external_code": "SKU-A",
        "parent_key": key,
        "order_key": order_key,
        "line_key": line,
        "kind": kind,
        "quantity": qty,
        "occurred_at": "2026-09-20T09:05:00",
    }


def _collect(session, adapter, *, object_type="order", shop_id="SHOP-A", **kwargs):
    return asyncio.run(
        col.collect_once(
            session, object_type=object_type, adapter=adapter, shop_id=shop_id, **kwargs
        )
    )


# ---------------------------------------------------------------- 幂等与断点


def test_repeat_collection_does_not_duplicate():
    """同一外部订单重拉：只有一行事实，第二次算"重复（内容未变）"。"""
    session = _session()
    page = [_order_record("EXT-1", "CRM-1"), _order_item("EXT-1", "1", "SKU-A", "10")]
    first = _collect(session, FakeCollectAdapter([page]))
    assert (first["inserted"], first["duplicates"]) == (2, 0)

    again = _collect(session, FakeCollectAdapter([page]))
    assert again["inserted"] == 0
    assert again["duplicates"] == 2

    rows = asyncio.run(col.list_records(session, owner_ids=None, page_size=50))[0]
    assert len(rows) == 2, "重复拉取不得多出行"
    order_row = next(row for row in rows if row["object_type"] == "order")
    assert order_row["collected_count"] == 2, "重复次数要如实记下来"


def test_pagination_break_keeps_token_and_resumes_without_loss():
    """第 2 页取数时崩了：断点留着，续拉后三页数据齐全且不重复。"""
    session = _session()
    pages = [
        [_order_record("EXT-1", "CRM-1")],
        [_order_record("EXT-2", "CRM-2")],
        [_order_record("EXT-3", "CRM-3")],
    ]
    broken = FakeCollectAdapter(pages)
    broken.fail_on_cursor = "cursor-2"

    with pytest.raises(RuntimeError):
        _collect(session, broken)

    cursor = asyncio.run(col.list_cursors(session))[0]
    assert cursor["status"] == CURSOR_FAILED
    assert cursor["page_token"], "失败时必须保留断点（否则续拉会漏页）"
    assert cursor["resumable"] is True
    assert cursor["page_no"] == 2, "断点应停在还没落完的那一页"
    assert cursor["retry_count"] == 1

    resumed = _collect(session, broken)  # 恢复：从断点续
    assert resumed["status"] == "idle"
    final = asyncio.run(col.list_cursors(session))[0]
    assert final["page_token"] is None, "采完之后断点必须清掉"
    assert final["status"] == "idle"

    rows = asyncio.run(col.list_records(session, owner_ids=None, page_size=50))[0]
    keys = sorted(row["dedupe_key"] for row in rows)
    assert keys == ["EXT-1", "EXT-2", "EXT-3"], "续拉后不缺页也不重复"
    # 第一页在崩溃前已经落库，续拉没有再取它
    assert broken.calls[0]["cursor"] is None
    assert broken.calls[1]["cursor"] == "cursor-2"


def test_break_and_resume_recounts_duplicates_instead_of_rewriting():
    """续拉时若对方把已经落过的那一页又给了一遍：只累加次数，不写第二行。"""
    session = _session()
    page = [_order_record("EXT-1", "CRM-1")]
    _collect(session, FakeCollectAdapter([page]))

    # 模拟"断点在第二页、但对方从第一页重新给"（接口不支持真游标时的常见情形）
    again = _collect(
        session, FakeCollectAdapter([page, [_order_record("EXT-2", "CRM-2")]])
    )
    assert again["duplicates"] == 1 and again["inserted"] == 1
    rows = asyncio.run(col.list_records(session, owner_ids=None, page_size=50))[0]
    assert len(rows) == 2


def test_two_shops_with_same_number_stay_separate():
    """A 店与 B 店用同一个外部单号：两行事实、两个映射，互不覆盖。"""
    session = _session()
    _collect(
        session, FakeCollectAdapter([[_order_record("EXT-1", "CRM-A")]]), shop_id="SHOP-A"
    )
    _collect(
        session, FakeCollectAdapter([[_order_record("EXT-1", "CRM-B")]]), shop_id="SHOP-B"
    )

    rows = asyncio.run(col.list_records(session, owner_ids=None, page_size=50))[0]
    assert len(rows) == 2
    assert {row["shop_id"] for row in rows} == {"SHOP-A", "SHOP-B"}
    assert {row["external_code"] for row in rows} == {"CRM-A", "CRM-B"}

    mappings = asyncio.run(col.list_mappings(session, owner_ids=None, page_size=50))[0]
    assert len(mappings) == 2
    assert {row["shop_id"] for row in mappings} == {"SHOP-A", "SHOP-B"}


def test_line_item_without_parent_key_is_rejected_with_position():
    """明细行不给父单键：当场报错并指出是第几条（静默丢弃会让差异查不出来源）。"""
    session = _session()
    broken = [
        _order_record("EXT-1", "CRM-1"),
        {"object_type": "order_item", "external_id": "L1", "line_key": "1", "quantity": "1"},
    ]
    with pytest.raises(AppError) as caught:
        _collect(session, FakeCollectAdapter([broken]))
    assert "第 2 条" in caught.value.message
    assert "parent_key" in caught.value.message


# ---------------------------------------------------------------- 不双算


def _split_merge_fixture() -> FakeCollectAdapter:
    """拆单 + 合单 + 部分退货 + 换货的一套外部事实。"""
    pages = [
        # 第 1 页：两张订单 + 订单行
        [
            _order_record("EXT-O1", "CRM-O1", "100"),
            _order_record("EXT-O2", "CRM-O2", "30"),
            _order_item("EXT-O1", "1", "SKU-A", "10"),
            _order_item("EXT-O2", "1", "SKU-C", "3"),
        ],
        # 第 2 页：拆单（O1 分两次发货）+ 合单（SHIP-1 同时带 O2 的货）
        [
            _shipment("SHIP-1", "EXT-O1"),
            _shipment_item("SHIP-1", "1", "EXT-O1", "SKU-A", "6"),
            _shipment_item("SHIP-1", "2", "EXT-O2", "SKU-C", "3"),
            _shipment("SHIP-2", "EXT-O1"),
            _shipment_item("SHIP-2", "1", "EXT-O1", "SKU-A", "4"),
        ],
        # 第 3 页：部分退货 2 件 + 换货 3 件（换货不能算退货）
        [
            _aftersale("AS-1", "EXT-O1", "return"),
            _aftersale_item("AS-1", "1", "EXT-O1", "return", "2"),
            _aftersale_item("AS-1", "2", "EXT-O1", "exchange", "3"),
        ],
    ]
    return FakeCollectAdapter(pages)


def test_split_and_merge_are_not_double_counted():
    """拆单/合单/部分退换货：按货归属聚合，发货量既不重复也不被换货顶掉。"""
    session = _session()
    adapter = _split_merge_fixture()
    # 三类对象各采一次（明细对象在页里自己声明类型，所以三类事实都装得进来）
    _collect(session, adapter, object_type="order")
    _collect(session, adapter, object_type="shipment")
    _collect(session, adapter, object_type="aftersale")

    facts = asyncio.run(
        rec.load_period_facts(
            session,
            system_type=ADAPTER_SYSTEM,
            shop_id="*",
            start=PERIOD_START,
            end=PERIOD_END,
        )
    )
    aggregates = rec.build_order_aggregates(facts)

    # 聚合键是**我方 SKU 编码**：行号在不同单据里各是各的编号，
    # 按行号聚合会把退货行与订单行算成两行，反而造出假差异。
    o1 = aggregates["EXT-O1"]["lines"]["SKU-A"]
    assert o1["ordered"] == Decimal(10)
    assert o1["shipped"] == Decimal(10), "拆单的两批货要相加，且各算一次"
    assert o1["returned"] == Decimal(2)
    assert o1["exchanged"] == Decimal(3), "换货单独计，不能混进退货"

    o2 = aggregates["EXT-O2"]["lines"]["SKU-C"]
    assert o2["shipped"] == Decimal(3), "合单里属于 O2 的行只算到 O2 头上"
    assert o1["shipped"] != Decimal(20), "同一批货被算两遍就会变成 20"


def test_over_shipped_diff_catches_double_counting():
    """把同一批发货算两遍时，over_shipped 差异必须出现（不双算的自动探针）。"""
    session = _session()
    _add(session, [_order("CRM-O1", amount="100")])
    asyncio.run(session.commit())

    stamp = datetime(2026, 9, 5, tzinfo=UTC)
    _add(
        session,
        [
            ExternalRecord(
                system_type=ADAPTER_SYSTEM,
                shop_id="SHOP-A",
                object_type="order",
                dedupe_key="EXT-O1",
                external_id="EXT-O1",
                external_code="CRM-O1",
                amount=Decimal("100"),
                currency="CNY",
                raw_digest="d1",
                occurred_at=stamp,
                first_seen_at=stamp,
                last_seen_at=stamp,
            ),
            ExternalRecord(
                system_type=ADAPTER_SYSTEM,
                shop_id="SHOP-A",
                object_type="order_item",
                dedupe_key="EXT-O1#1",
                parent_key="EXT-O1",
                order_key="EXT-O1",
                line_key="1",
                external_code="SKU-A",
                quantity=Decimal("10"),
                raw_digest="d2",
                occurred_at=stamp,
                first_seen_at=stamp,
                last_seen_at=stamp,
            ),
            # 同一批 10 件被两条不同的明细行记了两次（模拟"没有行级归属"时的双算）
            ExternalRecord(
                system_type=ADAPTER_SYSTEM,
                shop_id="SHOP-A",
                object_type="shipment_item",
                dedupe_key="SHIP-1#1",
                parent_key="SHIP-1",
                order_key="EXT-O1",
                line_key="1",
                external_code="SKU-A",
                quantity=Decimal("10"),
                raw_digest="d3",
                occurred_at=stamp,
                first_seen_at=stamp,
                last_seen_at=stamp,
            ),
            ExternalRecord(
                system_type=ADAPTER_SYSTEM,
                shop_id="SHOP-A",
                object_type="shipment_item",
                dedupe_key="SHIP-2#1",
                parent_key="SHIP-2",
                order_key="EXT-O1",
                line_key="1",
                external_code="SKU-A",
                quantity=Decimal("10"),
                raw_digest="d4",
                occurred_at=stamp,
                first_seen_at=stamp,
                last_seen_at=stamp,
            ),
        ],
    )
    asyncio.run(session.commit())

    result = asyncio.run(
        rec.run_reconciliation(
            session, period_start=PERIOD_START.date(), period_end=PERIOD_END.date()
        )
    )
    assert result["counters"]["over_shipped"] == 1
    diffs, _ = asyncio.run(rec.list_diffs(session, diff_type="over_shipped"))
    assert len(diffs) == 1
    assert "20" in str(diffs[0]["incoming_value"])


# ---------------------------------------------------------------- 待匹配与重放


def test_unknown_sku_goes_pending_and_replays_after_local_created():
    """未知 SKU：先待匹配；本地补建后重放即可匹配上，不需要重新拉外部数据。"""
    session = _session()
    _add(session, [_order("CRM-O1")])
    asyncio.run(session.commit())

    # 订单页里同时带订单头与订单行：订单头先匹配上本地订单，行才有父单可挂。
    _collect(
        session,
        FakeCollectAdapter(
            [[_order_record("EXT-O1", "CRM-O1"), _order_item("EXT-O1", "1", "SKU-NEW", "5")]]
        ),
    )

    pending = asyncio.run(
        col.list_mappings(session, owner_ids=None, match_status=MATCH_PENDING, page_size=50)
    )[0]
    assert len(pending) == 1
    assert pending[0]["object_type"] == "order_item"
    assert "SKU-NEW" in (pending[0]["match_note"] or "")

    product = Product(name="测试产品")
    _add(session, [product])
    asyncio.run(session.commit())
    _add(session, [Sku(product_id=product.id, sku_code="SKU-NEW", name="新件")])
    asyncio.run(session.commit())

    replayed = asyncio.run(col.replay_pending_mappings(session))
    assert replayed["matched"] >= 1
    matched = asyncio.run(
        col.list_mappings(session, owner_ids=None, match_status=MATCH_MATCHED, page_size=50)
    )[0]
    assert matched and matched[0]["internal_id"] is not None


def test_manual_match_records_basis_and_operator():
    """人工指定本地对象：留 match_basis=manual 与操作人，可事后解释。"""
    session = _session()
    _add(session, [_order("CRM-O1")])
    asyncio.run(session.commit())
    _collect(session, FakeCollectAdapter([[_order_record("EXT-O1", "CRM-UNKNOWN")]]))
    mapping = asyncio.run(col.list_mappings(session, owner_ids=None, page_size=50))[0][0]
    row = asyncio.run(col.get_mapping(session, mapping["id"]))

    target = asyncio.run(session.execute(select(SalesOrder))).scalars().first()
    result = asyncio.run(col.assign_mapping(session, row, internal_id=target.id, operator_id=7))
    assert result["mapping"]["match_basis"] == "manual"
    assert result["mapping"]["matched_by"] == 7


def test_record_visibility_follows_the_linked_order_owner():
    """看不到归属就看不到：未匹配/他人订单的原始事实不给非全量范围的人。"""
    session = _session()
    _add(session, [_order("CRM-MINE", owner_id=1), _order("CRM-OTHER", owner_id=2)])
    asyncio.run(session.commit())
    _collect(session, FakeCollectAdapter([[_order_record("EXT-MINE", "CRM-MINE")]]))
    _collect(
        session,
        FakeCollectAdapter([[_order_record("EXT-OTHER", "CRM-OTHER")]]),
        shop_id="SHOP-B",
    )
    _collect(
        session,
        FakeCollectAdapter([[_order_record("EXT-NONE", "CRM-NONE")]]),
        shop_id="SHOP-C",
    )

    mine = asyncio.run(col.list_records(session, owner_ids=[1], page_size=50))[0]
    assert [row["dedupe_key"] for row in mine] == ["EXT-MINE"]
    everything = asyncio.run(col.list_records(session, owner_ids=None, page_size=50))[0]
    assert len(everything) == 3


# ---------------------------------------------------------------- 对账与证据


def test_cross_period_diff_points_back_to_the_raw_payload():
    """跨期对账差异能点回原始证据（原始报文全文）。"""
    session = _session()
    _collect(session, FakeCollectAdapter([[_order_record("EXT-1", "CRM-NOT-EXIST", "88")]]))
    result = asyncio.run(
        rec.run_reconciliation(
            session, period_start=PERIOD_START.date(), period_end=PERIOD_END.date()
        )
    )
    assert result["counters"]["missing_local"] == 1

    diffs, total = asyncio.run(rec.list_diffs(session, diff_type="missing_local"))
    assert total == 1
    diff = asyncio.run(rec.get_diff(session, diffs[0]["id"]))
    detail = asyncio.run(rec.diff_evidence(session, diff))
    assert detail["records"], "差异详情必须能取回原始记录"
    assert detail["records"][0]["payload"]["o_id"] == "EXT-1"
    assert detail["missing_records"] == []
    assert detail["diff"]["evidence_keys"][0]["dedupe_key"] == "EXT-1"


def test_reconciliation_is_idempotent_for_the_same_period():
    """同一期间重复对账：复用批次，差异不重复出。"""
    session = _session()
    _collect(session, FakeCollectAdapter([[_order_record("EXT-1", "CRM-NOT-EXIST")]]))
    first = asyncio.run(
        rec.run_reconciliation(
            session, period_start=PERIOD_START.date(), period_end=PERIOD_END.date()
        )
    )
    second = asyncio.run(
        rec.run_reconciliation(
            session, period_start=PERIOD_START.date(), period_end=PERIOD_END.date()
        )
    )
    assert first["run"]["id"] == second["run"]["id"]
    assert second["reused_run"] is True
    _, total = asyncio.run(rec.list_diffs(session))
    assert total == first["counters"]["diffs_created"]


def test_aftersale_fact_never_writes_local_payment_and_needs_manual_review():
    """外部售后事实：只进待核定队列，不写本地回款/应收，且只允许人工核定 + 依据。"""
    session = _session()
    _add(session, [Customer(id=1, name="测试客户", owner_id=1, pool_status="private")])
    asyncio.run(session.commit())
    _add(session, [_order("CRM-O1")])
    asyncio.run(session.commit())
    adapter = FakeCollectAdapter(
        [
            [
                _order_record("EXT-O1", "CRM-O1"),
                _order_item("EXT-O1", "1", "SKU-A", "10"),
                _aftersale("AS-1", "EXT-O1", "return"),
                _aftersale_item("AS-1", "1", "EXT-O1", "return", "2"),
            ]
        ]
    )
    _collect(session, adapter)

    before = (
        asyncio.run(session.execute(select(PaymentRecord))).scalars().all(),
        asyncio.run(session.execute(select(ReceivablePlan))).scalars().all(),
    )
    result = asyncio.run(
        rec.run_reconciliation(
            session, period_start=PERIOD_START.date(), period_end=PERIOD_END.date()
        )
    )
    assert result["counters"]["aftersale_pending_review"] == 1
    diffs, _ = asyncio.run(rec.list_diffs(session, diff_type="aftersale_needs_review"))
    assert len(diffs) == 1
    assert diffs[0]["allowed_resolutions"] == ["manual"]
    assert diffs[0]["requires_note"] is True

    after = (
        asyncio.run(session.execute(select(PaymentRecord))).scalars().all(),
        asyncio.run(session.execute(select(ReceivablePlan))).scalars().all(),
    )
    assert before == after, "对账不得写本地回款/应收"

    # 只允许"人工处理 + 写清依据"：没依据直接被拒，也不允许选"以外部为准"
    diff = asyncio.run(rec.get_diff(session, diffs[0]["id"]))
    with pytest.raises(AppError) as caught:
        asyncio.run(
            rec.confirm_diff(session, diff, resolution="manual", note="  ", operator_id=1)
        )
    assert "依据" in caught.value.message
    with pytest.raises(AppError):
        asyncio.run(
            rec.confirm_diff(
                session, diff, resolution="take_external", note="x", operator_id=1
            )
        )

    outcome = asyncio.run(
        rec.confirm_diff(
            session,
            diff,
            resolution="manual",
            note="已与财务核对该退货来源与发生时点",
            operator_id=9,
        )
    )
    assert outcome["applied_to_local"] is False
    assert "不会自动改本地回款" in outcome["message"]


def test_unverified_adapter_never_calls_out_and_records_not_verified(monkeypatch):
    """未验收的适配器：一次请求都不发，水位如实记成 not_verified。"""
    monkeypatch.setattr(adapter_settings, "erp_base_url", "https://example.invalid")
    monkeypatch.setattr(adapter_settings, "erp_app_key", "key")
    monkeypatch.setattr(adapter_settings, "erp_app_secret", "secret")

    class _NeverUsedHttp:
        def __init__(self):
            self.posts: list = []

        async def post(self, path, json=None):  # pragma: no cover - 不应被调用
            self.posts.append((path, json))
            raise AssertionError("未验收的适配器不允许发请求")

        async def aclose(self):
            return None

    http = _NeverUsedHttp()
    session = _session()
    with pytest.raises(ErpNotVerified) as caught:
        _collect(session, JushuitanAdapter(http=http))
    assert "未通过真实环境验收" in str(caught.value)
    assert http.posts == []

    cursor = asyncio.run(col.list_cursors(session))[0]
    assert cursor["status"] == CURSOR_NOT_VERIFIED
    assert cursor["last_error"] and "验收" in cursor["last_error"]
    logs = asyncio.run(
        session.execute(
            select(IntegrationLog).where(
                IntegrationLog.business_type == col.BUSINESS_TYPE_COLLECT
            )
        )
    ).scalars().all()
    assert [row.status for row in logs] == [col.LOG_STATUS_NOT_SENT]


def test_source_registry_defaults_to_unverified_and_requires_evidence():
    """来源台账默认未核实；要标成已核实必须给依据。"""
    session = _session()
    state = asyncio.run(
        col.get_source_state(
            session, system_type=ADAPTER_SYSTEM, shop_id="SHOP-A", source_kind="collect_order"
        )
    )
    assert state["verified"] is False and state["status"] == "unverified"

    with pytest.raises(AppError) as caught:
        asyncio.run(
            col.register_source(
                session,
                system_type=ADAPTER_SYSTEM,
                shop_id="SHOP-A",
                source_kind="collect_order",
                verified=True,
                evidence="  ",
                authorization_note=None,
                operator_id=1,
            )
        )
    assert "依据" in caught.value.message

    verified = asyncio.run(
        col.register_source(
            session,
            system_type=ADAPTER_SYSTEM,
            shop_id="SHOP-A",
            source_kind="collect_order",
            verified=True,
            evidence="聚水潭开放平台只读验收报告 R-2026-10",
            authorization_note="店铺授权已到",
            operator_id=1,
        )
    )
    assert verified["verified"] is True and verified["evidence"]


def test_collect_log_is_written_for_success_and_failure():
    """采集留痕：成功与失败都写一条可查的集成日志（含批次与计数）。"""
    session = _session()
    _collect(session, FakeCollectAdapter([[_order_record("EXT-1", "CRM-1")]]))
    broken = FakeCollectAdapter([[]])
    broken.error = RuntimeError("对方接口 500")
    broken.fail_first_call = True
    with pytest.raises(RuntimeError):
        _collect(session, broken, shop_id="SHOP-B")

    logs = asyncio.run(
        session.execute(
            select(IntegrationLog)
            .where(IntegrationLog.business_type == col.BUSINESS_TYPE_COLLECT)
            .order_by(IntegrationLog.id.asc())
        )
    ).scalars().all()
    assert [row.status for row in logs] == [col.LOG_STATUS_SUCCESS, col.LOG_STATUS_FAILED]
    assert logs[0].response_data["inserted"] == 1
    assert "对方接口 500" in (logs[1].error_message or "")
