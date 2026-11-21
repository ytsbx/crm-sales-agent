"""模拟报价保持四位单价，金额复用正式逐行口径；仅使用内存 SQLite。"""
from datetime import date
from decimal import Decimal as D

import pytest

from conftest import make_user
from test_batch7_cost_lifecycle import case, USER  # noqa: F401
from app.core.errors import AppError
from app.core.money import line_amount
from app.modules.pricing import router
from app.modules.pricing.model import ProductCost, PriceRule
from app.modules.pricing.schema import PricingRequest, PricingSimulation


def add_cost(raw, amount='0.9876'):
    raw.add(ProductCost(sku_id=1, effective_from=date(2020, 1, 1), purchase_cost=D(amount),
                        production_cost=D(0), package_cost=D(0), processing_cost=D(0)))
    raw.flush()


@pytest.mark.anyio
@pytest.mark.parametrize('margin,expected', [('0.2', '1.2345'), ('0.04', '1.0288')])
async def test_margin_candidate_uses_four_decimals_and_half_up(case, margin, expected):
    raw, session, _, _ = case
    add_cost(raw)
    payload = PricingSimulation(base=PricingRequest(sku_id=1, quantity='10000'), margins=[margin])
    result = (await router.simulate_pricing(payload, USER, session))['data']['scenarios'][0]
    assert D(str(result['quoted_price'])) == D(expected)
    assert result['quoted_price_exact'] == expected
    assert result['amount_exact'] == str(line_amount(D(10000), D(expected)))


@pytest.mark.anyio
async def test_default_anchor_keeps_four_decimal_price(case):
    raw, session, _, _ = case
    add_cost(raw)
    raw.add(PriceRule(sku_id=1, effective_from=date(2020, 1, 1),
                      standard_price=D('1.2345'), guide_price=D('1.2345')))
    raw.flush()
    body = (await router.simulate_pricing(PricingSimulation(base=PricingRequest(sku_id=1)), USER, session))['data']
    assert any(D(str(row['quoted_price'])) == D('1.2345') for row in body['scenarios'])


@pytest.mark.anyio
@pytest.mark.parametrize('price,quantity', [('1.2345', '3'), ('999999999999.9999', '100')])
async def test_candidate_amount_matches_formal_line_without_float_roundtrip(case, price, quantity):
    raw, session, _, _ = case
    add_cost(raw)
    payload = PricingSimulation(base=PricingRequest(sku_id=1, quantity=quantity), candidates=[price])
    row = (await router.simulate_pricing(payload, USER, session))['data']['scenarios'][0]
    expected = line_amount(D(quantity), D(price))
    assert row['amount_exact'] == str(expected)
    assert row['amount'] == float(expected)
    assert row['quoted_price_exact'] == price


@pytest.mark.anyio
async def test_simulation_still_hides_cost_and_blocks_margin_inference(case):
    raw, session, _, _ = case
    add_cost(raw)
    user = make_user(permissions={'product:view'})
    base = PricingRequest(sku_id=1, quantity='3')
    body = (await router.simulate_pricing(PricingSimulation(base=base, candidates=['1.2345']), user, session))['data']
    row = body['scenarios'][0]
    assert row['profit'] is None and row['profit_rate'] is None
    assert body['cost_in_quote_currency'] is None
    assert row['amount_exact'] == '3.70'
    blocked = (await router.simulate_pricing(PricingSimulation(base=base, margins=['0.2']), user, session))['data']
    assert blocked['scenarios'] == [] and blocked['blocked_reason']


@pytest.mark.anyio
async def test_simulation_rejects_non_cny(case):
    _, session, _, _ = case
    with pytest.raises(AppError):
        await router.simulate_pricing(PricingSimulation(base=PricingRequest(sku_id=1, currency='USD'), candidates=['1.2345']), USER, session)


@pytest.mark.anyio
@pytest.mark.parametrize("maintained", [False, True])
async def test_incomplete_cost_cannot_supply_inferred_prices(case, maintained):
    from app.modules.pricing.service import calculate_price

    raw, session, _, _ = case
    raw.add(ProductCost(sku_id=1, effective_from=date(2020, 1, 1), purchase_cost=D(10)))
    if maintained:
        raw.add(PriceRule(sku_id=1, effective_from=date(2020, 1, 1),
                          standard_price=D(30), guide_price=D(25)))
    raw.flush()
    result = await calculate_price(session, sku_id=1, quantity=D(1))
    assert result["cost"]["cost_complete"] is False
    assert result["standard_price"] == (30 if maintained else None)
    assert result["recommended_price"] == (25 if maintained else None)


@pytest.mark.anyio
async def test_pricing_stats_uses_actual_quotes_without_a_recommended_price(case):
    from app.modules.analytics.service import pricing_stats
    from app.modules.customer.model import Customer
    from app.modules.quote.model import QuoteItem

    raw, session, quote, version = case
    raw.get(Customer, quote.customer_id).level = "A"
    raw.add_all([
        QuoteItem(quote_version_id=version.id, sku_id=1, quoted_price=D(90),
                  recommended_price_snapshot=D(100)),
        QuoteItem(quote_version_id=version.id, sku_id=1, quoted_price=D(300)),
    ])
    raw.flush()
    result = await pricing_stats(session, USER)
    assert result["average_discount_rate"] == 0.1
    assert result["average_quoted_price_by_level"] == [
        {"level": "A", "item_count": 2, "average_price": 195.0},
    ]


@pytest.mark.anyio
async def test_margin_simulation_requires_complete_cost(case):
    raw, session, _, _ = case
    raw.add(ProductCost(sku_id=1, effective_from=date(2020, 1, 1), purchase_cost=D(10)))
    raw.flush()
    payload = PricingSimulation(base=PricingRequest(sku_id=1), margins=["0.3"])
    result = (await router.simulate_pricing(payload, USER, session))["data"]
    assert result["scenarios"] == []
    assert "完整的生效成本" in result["blocked_reason"]
