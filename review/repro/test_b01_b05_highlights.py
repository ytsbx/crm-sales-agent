"""
B01–B05 P0/P1 复核的可运行证据。

运行：
    cd backend
    PYTHONPATH=. .venv/bin/python -m pytest ../review/repro/test_b01_b05_highlights.py -q

只做纯函数 / schema / 源码契约断言，不连数据库。
"""
from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2] / "backend"


def test_b01_01_example_71234_56_does_not_flip_tier():
    """审查员的例子（10000 × 7.123456，档位 71234.56）其实不会掉档。

    Decimal 精确等于上限，float 两边也相等，`<=` 成立 → 进入该档，正确。
    要触发“实际超了但 float 判没超”，档位得大到 float 分辨率吞掉 1e-8 的差。
    """
    amount = Decimal("10000.00")
    fx = Decimal("7.123456")
    cap = Decimal("71234.56")
    product = amount * fx
    assert product == cap
    assert not (product > cap)
    assert float(product) <= float(cap)


def test_b01_01_float_flip_requires_absurd_tier():
    """结构性风险存在，但只在 10^10 级档位才会翻：默认 5 万档不会。"""
    cap = Decimal("10000000000")
    product = Decimal("30274865503.91") * Decimal("0.330307")
    assert product > cap
    assert float(product) <= float(cap), "这个量级才出现 float 比较吞掉超出部分"


def test_b01_03_default_quantize_is_half_even_not_half_up():
    """`quantize(Decimal('0.01'))` 默认 HALF_EVEN：94.125 → 94.12，和 HALF_UP 的 94.13 不同。"""
    value = Decimal("94.125")
    assert value.quantize(Decimal("0.01")) == Decimal("94.12")
    assert value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) == Decimal("94.13")


def test_b03_06_logistics_quantity_should_limit_three_decimals():
    """试算数量入参应与落库列 Numeric(16,3) 对齐；当前 schema 没有 decimal_places。"""
    from app.modules.pricing.logistics_schema import LogisticsCalculateRequest

    with pytest.raises(Exception):
        LogisticsCalculateRequest(sku_id=1, quantity=Decimal("1.23456"))


def test_b05_02_receivable_create_should_have_request_key():
    from app.modules.payment.schema import ReceivableCreate

    assert "request_key" in ReceivableCreate.model_fields, "ReceivableCreate 没有 request_key"


def test_b05_04_payment_create_should_require_request_key():
    from app.modules.payment.schema import PaymentCreate

    field = PaymentCreate.model_fields["request_key"]
    assert field.is_required(), "request_key 仍是可选，弱网重试可产生重复回款"


def test_b03_02_draft_update_clears_unit_price_when_field_absent():
    """PATCH 只改数量、不带 unit_price 时，schema 给出 None；update 会无条件覆盖。"""
    from app.modules.order.schema import OrderDraftUpdate

    payload = OrderDraftUpdate.model_validate(
        {"revision": 1, "items": [{"source_item_id": 1, "quantity": "2"}]}
    )
    line = payload.items[0]
    assert line.unit_price is None
    assert "unit_price" not in line.model_fields_set

    source = (BACKEND / "app/modules/order/drafts.py").read_text()
    assert "item.quantity, item.unit_price = selected.quantity, selected.unit_price" in source


def test_b03_03_order_from_quote_does_not_call_currency_gate():
    """订单继承报价币种，但 create_order_from_quote 没有 ensure_currency_allowed。"""
    source = (BACKEND / "app/modules/order/service.py").read_text()
    start = source.index("async def create_order_from_quote")
    rest = source[start + 1:]
    end = rest.index("\nasync def ")
    block = rest[:end]
    assert "ensure_currency_allowed" in block, "转单里没有币种闸"


def test_b02_12_quote_valid_until_null_is_accepted_and_cleared_by_quote_patch():
    """`PATCH /quotes/{id}` 带 valid_until:null 会把主单和版本快照都清空；
    `PATCH /quote-versions/{id}` 反而忽略 null。"""
    from app.modules.quote.schema import QuoteUpdate

    payload = QuoteUpdate.model_validate({"valid_until": None})
    assert "valid_until" in payload.model_fields_set
    assert payload.valid_until is None

    source = (BACKEND / "app/modules/quote/router.py").read_text()
    assert re.search(r'if "valid_until" in data:\n\s+#', source) or 'if "valid_until" in data:' in source
    assert "current_version.valid_until_snapshot = data[\"valid_until\"]" in source
