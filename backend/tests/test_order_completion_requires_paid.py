"""订单完成必须等财务确认全部回款（2026-11-27 主人拍板）。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.core.errors import AppError
from app.modules.order import service as order_service


def _setup(monkeypatch, *, unreceived: str):
    order = SimpleNamespace(id=1, order_no='SO-1', status='shipped')
    monkeypatch.setattr(order_service, 'order_shipments', AsyncMock(return_value={
        'summary': {'ordered_exact': '10', 'shipped_exact': '10', 'all_shipped': True},
        'items': [], 'batches': [],
    }))
    from app.modules.payment import service as payment_service
    monkeypatch.setattr(payment_service, 'order_finance_summary', AsyncMock(return_value={
        'unreceived_amount_exact': unreceived,
    }))
    session = SimpleNamespace(add=Mock())
    return order, session


@pytest.mark.anyio
async def test_completed_with_unreceived_is_blocked_even_with_reason(monkeypatch):
    order, session = _setup(monkeypatch, unreceived='100.00')
    with pytest.raises(AppError) as caught:
        await order_service.change_status(
            session, order,
            new_status='completed', operator_id=1,
            remark='月结客户，按合同账期收款',
        )
    assert caught.value.http_status == 422
    assert '未收款 100.00' in caught.value.message
    assert '财务确认全部回款' in caught.value.message
    assert order.status == 'shipped'
    session.add.assert_not_called()


@pytest.mark.anyio
async def test_completed_after_all_payments_confirmed(monkeypatch):
    order, session = _setup(monkeypatch, unreceived='0.00')
    await order_service.change_status(
        session, order, new_status='completed', operator_id=1,
    )
    assert order.status == 'completed'
    session.add.assert_called_once()
    history = session.add.call_args.args[0]
    assert history.new_status == 'completed'
