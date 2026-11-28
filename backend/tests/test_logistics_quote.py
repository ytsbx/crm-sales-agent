"""物流计价纯函数单元测试：`quote_rate` 重量/体积取大、最低收费、时效文案。"""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from app.core.errors import AppError
from app.modules.agent.tools import calculate_logistics
from app.modules.pricing import logistics, logistics_router
from app.modules.pricing.logistics import quote_rate
from app.modules.pricing.logistics_schema import LogisticsCalculateRequest
from app.modules.pricing.model import LogisticsQuote, LogisticsRate
from app.modules.product.model import Sku


def make_rate(**overrides) -> LogisticsRate:
    defaults = dict(
        provider="测试物流",
        shipping_method="陆运",
        unit_price_per_kg=Decimal("2.5"),
        unit_price_per_volume=Decimal("300"),
        min_charge=Decimal("50"),
        eta_days=3,
        eta_days_max=None,
        status="active",
    )
    defaults.update(overrides)
    return LogisticsRate(**defaults)


@pytest.mark.anyio
async def test_logistics_confirmation_and_history_keep_exact_cents(monkeypatch):
    sku = Sku(id=1, sku_code="SKU-1", weight=Decimal("99999999999"), unit="件")
    rows = []
    session = SimpleNamespace(get=AsyncMock(return_value=sku), add=rows.append,
                              flush=AsyncMock(), commit=AsyncMock())
    user = SimpleNamespace(id=1)
    request = Request({"type": "http", "headers": []})
    rate = make_rate(id=1, unit_price_per_kg=Decimal("999.9912"))
    monkeypatch.setattr(logistics, "volumetric_ratio", AsyncMock(return_value=Decimal(0)))
    monkeypatch.setattr(logistics, "match_rates", AsyncMock(return_value=([rate], [], 1)))

    payload = LogisticsCalculateRequest(sku_id=1)
    selected = (await logistics_router.calculate(payload, request, user, session))["data"]["selected"]
    assert selected["amount_exact"] == "99999119999000.01"

    def confirmation(amount):
        return LogisticsCalculateRequest(sku_id=1, save=True, selected_rate_id=1, expected_amount=amount)

    # 批4 后 selected["amount"] 已是精确 Decimal，原样回传不再漂移；
    # 用旧 float 客户端的丢尾数回传验证"运费已变化"防线仍然生效。
    with pytest.raises(AppError, match="运费已变化"):
        await logistics_router.calculate(confirmation(float(selected["amount"])), request, user, session)
    assert not rows

    await logistics_router.calculate(confirmation(selected["amount_exact"]), request, user, session)
    quote = rows[0]
    assert isinstance(quote, LogisticsQuote)
    assert quote.amount == Decimal("99999119999000.01")
    assert logistics.serialize_quote(quote)["quantity_unit"] == "件"
    assert quote.raw_data["option"]["amount_exact"] == logistics.serialize_quote(quote)["amount_exact"]
    session.commit.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize("unit,quantity,error", [
    ("件", "1.5", "正整数"), ("件", "3.0000", None),
    ("米", "1.234", None), ("公斤", "0.0004", "三位小数"),
])
async def test_logistics_checks_quantity_by_sku_unit(monkeypatch, unit, quantity, error):
    sku = Sku(id=1, sku_code="SKU-1", weight=Decimal("2"), unit=unit)
    session = SimpleNamespace(get=AsyncMock(return_value=sku))
    monkeypatch.setattr(logistics, "volumetric_ratio", AsyncMock(return_value=Decimal(0)))
    monkeypatch.setattr(logistics, "match_rates", AsyncMock(return_value=([make_rate()], [], 1)))
    if error:
        with pytest.raises(AppError, match=error):
            await logistics.prepare(session, sku_id=1, quantity=Decimal(quantity))
    else:
        result = await logistics.prepare(session, sku_id=1, quantity=Decimal(quantity))
        assert result["sku"]["unit"] == unit
        assert Decimal(result["quantity_exact"]) == Decimal(quantity)
        assert result["measures"]["actual_weight"] == Decimal(quantity) * 2


@pytest.mark.anyio
@pytest.mark.parametrize("weight", [None, Decimal("0")])
async def test_missing_weight_only_offers_volume_based_rates(monkeypatch, weight):
    sku = Sku(id=1, sku_code="SKU-1", weight=weight, unit="件")
    session = SimpleNamespace(get=AsyncMock(return_value=sku))
    monkeypatch.setattr(logistics, "volumetric_ratio", AsyncMock(return_value=Decimal(0)))
    weight_only = make_rate(id=1, unit_price_per_volume=None, min_charge=Decimal(0))
    by_volume = make_rate(id=2, unit_price_per_volume=Decimal(300), min_charge=Decimal(0))
    monkeypatch.setattr(logistics, "match_rates", AsyncMock(return_value=([weight_only, by_volume], [], 1)))

    result = await logistics.prepare(session, sku_id=1, quantity=Decimal(1))
    assert result["options"] == []
    assert result["measures"]["weight_missing"]
    assert any("补填单重" in warning for warning in result["warnings"])

    result = await logistics.prepare(session, sku_id=1, quantity=Decimal(1), volume_override=Decimal("0.1"))
    assert [(option["rate_id"], option["amount_exact"]) for option in result["options"]] == [(2, "30.00")]
    assert any("按体积估算" in warning for warning in result["warnings"])

    logistics.volumetric_ratio.return_value = Decimal(100)
    result = await logistics.prepare(session, sku_id=1, quantity=Decimal(1), volume_override=Decimal("0.1"))
    assert [(option["rate_id"], option["amount_exact"]) for option in result["options"]] == [(1, "25.00"), (2, "30.00")]

    result = await logistics.prepare(session, sku_id=1, quantity=Decimal(1), weight_override=Decimal(2))
    assert not result["measures"]["weight_missing"]
    assert {option["rate_id"] for option in result["options"]} == {1, 2}


