"""float→Decimal 专项批4 边界回归：定价/物流/产品序列化不得经 float。

覆盖：查价出口大金额 Decimal + 线路逐位精确 + exact 回填字段；
物流试算大金额 Decimal + 线路逐位精确；SKU 物理量多位小数 Decimal。
master._json_value 挂起 Q6（比较链/指纹口径待主人决策），本批不碰。
"""
import json
from decimal import Decimal as D
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from app.core.jsonio import exact_json_dumps
from app.modules.pricing import logistics, logistics_router, service as pricing_svc
from app.modules.pricing.logistics_schema import LogisticsCalculateRequest
from app.modules.pricing.model import LogisticsRate, PriceRule
from app.modules.product import service as product_svc
from app.modules.product.model import Sku

BIG = D("99999999999999.99")


def _wire_exact(payload: dict, key: str, expected: D):
    back = json.loads(exact_json_dumps(payload), parse_float=D, parse_int=D)
    assert back[key] == expected, (key, back[key], expected)


@pytest.mark.anyio
async def test_lookup_applicable_price_decimal_and_exact_fields(monkeypatch):
    session = AsyncMock()
    rule = PriceRule(sku_id=1, standard_price=BIG, guide_price=BIG, minimum_price=BIG)
    monkeypatch.setattr(pricing_svc, "find_price_rule", AsyncMock(return_value=rule))
    monkeypatch.setattr(pricing_svc, "find_customer_price", AsyncMock(return_value=None))
    monkeypatch.setattr(product_svc, "require_available_sku", AsyncMock())
    snapshot = {}
    customer = SimpleNamespace(id=1, level=None)
    data = await pricing_svc.lookup_applicable_price(
        session, customer=customer, sku_id=1, quantity=D("100"), snapshot_out=snapshot)
    assert isinstance(data["unit_price"], D) and data["unit_price"] == BIG
    assert data["unit_price_exact"] == format(BIG, "f")
    assert snapshot["unit_price"] == BIG
    _wire_exact(data, "unit_price", BIG)


@pytest.mark.anyio
async def test_logistics_trial_big_amount_wire_exact(monkeypatch):
    sku = Sku(id=1, sku_code="SKU-1", weight=D("99999999999"), unit="件")
    session = SimpleNamespace(get=AsyncMock(return_value=sku), add=lambda obj: None,
                              flush=AsyncMock(), commit=AsyncMock())
    user = SimpleNamespace(id=1)
    request = Request({"type": "http", "headers": []})
    rate = LogisticsRate(
        provider="测试物流", shipping_method="陆运", unit_price_per_kg=D("999.9912"),
        unit_price_per_volume=D("300"), min_charge=D("50"), eta_days=3, status="active",
    )
    monkeypatch.setattr(logistics, "volumetric_ratio", AsyncMock(return_value=D(0)))
    monkeypatch.setattr(logistics, "match_rates", AsyncMock(return_value=([rate], [], 1)))
    selected = (await logistics_router.calculate(
        LogisticsCalculateRequest(sku_id=1), request, user, session))["data"]["selected"]
    expected = D("99999119999000.01")
    assert isinstance(selected["amount"], D) and selected["amount"] == expected
    assert selected["amount_exact"] == format(expected, "f")
    assert isinstance(selected["by_weight_amount"], D)
    _wire_exact(selected, "amount", expected)
    _wire_exact(selected, "by_weight_amount", expected)


def test_sku_physical_quantities_keep_decimal():
    sku = Sku(id=1, sku_code="SKU-1", weight=D("12.3456"), length=D("0.0001"),
              width=D("1.5"), height=D("2.25"), carton_volume=D("0.0123"))
    out = product_svc.serialize_sku(sku)
    assert isinstance(out["weight"], D) and out["weight"] == D("12.3456")
    assert isinstance(out["length"], D) and out["length"] == D("0.0001")
    _wire_exact(out, "weight", D("12.3456"))
    _wire_exact(out, "length", D("0.0001"))