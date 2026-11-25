"""float→Decimal 专项批3 边界回归：订单/草稿/回款序列化不得经 float。

覆盖：订单与订单行大金额/多位小数的 Decimal 原值 + 线路逐位精确；
草稿数量/单价 Decimal + exact 同源；财务概览大金额线路逐位精确。
"""
import json
from datetime import timedelta
from decimal import Decimal as D
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from conftest import SyncSessionAsAsync

from app.core.jsonio import exact_json_dumps
from app.core.timebase import today_business
from app.modules.order import drafts
from app.modules.order import service as order_svc
from app.modules.order.model import OrderDraft, OrderDraftItem, SalesOrder, SalesOrderItem
from app.modules.payment import service as pay_svc

BIG = D("99999999999999.99")


def _wire_exact(payload: dict, key: str, expected: D):
    wire = exact_json_dumps(payload)
    back = json.loads(wire, parse_float=D, parse_int=D)
    assert back[key] == expected, (key, back[key], expected)


def test_order_serialize_keeps_decimal_and_wire_exact():
    order = SalesOrder(
        order_no="SO-DEC-1", customer_id=1, owner_id=1, status="confirmed",
        total_amount=BIG, goods_amount=BIG, currency="CNY",
    )
    out = order_svc.serialize_order(order, received_amount=D("0.01"))
    assert isinstance(out["total_amount"], D)
    assert out["total_amount"] == BIG
    assert out["unreceived_amount"] == BIG - D("0.01")
    _wire_exact(out, "total_amount", BIG)
    _wire_exact(out, "unreceived_amount", BIG - D("0.01"))


def test_order_item_serialize_keeps_decimal_and_adds_quantity_exact():
    item = SalesOrderItem(
        order_id=1, quantity=D("12.345"),
        unit_price=D("99999999999999.99"), amount=D("1234567901234.567"),
    )
    out = order_svc.serialize_item(item)
    assert isinstance(out["quantity"], D) and out["quantity"] == D("12.345")
    assert out["quantity_exact"] == "12.345"
    assert isinstance(out["unit_price"], D) and out["unit_price"] == BIG
    _wire_exact(out, "unit_price", BIG)
    _wire_exact(out, "quantity", D("12.345"))


@pytest.mark.anyio
async def test_draft_detail_keeps_decimal(db_session):
    # 替身 SQLite 的 Numeric 经 float 存储（仅测试环境；生产 PG 精确），
    # 故 DB 往返用 double 可精确表示的值；大金额线路精度由序列化层用例保证。
    price = D("1234.5625")
    bind = db_session.get_bind()
    OrderDraft.__table__.create(bind, checkfirst=True)
    OrderDraftItem.__table__.create(bind, checkfirst=True)
    draft = OrderDraft(
        customer_id=1, owner_id=1, created_by=1, source_context={},
        request_key="dec-b3-draft", request_hash="0" * 64,
    )
    db_session.add(draft)
    db_session.flush()
    db_session.add(OrderDraftItem(
        draft_id=draft.id, name="草稿行", source_snapshot={"source_item_id": 1},
        quantity=D("12.345"), unit_price=price,
    ))
    db_session.flush()
    out = await drafts.detail(SyncSessionAsAsync(db_session), draft)
    row = out["items"][0]
    assert isinstance(row["quantity"], D) and row["quantity"] == D("12.345")
    assert isinstance(row["unit_price"], D) and row["unit_price"] == price
    assert row["quantity_exact"] == "12.345" and row["unit_price_exact"] == format(price, "f")
    _wire_exact(row, "unit_price", price)


@pytest.mark.anyio
async def test_finance_summary_big_amount_wire_exact(monkeypatch):
    plans = [SimpleNamespace(id=1, amount=BIG, status="paid",
                             due_date=today_business() + timedelta(days=1))]
    session = AsyncMock()
    session.get.return_value = SimpleNamespace(total_amount=BIG)
    plan_result = Mock()
    plan_result.scalars.return_value.all.return_value = plans
    received_result, pending_result = Mock(), Mock()
    received_result.scalar_one.return_value = BIG
    pending_result.scalar_one.return_value = BIG
    return_result = Mock()
    return_result.scalars.return_value.all.return_value = []
    session.execute.side_effect = [plan_result, received_result, pending_result, return_result]
    monkeypatch.setattr(pay_svc, "confirmed_amounts", AsyncMock(return_value={1: BIG}))
    monkeypatch.setattr(pay_svc, "allocation_totals",
                        AsyncMock(return_value={1: {"allocated_amount": BIG, "unallocated_amount": BIG}}))
    out = await pay_svc.order_finance_summary(session, 1)
    assert isinstance(out["order_amount"], D) and out["order_amount"] == BIG
    _wire_exact(out, "order_amount", BIG)
    _wire_exact(out, "received_amount", BIG)