@pytest.mark.anyio
async def test_agent_logistics_uses_decimal_quantity(monkeypatch):
    sku = Sku(id=1, sku_code="SKU-1", weight=Decimal("2"))
    session = SimpleNamespace(get=AsyncMock(return_value=sku))
    ctx = SimpleNamespace(session=session)
    monkeypatch.setattr(logistics, "volumetric_ratio", AsyncMock(return_value=Decimal(0)))
    monkeypatch.setattr(logistics, "match_rates", AsyncMock(return_value=([make_rate()], [], "exact")))

    result = await calculate_logistics(ctx, sku_id=1, quantity=1.5)

    assert result["quantity"] == 1.5
    assert result["measures"]["actual_weight"] == 3
    assert result["options"][0]["amount"] == 50
    with pytest.raises(AppError, match="数量必须大于 0"):
        await calculate_logistics(ctx, sku_id=1, quantity=0)
    with pytest.raises(AppError, match="数量必须是有效数字"):
        await calculate_logistics(ctx, sku_id=1, quantity=float("inf"))


class TestQuoteRateAmount:
    def test_volume_priced_when_larger(self):
        rate = make_rate()
        result = quote_rate(
            rate, chargeable_weight=Decimal("10"), volume=Decimal("0.2")
        )
        # 按重 25.00 vs 按体 60.00 → 取体积（高于最低收费，不被兜底）
        assert result["amount"] == 60.0
        assert result["by_weight_amount"] == 25.0
        assert result["by_volume_amount"] == 60.0
        assert result["pricing_basis"] == "体积"
        assert result["above_minimum"] is True

    def test_weight_priced_when_larger(self):
        rate = make_rate()
        result = quote_rate(
            rate, chargeable_weight=Decimal("100"), volume=Decimal("0.1")
        )
        assert result["amount"] == 250.0
        assert result["pricing_basis"] == "重量"

    def test_no_volume_price_falls_back_to_weight(self):
        rate = make_rate(unit_price_per_volume=None)
        result = quote_rate(
            rate, chargeable_weight=Decimal("30"), volume=Decimal("9.9")
        )
        assert result["by_volume_amount"] == 0.0
        assert result["amount"] == 75.0
        assert result["pricing_basis"] == "重量"


class TestMinCharge:
    def test_minimum_bumps_amount(self):
        rate = make_rate()
        result = quote_rate(
            rate, chargeable_weight=Decimal("4"), volume=Decimal("0.1")
        )
        # max(10.00, 30.00)=30.00 < 最低收费 50 → 提到 50
        assert result["amount"] == 50.0
        assert result["above_minimum"] is False
        assert result["min_charge"] == 50.0

    def test_override_min_charge(self):
        rate = make_rate()
        result = quote_rate(
            rate,
            chargeable_weight=Decimal("4"),
            volume=Decimal("0.1"),
            min_charge_override=Decimal("80"),
        )
        assert result["amount"] == 80.0
        assert result["min_charge"] == 80.0

    def test_zero_min_charge_never_bumps(self):
        rate = make_rate(min_charge=Decimal("0"))
        result = quote_rate(
            rate, chargeable_weight=Decimal("1"), volume=Decimal("0.01")
        )
        # 重量 2.50 / 体积 3.00，无最低收费兜底
        assert result["amount"] == 3.0
        assert result["above_minimum"] is True


class TestEtaText:
    def test_range_when_max_differs(self):
        rate = make_rate(eta_days=3, eta_days_max=5)
        assert quote_rate(rate, chargeable_weight=Decimal("10"), volume=Decimal("0"))[
            "eta_text"
        ] == "3-5 天"

    def test_single_day_when_max_equals(self):
        rate = make_rate(eta_days=3, eta_days_max=3)
        assert quote_rate(rate, chargeable_weight=Decimal("10"), volume=Decimal("0"))[
            "eta_text"
        ] == "约 3 天"

    def test_no_eta(self):
        rate = make_rate(eta_days=None, eta_days_max=None)
        assert quote_rate(rate, chargeable_weight=Decimal("10"), volume=Decimal("0"))[
            "eta_text"
        ] is None
