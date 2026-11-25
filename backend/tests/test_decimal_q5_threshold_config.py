"""Q5 决策3 边界回归：规则阈值改精确字符串配置，配置侧不再经 float。

覆盖：16 位阈值字符串写入逐位保留（旧 float 配置会糊最后一位）；数字入参兼容
归一；非法数字串 422；枚举字段不受影响；求值/重叠比较认字符串且与历史 float 混用不炸。
"""
from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from app.modules.approval import rules_engine
from app.modules.approval.rules_router import RuleCondition, RulePayload, _validate_payload

BIG = "99999999999999.99"


def test_threshold_string_keeps_every_digit():
    cond = RuleCondition(field="total_amount", op="gte", value=BIG)
    assert cond.value == BIG
    assert cond.model_dump()["value"] == BIG


def test_numeric_inputs_canonicalize_to_exact_string():
    assert RuleCondition(field="total_amount", op="gte", value=0.1).value == "0.1"
    assert RuleCondition(field="total_amount", op="gte", value=50000).value == "50000"
    assert RuleCondition(field="total_amount", op="gte", value="1e5").value == "100000"


def test_invalid_threshold_string_refused():
    with pytest.raises(ValidationError):
        RuleCondition(field="total_amount", op="gte", value="abc")
    with pytest.raises(ValidationError):
        RuleCondition(field="total_amount", op="gte", value="NaN")


def test_enum_field_string_untouched():
    cond = RuleCondition(field="customer_level", op="eq", value="A")
    assert cond.value == "A"


def test_evaluate_string_threshold_exact_at_boundary():
    cond = [{"field": "total_amount", "op": "gte", "value": BIG}]
    hit, _ = rules_engine.evaluate_conditions(cond, {"total_amount": D(BIG)})
    assert hit is True
    hit, _ = rules_engine.evaluate_conditions(cond, {"total_amount": D(BIG) - D("0.01")})
    assert hit is False


def test_overlap_mixes_string_and_legacy_float_without_typeerror():
    left = [{"field": "total_amount", "op": "gte", "value": "100"}]
    right = [{"field": "total_amount", "op": "lte", "value": 50.0}]
    assert rules_engine.conditions_overlap(left, right) is False
    right2 = [{"field": "total_amount", "op": "lte", "value": "100"}]
    assert rules_engine.conditions_overlap(left, right2) is True


def test_validate_payload_accepts_numeric_string():
    payload = RulePayload(
        name="字符串阈值", kind=next(iter(rules_engine.VALID_KINDS)),
        conditions=[RuleCondition(field="total_amount", op="gte", value="0.1")],
    )
    _validate_payload(payload)


def test_fmt_string_threshold_same_style_as_number():
    assert rules_engine._fmt(BIG) == f"{D(BIG):,.2f}"
    assert rules_engine._fmt("A") == "A"