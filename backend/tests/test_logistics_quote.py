"""物流计价纯函数单元测试：`quote_rate` 重量/体积取大、最低收费、时效文案。"""

from decimal import Decimal

from app.modules.pricing.logistics import quote_rate
from app.modules.pricing.model import LogisticsRate


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
