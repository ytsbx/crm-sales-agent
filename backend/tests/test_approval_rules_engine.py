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
        """**明确说了现款**才是 0 —— 这是事实，不是猜的。"""
        assert payment_terms_days("现款现货") == 0
        assert payment_terms_days("款到发货") == 0
        assert payment_terms_days("现结") == 0

    def test_unparseable_is_none_not_zero(self):
        """读不出账期时返回 `None`（未知），**不能**返回 0（现款）。

        这不是"文案更准"的问题：0 会让"账期 ≤ 30 天免审"这条规则命中，
        于是一张账期不明的单子被免审。`None` 在 `evaluate_conditions` 里
        走的是"该单此字段为空，视为未命中"，也就是照常人工审批。
        """
        assert payment_terms_days(None) is None
        assert payment_terms_days("") is None
        assert payment_terms_days("   ") is None
        assert payment_terms_days("款到发货，具体面议") == 0  # 有"款到发货"仍是 0
        assert payment_terms_days("按合同约定") is None

    def test_percentage_is_not_a_term(self):
        """**第四批审查 P1**：`预付30%，余款月结60天` 从前被读成 30 天。

        30 是**预付比例**，真实账期是 60 天。配上"账期 ≤ 30 天免审"，
        这张单子就绕过了人工审批。
        """
        assert payment_terms_days("预付30%，余款月结60天") == 60
        assert payment_terms_days("月结60天，预付30%") == 60
        # 整句里只有比例、没有账期 → 未知，不能当现款
        assert payment_terms_days("预付30%") is None
        assert payment_terms_days("预付 30% 定金") is None

    def test_cash_word_does_not_override_a_real_term(self):
        """**第四批复审 P1**：局部出现"款到"不能代表整份条件是现款。

        一份付款条件可以**同时有现款部分和账期部分**（预付/首付是现款，
        余款是账期）。我第一版把现款词判断放在最前面、而且拿整串去匹配
        `款到发货`，于是这两句被判成"现款 0 天"，双双错误命中"账期 ≤ 30 天免审"：

            预付30%，余款到货后60天   → 0（应为 60，"余款到货"里的"款到"被误伤）
            30%款到发货，余款月结60天  → 0（应为 60，前半句是款到发货、后半句是月结）

        现在顺序反过来：**先找明确账期**；现款词只在"某个分句整体就是现款"时才算数。
        """
        assert payment_terms_days("预付30%，余款到货后60天") == 60
        assert payment_terms_days("30%款到发货，余款月结60天") == 60
        assert payment_terms_days("款到发货，余款验收后45天") == 45
        # 真的只有现款时仍然是 0（别修过头）
        assert payment_terms_days("款到发货") == 0
        assert payment_terms_days("现款现货") == 0
        # ⚠️ 这个**故意返回未知而不是 0**：分句以数字开头（`30%款到发货`），
        # 我的"现款分句"判据要求分句**整体**是现款词，所以认不出来。
        # 而它确实没有提到任何账期 —— 未知比"当成现款"保守：
        # 未知 → 规则未命中 → 走人工审；判成 0 会让"账期≤30免审"命中。
        # 这条我在写测试时先入为主写成了 0，被自己的测试抓住了。
        assert payment_terms_days("30%款到发货，余款发货前付清") is None

    def test_unit_must_be_read_in_full(self):
        """**第四批复审再返修 P1（2026-10-12）**：只认 `天|日` 时，
        `账期2个月` 被读成 **2 天** —— 真实 60 天的账期命中"≤30 天免审"。

        月份/年份按 30 / 365 折算（业务上没有更细口径，宁可折算得**偏保守**）。
        """
        assert payment_terms_days("账期2个月") == 60
        assert payment_terms_days("账期3个月") == 90
        assert payment_terms_days("账期1年") == 365

    def test_local_cash_word_does_not_represent_the_whole_document(self):
        """**第四批复审再返修 P1**：分句里先撞上"款到发货"就返回 0，
        根本没看后面的账期 —— `款到发货，余款60天后付清` 被判成现款。

        整份文本里**任何一个分句读出账期**，就不能当现款。
        """
        assert payment_terms_days("款到发货，余款60天后付清") == 60
        assert payment_terms_days("货到付款，余款验收后30天") == 30
        # 整份都是现款时才返回 0
        assert payment_terms_days("款到发货") == 0
        assert payment_terms_days("货到付款") == 0

    def test_multiple_terms_take_the_longest(self):
        """**第四批复审再返修 P1**：`search` 只取第一处，
        `账期30天，尾款验收后90天` 被判成 30 —— 真实信用期是 90 天，**取短了会错误免审**。
        """
        assert payment_terms_days("账期30天，尾款验收后90天") == 90
        assert payment_terms_days("账期2个月，尾款月结90天") == 90
        # 顺序不影响结果（取的是最长，不是第一处）
        assert payment_terms_days("尾款验收后90天，账期30天") == 90

    def test_reversed_wording_number_before_the_term_word(self):
        """"数字 + 单位 + 后 + 付款动词"是极常见的写法，而账期词在**后面**：
        `余款60天后付清` / `余款90日内付清`。只写"账期词在前"的模式读不出来。"""
        assert payment_terms_days("余款90日内付清") == 90
        assert payment_terms_days("验收合格后60天付清") == 60
        assert payment_terms_days("发货后30天付款") == 30

    def test_contradictory_or_unreadable_terms_go_to_manual_review(self):
        """读不准的一律 `None` → 规则不成立 → **人工审批**。

        ⚠️ 我一开始为这条写了个"自相矛盾分句"的断言（`款到发货60天`），
        反向验证时发现它**撤掉规则也照样绿** —— 因为那句返回 None 的真实原因是
        `_MONEY_OR_PERCENT` 把"款"字当成了干扰词、根本没读出天数，
        跟我以为的那条规则无关。**测不到的保护等于没有保护**，所以改成直接测
        这组真实机制：读不出就一定不能是 0。

        为什么"不能是 0"是关键：`0` 会被当成现款，命中"账期 ≤ 30 天免审"；
        `None` 只会让规则不成立。**未知永远比猜一个数字安全。**
        """
        # 含现款词、又含数字，但整句读不出明确账期 → 未知（不是 0）
        assert payment_terms_days("款到发货60天") is None
        assert payment_terms_days("款到发货70天") is None
        # 只有比例/金额、没有账期 → 未知
        assert payment_terms_days("预付30%，余款发货前付清") is None
        # 什么都没写 → 未知
        assert payment_terms_days("按合同约定") is None
        # 反例：确实读得出账期的，不许被这条"保守"掉
        assert payment_terms_days("款到发货，账期60天") == 60

    def test_amount_is_not_a_term(self):
        """金额也不能被当成账期：`预付款 5000 元，账期45天` 从前读成 500 天。"""
        assert payment_terms_days("预付款 5000 元，账期45天") == 45
        assert payment_terms_days("预付款 5000 元") is None
        assert payment_terms_days("定金 2000 元，余款验收后30天") == 30

    def test_unknown_never_passes_a_lte_rule(self):
        """`None` 必须让「账期 ≤ N 天」**不成立**（这才是防绕过的落点）。"""
        hit, detail = evaluate_conditions(
            [{"field": "payment_terms_days", "op": "lte", "value": 30}],
            {"payment_terms_days": payment_terms_days("预付30%")},
        )
        assert hit is False
        assert detail[0]["actual"] is None
        assert "未命中" in detail[0]["note"]

    def test_known_zero_still_passes_a_lte_rule(self):
        """反过来：明确现款的单子**要**能走免审（别把功能修没了）。"""
        hit, _detail = evaluate_conditions(
            [{"field": "payment_terms_days", "op": "lte", "value": 30}],
            {"payment_terms_days": payment_terms_days("款到发货")},
        )
        assert hit is True


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
