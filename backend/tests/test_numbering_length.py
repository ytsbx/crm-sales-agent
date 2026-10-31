"""编号组合长度边界；保持现有格式与流水不截断的契约。"""
from datetime import UTC, datetime

import pytest

from app.core.errors import AppError
from app.modules.settings.model import NumberingRule
from app.modules.settings.numbering import (
    DEFAULT_RULES, format_number, validate_rule_length,
)

NOW = datetime(2026, 10, 31, tzinfo=UTC)
CODES = ['quote', 'order', 'contract', 'inquiry', 'sample_doc', 'order_doc', 'quote_doc']


def rule(code='order', **changes):
    return NumberingRule(code=code, enabled=True, **{**DEFAULT_RULES[code], **changes})


@pytest.mark.parametrize('code', CODES)
def test_defaults_remain_unchanged(code):
    value = rule(code)
    validate_rule_length(value, now=NOW)
    assert format_number(value, 1, NOW) == DEFAULT_RULES[code]['prefix'] + '202610310001'


@pytest.mark.parametrize('code', CODES)
def test_full_32_characters_accepted_and_33_rejected(code):
    value = rule(code, prefix='编' * 12, seq_length=12)
    validate_rule_length(value, now=NOW)
    assert len(format_number(value, 1, NOW)) == 32
    value.prefix += '编'
    with pytest.raises(AppError, match='编号过长'):
        validate_rule_length(value, now=NOW)


def test_date_format_expansion_counted_instead_of_format_string_length():
    value = rule(prefix='P' * 5, date_format='%Y' * 6)
    with pytest.raises(AppError, match='编号过长'):
        validate_rule_length(value, now=NOW)


def test_real_next_sequence_can_exceed_minimum_padding():
    value = rule(prefix='P' * 16, date_format='D' * 14, seq_length=2)
    validate_rule_length(value, 98, now=NOW)
    with pytest.raises(AppError, match='编号过长'):
        validate_rule_length(value, 99, now=NOW)
    assert format_number(value, 100, NOW).endswith('100')


def test_date_part_uses_requested_date():
    value = rule(prefix='P' * 16, date_format='%j' * 5, seq_length=1)
    # 日序号跨年归零，但长度仍固定；最大32位可用，不能仅因日期变化拒绝。
    for moment in [datetime(2026, 12, 31, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC)]:
        validate_rule_length(value, now=moment)
        assert len(format_number(value, 1, moment)) == 32
