"""编号规则纯函数单元测试：周期键与单号格式化（不连数据库）。"""

from datetime import datetime

from app.modules.settings.model import NumberingRule
from app.modules.settings.numbering import format_number, period_key


def make_rule(**overrides) -> NumberingRule:
    defaults = dict(
        code="quote",
        name="报价单号",
        prefix="Q",
        date_format="%Y%m%d",
        seq_length=4,
        reset_period="daily",
    )
    defaults.update(overrides)
    return NumberingRule(**defaults)


class TestPeriodKey:
    def test_daily(self):
        assert period_key("daily", datetime(2026, 9, 27, 23, 59)) == "20260927"

    def test_monthly(self):
        assert period_key("monthly", datetime(2026, 9, 1)) == "202609"

    def test_yearly(self):
        assert period_key("yearly", datetime(2026, 12, 31)) == "2026"

    def test_none_period_returns_empty(self):
        assert period_key("none", datetime(2026, 9, 27)) == ""


class TestFormatNumber:
    def test_standard_quote_number(self):
        rule = make_rule()
        now = datetime(2026, 9, 27, 10, 30)
        assert format_number(rule, 7, now) == "Q202609270007"

    def test_sequence_padding(self):
        rule = make_rule(seq_length=6)
        now = datetime(2026, 9, 27)
        assert format_number(rule, 42, now) == "Q20260927000042"

    def test_long_sequence_not_truncated(self):
        """位数超出 seq_length 时按原样输出（补零是下限宽度，不是截断）。"""
        rule = make_rule(seq_length=2)
        now = datetime(2026, 9, 27)
        assert format_number(rule, 123, now) == "Q20260927123"

    def test_no_date_part(self):
        rule = make_rule(date_format="", prefix="SO")
        now = datetime(2026, 9, 27)
        assert format_number(rule, 3, now) == "SO0003"

    def test_custom_prefix_and_format(self):
        rule = make_rule(prefix="SP", date_format="%Y%m", seq_length=3)
        now = datetime(2026, 9, 27)
        assert format_number(rule, 11, now) == "SP202609011"
