from app.modules.approval.rules_engine import strip_approval_summary


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
