"""float→Decimal 专项批7 边界回归：打样/洞察金额不得经 float。

覆盖：打样申请费用/工时/派生积分、发货运费 Decimal 原值 + 线路逐位精确；
洞察 price_assumption Decimal + exact；评审快照按 Q2 存原值字符串（JSON 可落库）。
"""
import json
from decimal import Decimal as D

from app.core.jsonio import exact_json_dumps
from app.modules.product_insight import router as insight_router
from app.modules.product_insight.model import ProductInsight
from app.modules.sample import service as sample_svc
from app.modules.sample.model import SampleRequest, SampleShipment

BIG = D("99999999999999.99")


def _wire_exact(payload: dict, key: str, expected: D):
    back = json.loads(exact_json_dumps(payload), parse_float=D, parse_int=D)
    assert back[key] == expected, (key, back[key], expected)


def test_sample_request_fees_keep_decimal():
    req = SampleRequest(id=1, customer_id=1, owner_id=1, status="draft",
                        sample_fee=BIG, estimated_hours=D("1.5"))
    out = sample_svc.serialize_request(req)
    assert isinstance(out["sample_fee"], D) and out["sample_fee"] == BIG
    assert isinstance(out["estimated_hours"], D)
    assert isinstance(out["craft_points"], D)
    _wire_exact(out, "sample_fee", BIG)


def test_sample_shipment_fee_keeps_decimal():
    shipment = SampleShipment(id=1, sample_request_id=1, shipping_fee=BIG)
    out = sample_svc.serialize_shipment(shipment)
    assert isinstance(out["shipping_fee"], D) and out["shipping_fee"] == BIG
    _wire_exact(out, "shipping_fee", BIG)


def test_insight_price_assumption_and_snapshot_string():
    row = ProductInsight(id=1, title="洞察", price_assumption=BIG, conclusion="可做")
    out = insight_router._serialize(row)
    assert isinstance(out["price_assumption"], D) and out["price_assumption"] == BIG
    assert out["price_assumption_exact"] == format(BIG, "f")
    _wire_exact(out, "price_assumption", BIG)
    snap = insight_router._content_snapshot(row)
    assert snap["price_assumption"] == format(BIG, "f")
    assert snap["conclusion"] == "可做"
    # 快照必须能直接进 JSON 列（Decimal 会 TypeError）
    json.dumps(snap)