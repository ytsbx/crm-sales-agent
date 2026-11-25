"""float→Decimal 专项批8 边界回归：Agent 工具/洞察/ERP 入站序列化不得经 float。

覆盖：_money/_measure quantize 口径不变但出口 Decimal；大金额线路逐位精确；
commentary _compact Decimal 分支 + _facts_text 可精确渲染；
ERP collection serialize_record 数量/金额 Decimal。
"""
import json
from decimal import Decimal as D
from types import SimpleNamespace

from app.core.jsonio import exact_json_dumps
from app.modules.agent import commentary, insights
from app.modules.agent.tools import _measure, _money
from app.modules.erp import collection

BIG = D("99999999999999.99")


def _wire_exact(payload: dict, key: str, expected: D):
    back = json.loads(exact_json_dumps(payload), parse_float=D, parse_int=D)
    assert back[key] == expected, (key, back[key], expected)


def test_money_and_measure_keep_decimal_with_rounding_rule():
    # HALF_UP 两位口径不变（B16-02）：2.675 → 2.68
    assert _money(D("2.675")) == D("2.68")
    assert isinstance(_money(BIG), D) and _money(BIG) == BIG
    assert _measure(D("1.23456")) == D("1.2346")
    assert _money(None) is None and _measure(None) is None
    _wire_exact({"v": _money(BIG)}, "v", BIG)


def test_insights_f_keeps_decimal():
    assert isinstance(insights._f(BIG), D) and insights._f(BIG) == BIG
    assert insights._f(D("2.675")) == D("2.68")


def test_compact_and_facts_text_exact():
    packed = commentary._compact({"amount": BIG, "note": "ok"})
    assert isinstance(packed["amount"], D) and packed["amount"] == BIG
    text = commentary._facts_text({"amount": BIG})
    assert "99999999999999.99" in text


def test_erp_collection_record_keeps_decimal():
    row = SimpleNamespace(
        id=1, system_type="jst", shop_id=1, object_type="order", dedupe_key="d",
        parent_key="p", order_key="o", kind="order",
        line_key="l", external_id="e", external_code="c",
        quantity=D("12.345"), amount=BIG, currency="CNY",
        occurred_at=None, external_updated_at=None,
        first_seen_at=None, last_seen_at=None,
        collected_count=1, last_batch_id=1, payload={}, raw_digest="x",
    )
    out = collection.serialize_record(row)
    assert isinstance(out["quantity"], D) and out["quantity"] == D("12.345")
    assert isinstance(out["amount"], D) and out["amount"] == BIG
    _wire_exact(out, "amount", BIG)