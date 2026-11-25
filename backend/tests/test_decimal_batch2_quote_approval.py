"""float→Decimal 专项 · 批2 回归：报价链与审批阈值一位不差。

钉死四件事：
1. 报价明细序列化保留 Decimal 原值，JSON 出口渲染精确数字字面量（B01-02）；
   纯比率（利润率）仍 float，前端 *100/toFixed 契约不变（Q3）。
2. 审批规则阈值比较：金额上下文 Decimal 与配置阈值（JSON float）归一后比较，
   16 位边界不再因 float 丢分误判免审/极速通道（B09-02）；也不因
   Decimal×float TypeError 被静默判未命中。
3. 规则 trace 落 JSONB 前 Decimal→原值字符串，可精确复算且 json.dumps 不炸。
4. 毛利率等比率上下文口径不变（float + 既有舍入），不在本批顺手改口径。
"""

import json
from decimal import Decimal

from app.core import jsonio
from app.modules.approval import rules_engine
from app.modules.quote import service as quotes
from app.modules.quote.model import QuoteItem

BIG = Decimal("99999999999999.99")
QTY = Decimal("9999999999999.988")


def _item() -> QuoteItem:
    return QuoteItem(
        id=1,
        quote_version_id=1,
        sku_id=1,
        sku_code_snapshot="SKU-1",
        sku_name_snapshot="测试品",
        quantity=Decimal("100"),
        quoted_price=Decimal("999999999999.9999"),
        cost_snapshot=Decimal("1"),
        profit_snapshot=Decimal("0"),
        profit_rate_snapshot=Decimal("0.25"),
        approval_required=False,
    )


def test_serialize_item_decimal_and_wire_exact():
    data = quotes.serialize_item(_item())
    assert data["quoted_price"] == Decimal("999999999999.9999")
    assert isinstance(data["quoted_price"], Decimal)
    assert data["amount"] == BIG  # 100 × 四位单价，逐行到分口径不变
    # 纯比率保留 float（Q3）：前端 (profit_rate*100).toFixed(2) 依赖数字类型。
    assert data["profit_rate_snapshot"] == 0.25 and isinstance(data["profit_rate_snapshot"], float)
    text = jsonio.exact_json_dumps(data)
    assert '"quoted_price":999999999999.9999' in text
    assert '"amount":99999999999999.99' in text
    # 三位数量边界：出口一位不丢。
    qty_item = _item()
    qty_item.quantity = QTY
    qty_item.quoted_price = Decimal("1")
    qty_data = quotes.serialize_item(qty_item)
    assert qty_data["quantity"] == QTY and isinstance(qty_data["quantity"], Decimal)
    assert '"quantity":9999999999999.988' in jsonio.exact_json_dumps(qty_data)
    # 前端跟进1：成本/底价快照补 *_exact 字符串键，编辑种子不经 JS Number 读回丢分。
    assert data["cost_snapshot_exact"] == "1"
    assert data["quoted_price_exact"] == "999999999999.9999"


def test_serialize_item_cost_exact_keys_masked_with_numeric():
    """无成本权限时精确字符串键与数值键同口径隐掉，不留反推成本的新泄漏口。"""
    data = quotes.serialize_item(_item(), can_see_cost=False)
    for field in ("cost_snapshot", "package_cost_snapshot", "logistics_cost_snapshot",
                  "standard_price_snapshot", "recommended_price_snapshot",
                  "minimum_price_snapshot", "profit_snapshot"):
        assert data[field] is None
        assert data[f"{field}_exact"] is None


def test_rules_amount_threshold_decimal_boundary():
    """Decimal 上下文 vs 配置阈值：归一后精确比较，不再 TypeError 静默未命中。

    旧实现 Decimal >= float 抛 TypeError 被 except 判未命中——0.1 这类**不可
    精确表示**的阈值会让所有 Decimal 上下文永远不命中；归一为 Decimal(str(...))
    后按配置文本的十进制意图比较。
    """
    cond = [{"field": "total_amount", "op": "gte", "value": 0.1}]
    hit, detail = rules_engine.evaluate_conditions(cond, {"total_amount": Decimal("0.1")})
    assert hit is True and detail[0]["hit"] is True
    hit, _ = rules_engine.evaluate_conditions(cond, {"total_amount": Decimal("0.09")})
    assert hit is False
    # 让价金额 lte 边界精确（可精确表示阈值）。
    cond = [{"field": "concession_amount", "op": "lte", "value": 1000.0}]
    hit, _ = rules_engine.evaluate_conditions(cond, {"concession_amount": Decimal("1000.00")})
    assert hit is True
    hit, _ = rules_engine.evaluate_conditions(cond, {"concession_amount": Decimal("1000.01")})
    assert hit is False
    # 大额上下文差一分不命中（阈值取可精确表示的整元）。
    cond = [{"field": "total_amount", "op": "gte", "value": 100000000000000.0}]
    hit, _ = rules_engine.evaluate_conditions(cond, {"total_amount": BIG})
    assert hit is False
    hit, _ = rules_engine.evaluate_conditions(
        cond, {"total_amount": Decimal("100000000000000.00")}
    )
    assert hit is True


def test_rules_trace_jsonb_exact_strings():
    decision = rules_engine.RuleDecision(
        rule_id=1,
        rule_name="边界规则",
        kind="auto_pass",
        version_no=1,
        action={},
        context={"total_amount": BIG, "concession_amount": Decimal("0.01"), "gross_margin": 20.0},
        matched=[{"field": "total_amount", "label": "总额", "op": "gte",
                  "value": 1, "actual": BIG, "hit": True}],
    )
    trace = decision.trace()
    assert trace["context"]["total_amount"] == "99999999999999.99"
    assert trace["context"]["concession_amount"] == "0.01"
    assert trace["matched_conditions"][0]["actual"] == "99999999999999.99"
    json.dumps(trace)  # JSONB 列要求：必须可直接序列化
    assert Decimal(trace["context"]["total_amount"]) == BIG  # 可精确复算


def test_margin_ratio_context_unchanged():
    """比率上下文口径不变：float 毛利率与整数阈值比较照旧。"""
    cond = [{"field": "gross_margin", "op": "gte", "value": 30}]
    hit, _ = rules_engine.evaluate_conditions(cond, {"gross_margin": 30.0})
    assert hit is True
    hit, _ = rules_engine.evaluate_conditions(cond, {"gross_margin": 29.99})
    assert hit is False