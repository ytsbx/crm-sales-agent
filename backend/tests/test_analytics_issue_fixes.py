import pytest
from pydantic import ValidationError

from app.modules.analytics.router import TimingReport
from app.modules.approval.rules_engine import strip_approval_summary


@pytest.mark.parametrize("field,valid,invalid", [
    ("business_type", "甲" * 32, "甲" * 33),
    ("business_id", 2**63 - 1, 2**63),
    ("business_id", -(2**63), -(2**63) - 1),
    ("typed_fields", 2**31 - 1, 2**31),
    ("rework_count", 2**31 - 1, 2**31),
])
def test_timing_input_fits_storage(field, valid, invalid):
    payload = {"operation": "sample_create", "duration_ms": 300}
    assert getattr(TimingReport(**payload, **{field: valid}), field) == valid
    with pytest.raises(ValidationError) as error:
        TimingReport(**payload, **{field: invalid})
    assert error.value.errors()[0]["loc"] == (field,)


def test_approval_summary_hides_margin_inside_reason_text():
    result = strip_approval_summary({
        'reason': '利润率 80.00% 低于授权 90%',
        'offending': [{'sku_code': 'A', 'profit_rate': 0.8, 'reason': '最低保护价 ¥20，利润率 80.00%'}],
    }, can_see_cost=False)
    assert result['reason'] == '报价超出价格权限，需要审批'
    assert result['offending'][0]['profit_rate'] is None
    assert result['offending'][0]['reason'] == '报价超出价格权限，需要审批'


def test_approval_summary_keeps_margin_for_price_manager():
    source = {'reason': '利润率 80.00%', 'offending': [{'profit_rate': 0.8}]}
    assert strip_approval_summary(source, can_see_cost=True) is source
