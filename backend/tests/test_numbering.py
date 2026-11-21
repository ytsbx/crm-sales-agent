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


def test_reset_period_requires_date_to_distinguish_cycles():
    import pytest

    from app.core.errors import AppError
    from app.modules.settings.numbering import validate_rule_period

    for cycle, bad_format, good_format in [
        ("daily", "%Y%m", "%Y%m%d"),
        ("monthly", "%m", "%Y%m"),
        ("yearly", "%%Y", "%Y"),
    ]:
        with pytest.raises(AppError, match="编号日期必须能区分"):
            validate_rule_period(make_rule(reset_period=cycle, date_format=bad_format))
        validate_rule_period(make_rule(reset_period=cycle, date_format=good_format))
    validate_rule_period(make_rule(reset_period="none", date_format=""))


def test_next_number_rolls_over_at_beijing_midnight():
    import asyncio
    from datetime import UTC

    from sqlalchemy.orm import Session
    from conftest import SyncSessionAsAsync, make_engine
    from app.modules.settings.model import NumberSequence
    from app.modules.settings.numbering import next_number

    engine = make_engine()
    for model in (NumberingRule, NumberSequence):
        model.__table__.create(engine)
    with Session(engine) as raw:
        session = SyncSessionAsAsync(raw)
        before = datetime(2026, 11, 20, 15, 59, tzinfo=UTC)
        after = datetime(2026, 11, 20, 16, 0, tzinfo=UTC)
        assert asyncio.run(next_number(session, "order", now=before)) == "SO202611200001"
        assert asyncio.run(next_number(session, "order", now=after)) == "SO202611210001"
        assert asyncio.run(next_number(session, "order", now=after)) == "SO202611210002"
    engine.dispose()
