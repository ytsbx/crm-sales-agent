"""重复ERP回调只恢复特定事件键冲突，包含查询自动flush和asyncpg诊断。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import IntegrityError

from app.modules.erp import service
from app.modules.integration.model import IntegrationLog
from app.modules.order.model import SalesOrder
from tests.test_erp_callback_and_push import FakeAdapter, FakeSession


def conflict(driver, name):
    native = Exception('synthetic PostgreSQL integrity error')
    if driver == 'psycopg':
        native.diag = SimpleNamespace(constraint_name=name)
        original = native
    else:
        native.constraint_name = name
        if driver == 'asyncpg':
            original = Exception('SQLAlchemy adapter')
            original.__cause__ = native
        else:
            original = native
    return IntegrityError('synthetic statement', {}, original)


@pytest.mark.parametrize('driver', ['psycopg', 'asyncpg', 'native'])
@pytest.mark.parametrize('name,expected', [(service.EVENT_KEY_UNIQUE_CONSTRAINT, True), ('unrelated_constraint', False)])
def test_conflict_recognizer_uses_exact_driver_constraint(driver, name, expected):
    assert service._is_event_key_conflict(conflict(driver, name)) is expected


def test_constraint_name_in_message_alone_is_not_replay_evidence():
    error = IntegrityError('synthetic statement', {}, Exception(service.EVENT_KEY_UNIQUE_CONSTRAINT))
    assert not service._is_event_key_conflict(error)


@pytest.mark.anyio
@pytest.mark.parametrize('name', [service.EVENT_KEY_UNIQUE_CONSTRAINT, 'unrelated_constraint'])
async def test_query_autoflush_conflict_boundary(monkeypatch, name):
    session = FakeSession(orders=[SalesOrder(id=7, order_no='ISO-ERP-7', status='in_production')])
    monkeypatch.setattr(service, 'get_adapter', lambda: FakeAdapter())
    prior = IntegrationLog(id=99, status='success', response_data={'matched': True})
    lookup = AsyncMock(side_effect=[None, prior])
    monkeypatch.setattr(service, '_find_processed_event', lookup)
    error = conflict('asyncpg', name)
    monkeypatch.setattr(service, '_mark_superseded', AsyncMock(side_effect=error))
    if name == service.EVENT_KEY_UNIQUE_CONSTRAINT:
        result = await service.apply_status_webhook(session, order_no='ISO-ERP-7', erp_order_id=None, raw_status='Confirmed')
        assert result['replayed'] is True and result['changed'] is False
        assert session.rolled_back == 1 and session.commits == 0 and lookup.await_count == 2
    else:
        with pytest.raises(IntegrityError) as caught:
            await service.apply_status_webhook(session, order_no='ISO-ERP-7', erp_order_id=None, raw_status='Confirmed')
        assert caught.value is error
        assert session.commits == 0 and lookup.await_count == 1
