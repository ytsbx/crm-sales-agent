"""审批规则引擎纯函数单元测试（不连数据库）。

覆盖 `app/modules/approval/rules_engine.py` 里注释明确写"不用碰数据库就能调"的
`evaluate_conditions`，以及账期识别、命中后果文案、留痕序列化。
"""

from app.modules.approval.rules_engine import (
    RuleDecision,
    _cond_detail_pretty,
    effect_summary,
    evaluate_conditions,
    payment_terms_days,
)


class TestEvaluateConditions:
    def test_empty_conditions_all_hit(self):
        """空条件 = 无条件规则，直接命中。"""
        hit, detail = evaluate_conditions([], {"total_amount": 100})
        assert hit is True
        assert detail == []

    def test_none_conditions_all_hit(self):
        hit, _ = evaluate_conditions(None, {})
        assert hit is True

    def test_gte_hit_and_miss(self):
        cond = [{"field": "total_amount", "op": "gte", "value": 50000}]
        hit, detail = evaluate_conditions(cond, {"total_amount": 50000})
        assert hit is True and detail[0]["hit"] is True

        hit, _ = evaluate_conditions(cond, {"total_amount": 49999.99})
        assert hit is False

    def test_lte(self):
        cond = [{"field": "gross_margin", "op": "lte", "value": 25}]
        hit, _ = evaluate_conditions(cond, {"gross_margin": 24.9})
        assert hit is True
        hit, _ = evaluate_conditions(cond, {"gross_margin": 25.1})
        assert hit is False

    def test_eq(self):
        cond = [{"field": "customer_level", "op": "eq", "value": "A"}]
        hit, _ = evaluate_conditions(cond, {"customer_level": "A"})
        assert hit is True
        hit, _ = evaluate_conditions(cond, {"customer_level": "B"})
        assert hit is False

    def test_in_accepts_list_and_scalar(self):
        cond = [{"field": "customer_level", "op": "in", "value": ["A", "B"]}]
        hit, _ = evaluate_conditions(cond, {"customer_level": "B"})
        assert hit is True
        # value 不是列表时按单值处理
        hit, _ = evaluate_conditions(
            [{"field": "customer_level", "op": "in", "value": "A"}],
            {"customer_level": "A"},
        )
        assert hit is True

    def test_unknown_field_misses_with_note(self):
        cond = [{"field": "not_a_field", "op": "gte", "value": 1}]
        hit, detail = evaluate_conditions(cond, {"total_amount": 100})
        assert hit is False
        assert detail[0]["hit"] is False
        assert "条件字段不存在" in detail[0]["note"]

    def test_missing_context_value_misses(self):
        """字段合法但该单没有此数据 → 未命中，给出专门文案。"""
        cond = [{"field": "gross_margin", "op": "gte", "value": 10}]
        hit, detail = evaluate_conditions(cond, {"total_amount": 100})
        assert hit is False
        assert "此字段为空" in detail[0]["note"]

    def test_type_mismatch_misses_not_raises(self):
        """类型对不上（字符串比数字）按未命中处理，不能抛异常。"""
        cond = [{"field": "total_amount", "op": "gte", "value": 100}]
        hit, detail = evaluate_conditions(cond, {"total_amount": "很多钱"})
        assert hit is False
        assert detail[0]["hit"] is False

    def test_multiple_conditions_are_and(self):
        cond = [
            {"field": "total_amount", "op": "gte", "value": 50000},
            {"field": "customer_level", "op": "eq", "value": "C"},
        ]
        hit, _ = evaluate_conditions(
            cond, {"total_amount": 60000, "customer_level": "C"}
        )
        assert hit is True
        hit, detail = evaluate_conditions(
            cond, {"total_amount": 60000, "customer_level": "A"}
        )
        assert hit is False
        assert [d["hit"] for d in detail] == [True, False]


class TestPaymentTermsDays:
    def test_chinese_phrases(self):
        assert payment_terms_days("账期60天") == 60
        assert payment_terms_days("月结30") == 30

    def test_with_spaces(self):
        assert payment_terms_days("账期 45 天") == 45

    def test_fallback_zero(self):
        assert payment_terms_days(None) == 0
        assert payment_terms_days("") == 0
        assert payment_terms_days("现款现货") == 0


class TestEffectSummary:
    def test_auto_pass(self):
        assert effect_summary("auto_pass", {}) == "免审：提交即通过，无需人工审批"

    def test_express(self):
        assert "主管" in effect_summary("express", {})

    def test_exception_route_defaults(self):
        text = effect_summary("exception_route", {})
        assert "财务会签" in text and "finance" in text

    def test_exception_route_custom_action(self):
        text = effect_summary(
            "exception_route",
            {"add_node_label": "质量会签", "add_node_role_codes": ["qa", "finance"]},
        )
        assert "质量会签" in text and "qa、finance" in text

    def test_unknown_kind_echoes(self):
        assert effect_summary("weird", {}) == "weird"


class TestTraceAndPretty:
    def test_trace_strips_private_context_keys(self):
        decision = RuleDecision(
            rule_id=1,
            rule_name="小额免审",
            kind="auto_pass",
            version_no=2,
            action={},
            context={"total_amount": 100, "_quote_no": "Q1", "_customer_name": "某客户"},
        )
        trace = decision.trace()
        assert trace["rule_id"] == 1
        assert trace["rule_version_no"] == 2
        assert "total_amount" in trace["context"]
        assert not any(k.startswith("_") for k in trace["context"])

    def test_pretty_labels_with_unit(self):
        pretty = _cond_detail_pretty(
            [{"field": "total_amount", "op": "gte", "value": 100000.0, "actual": 120000.0}]
        )
        assert pretty[0]["value_label"] == "≥ 100,000.00元"
        assert pretty[0]["actual_label"] == "120,000.00元"

    def test_pretty_missing_actual(self):
        pretty = _cond_detail_pretty(
            [{"field": "gross_margin", "op": "lte", "value": 25, "actual": None}]
        )
        assert pretty[0]["actual_label"] == "无数据"

    def test_pretty_bool_actual(self):
        pretty = _cond_detail_pretty(
            [{"field": "customer_has_overdue", "op": "eq", "value": True, "actual": False}]
        )
        assert pretty[0]["actual_label"] == "否"
