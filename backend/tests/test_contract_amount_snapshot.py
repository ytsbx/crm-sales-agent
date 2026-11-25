"""B07-03：合同金额生成时冻结，OA/签署审计读同一份快照。"""
from decimal import Decimal
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock

import pytest

from app.modules.contract.amounts import (
    frozen_amount,
    resolve_generation_amount,
    contract_amount,
)


def test_generation_uses_selected_quote_version_even_v2():
    """选了 V2 就按 V2 冻，不能因为订单总额不同就回退订单。"""
    quote_v2 = Obj(id=22, version_no=2, total_amount=Decimal("100000.00"))
    order = Obj(id=9, total_amount=Decimal("95000.00"))

    amount, source = resolve_generation_amount(order, quote_v2)

    assert amount == Decimal("100000.00")
    assert source == "quote_version:22"


def test_generation_falls_back_to_order_without_quote_version():
    order = Obj(id=9, total_amount=Decimal("95000.00"))

    amount, source = resolve_generation_amount(order, None)

    assert amount == Decimal("95000.00")
    assert source == "order"


def test_generation_can_freeze_explicit_no_amount():
    amount, source = resolve_generation_amount(None, None)

    assert amount is None
    assert source == "none"


def test_frozen_key_distinguishes_none_from_missing():
    assert frozen_amount({}) == (None, False)
    assert frozen_amount({"_amount": None}) == (None, True)
    assert frozen_amount({"_amount": "100000.00"}) == (Decimal("100000.00"), True)


@pytest.mark.anyio
async def test_frozen_amount_wins_without_live_query():
    doc = Obj(
        filled_data={"_amount": "100000.00", "_amount_source": "quote_version:22"},
        order_id=9,
        quote_version_id=22,
    )
    session = Obj(get=AsyncMock(side_effect=AssertionError("有冻结值就不该再查库")))

    assert await contract_amount(session, doc) == Decimal("100000.00")
    session.get.assert_not_called()


@pytest.mark.anyio
async def test_explicit_no_amount_never_falls_back_to_live_order():
    """生成时无金额，后面订单补了金额也不能让审批单突然变成有金额。"""
    doc = Obj(filled_data={"_amount": None}, order_id=9)
    session = Obj(get=AsyncMock())

    assert await contract_amount(session, doc) is None
    session.get.assert_not_called()


@pytest.mark.anyio
async def test_legacy_contract_falls_back_quote_version_before_order():
    doc = Obj(filled_data={}, quote_version_id=22, order_id=9)
    version = Obj(total_amount=Decimal("100000.00"))
    order = Obj(total_amount=Decimal("95000.00"))
    session = Obj(get=AsyncMock(side_effect=[version, order]))

    assert await contract_amount(session, doc) == Decimal("100000.00")


@pytest.mark.anyio
async def test_oa_contract_amount_delegates_to_shared_snapshot():
    from app.modules.oa.service import _contract_amount

    doc = Obj(filled_data={"_amount": "100000.00"}, order_id=9)
    session = Obj(get=AsyncMock(side_effect=AssertionError("不该查库")))

    assert await _contract_amount(session, doc) == Decimal("100000.00")
