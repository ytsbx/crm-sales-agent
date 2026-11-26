"""完成交付时如果还有未收款，必须写原因（主人 2026-11-26 拍板 A）。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from app.core.errors import AppError
from app.modules.order import router as order_router
from app.modules.order.schema import OrderStatusChange
from conftest import make_user


def request() -> Request:
    return Request({'type': 'http', 'headers': [], 'client': ('127.0.0.1', 1)})


def _setup(monkeypatch):
    order = SimpleNamespace(id=1, order_no='SO-1', status='shipped')
    monkeypatch.setattr(order_router.svc, 'get_visible_order', AsyncMock(return_value=order))
    monkeypatch.setattr(order_router.svc, 'order_shipments', AsyncMock(return_value={
        'summary': {'shipped_exact': '10', 'all_shipped': True}, 'items': [], 'batches': [],
    }))
    from app.modules.payment import service as payment_service
    monkeypatch.setattr(payment_service, 'order_finance_summary', AsyncMock(return_value={
        'unreceived_amount_exact': '100.00',
    }))
    change = AsyncMock()
    monkeypatch.setattr(order_router.svc, 'change_status', change)
    monkeypatch.setattr(order_router.svc, 'serialize_order', lambda _order: {})
    audit = AsyncMock()
    monkeypatch.setattr(order_router, 'write_audit', audit)
    session = SimpleNamespace(commit=AsyncMock())
    user = make_user(1, permissions=('order:manage',), data_scope='all')
    return change, audit, session, user


@pytest.mark.anyio
async def test_completed_with_unreceived_requires_reason(monkeypatch):
    change, audit, session, user = _setup(monkeypatch)
    with pytest.raises(AppError) as caught:
        await order_router.change_status(
            1, OrderStatusChange(status='completed'), request(), user, session,
        )
    assert caught.value.http_status == 422
    assert '未收款 100.00' in caught.value.message
    change.assert_not_awaited()

    await order_router.change_status(
        1, OrderStatusChange(status='completed', remark='月结客户，按合同账期收款'),
        request(), user, session,
    )
    change.assert_awaited_once()
    assert change.await_args.kwargs['remark'] == '月结客户，按合同账期收款'
    assert audit.call_args.kwargs['after']['unreceived_amount'] == '100.00'
    assert audit.call_args.kwargs['after']['remark'] == '月结客户，按合同账期收款'
