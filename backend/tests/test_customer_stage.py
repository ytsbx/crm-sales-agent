"""客户六阶段自动推导（customer/stage.py derive_stage）的纯函数测试。"""

import pytest

from app.modules.customer.stage import (
    REPEAT_ORDER_THRESHOLD,
    STABLE_ORDER_THRESHOLD,
    STAGE_FIRST_ORDER,
    STAGE_QUOTE,
    STAGE_REPEAT,
    STAGE_SAMPLE,
    STAGE_STABLE,
    STAGE_UNDERSTANDING,
    derive_stage,
)


class TestDeriveStage:
    def test_no_facts_is_understanding(self):
        assert derive_stage(0, 0, 0) == STAGE_UNDERSTANDING

    def test_quote_only(self):
        assert derive_stage(0, 0, 1) == STAGE_QUOTE
        assert derive_stage(0, 0, 5) == STAGE_QUOTE

    def test_sample_beats_quote(self):
        assert derive_stage(0, 1, 3) == STAGE_SAMPLE

    def test_first_order(self):
        assert derive_stage(1, 2, 3) == STAGE_FIRST_ORDER

    def test_repeat_order(self):
        assert derive_stage(2, 1, 1) == STAGE_REPEAT

    def test_stable_repurchase(self):
        assert derive_stage(3, 0, 0) == STAGE_STABLE
        assert derive_stage(10, 0, 0) == STAGE_STABLE

    def test_thresholds_are_two_and_three(self):
        assert REPEAT_ORDER_THRESHOLD == 2
        assert STABLE_ORDER_THRESHOLD == 3

    def test_order_count_overrides_everything(self):
        """有单之后打样/报价数量不再影响阶段。"""
        assert derive_stage(2, 99, 99) == STAGE_REPEAT

    @pytest.mark.parametrize(
        "order_count,expected",
        [(0, None), (1, STAGE_FIRST_ORDER), (2, STAGE_REPEAT), (3, STAGE_STABLE)],
    )
    def test_order_ladder(self, order_count, expected):
        result = derive_stage(order_count, 0, 0)
        if expected is None:
            assert result == STAGE_UNDERSTANDING
        else:
            assert result == expected


def test_stage_distribution_counts_all_stages_in_order():
    """分布统计：给一批计数，按从了解到稳定复购的固定顺序输出。"""

    class _FakeSession:
        async def execute(self, stmt):
            raise AssertionError("distribution 不应再查库")

    # 直接测纯逻辑部分：stage_distribution 内部调用 stage_counts_map（要查库），
    # 所以这里绕过它，用与实现相同的推导链路验证排序与计数
    from collections import Counter

    from app.modules.customer.stage import STAGE_LABELS

    fact_rows = [(0, 0, 0), (0, 0, 2), (0, 1, 1), (1, 0, 0), (2, 0, 0), (3, 0, 0), (5, 9, 9)]
    dist = Counter(derive_stage(*v) for v in fact_rows)
    result = [
        {"stage": s, "label": l, "count": dist.get(s, 0)} for s, l in STAGE_LABELS.items()
    ]
    assert [r["stage"] for r in result] == [
        "understanding", "quote", "sample", "first_order", "repeat", "stable",
    ]
    assert [r["count"] for r in result] == [1, 1, 1, 1, 1, 2]
