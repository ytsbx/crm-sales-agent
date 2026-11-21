"""核价公开响应中的成本派生字段应与底价采用同一权限边界。"""
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from tests.conftest import make_user
from app.modules.pricing import router
from app.modules.pricing.schema import PricingRequest


def calculation():
    # 对应 calculate_price 的真实响应：售价 100，成本 20，利润 80。
    return {'sku_id': 1, 'recommended_price': 100, 'recommended_range': [95, 105],
            'price_rule': {'standard_price': 100, 'guide_price': 100, 'minimum_price': 25},
            'customer_price_rule': None, 'minimum_price': 25, 'minimum_price_cny': 25,
            'cost_in_quote_currency': 20, 'profit': 80, 'profit_rate': .8,
            'recommended_profit': 80, 'recommended_profit_rate': .8,
            'cost': {'goods_cost': 20, 'source': 'cost table'}}


@pytest.mark.anyio
@pytest.mark.parametrize('price_source', ['guide', 'customer_agreed'])
@pytest.mark.parametrize('sensitive_key', ['minimum_price_cny', 'recommended_profit', 'recommended_profit_rate'])
async def test_calculate_endpoint_hides_all_cost_derived_metrics(monkeypatch, sensitive_key, price_source):
    calculated = calculation()
    if price_source == 'customer_agreed':
        calculated['price_rule'] = None
        calculated['customer_price_rule'] = {'agreed_price': 100, 'minimum_price': 25}
    monkeypatch.setattr(router.svc, 'calculate_price', AsyncMock(return_value=calculated))
    result = await router.calculate(PricingRequest(sku_id=1, quantity=1),
                                    make_user(permissions={'product:view'}), None)
    body = result['data']
    assert body[sensitive_key] is None, f'{sensitive_key} reveals cost despite missing price:manage'
    assert body['recommended_price'] == 100
    assert body['recommended_range'] == [95, 105]


def test_price_manager_keeps_original_metrics():
    original = calculation()
    assert router._sanitize_pricing_result(deepcopy(original), make_user(permissions={'price:manage'})) == original
