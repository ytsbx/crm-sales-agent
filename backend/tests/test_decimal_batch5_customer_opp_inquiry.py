"""float→Decimal 专项批5 边界回归：客户/商机/询价序列化不得经 float。

覆盖：商机预计金额/明细数量/目标价、询价数量/目标价 Decimal 原值 + exact 同源 +
线路逐位精确；询价转报价响应 exact 字段由 router 出口保证（服务层同源）。
"""
import json
from decimal import Decimal as D

from app.core.jsonio import exact_json_dumps
from app.modules.inquiry.model import CustomInquiry
from app.modules.inquiry import service as inquiry_svc
from app.modules.opportunity.model import Opportunity, OpportunityItem
from app.modules.opportunity import service as opp_svc

BIG = D("99999999999999.99")


def _wire_exact(payload: dict, key: str, expected: D):
    back = json.loads(exact_json_dumps(payload), parse_float=D, parse_int=D)
    assert back[key] == expected, (key, back[key], expected)


def test_opportunity_serialize_keeps_decimal():
    opp = Opportunity(id=1, title="大单", stage_id=1, status="open",
                      customer_id=1, expected_amount=BIG)
    out = opp_svc.serialize_opportunity(opp)
    assert isinstance(out["expected_amount"], D) and out["expected_amount"] == BIG
    _wire_exact(out, "expected_amount", BIG)


def test_opportunity_item_serialize_keeps_decimal():
    item = OpportunityItem(id=1, opportunity_id=1, quantity=D("12.345"),
                           target_price=BIG)
    out = opp_svc.serialize_item(item)
    assert isinstance(out["quantity"], D) and out["quantity"] == D("12.345")
    assert isinstance(out["target_price"], D) and out["target_price"] == BIG
    _wire_exact(out, "target_price", BIG)


def test_inquiry_serialize_keeps_decimal_and_exact():
    inquiry = CustomInquiry(id=1, inquiry_no="IN-1", customer_id=1, status="pending",
                            quantity=D("12.345"), target_price=BIG)
    out = inquiry_svc.serialize(inquiry)
    assert isinstance(out["quantity"], D) and out["quantity"] == D("12.345")
    assert isinstance(out["target_price"], D) and out["target_price"] == BIG
    assert out["quantity_exact"] == "12.345"
    assert out["target_price_exact"] == format(BIG, "f")
    _wire_exact(out, "target_price", BIG)