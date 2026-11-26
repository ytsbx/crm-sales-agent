"""OA 客户端内嵌 CRM 的一次性票据登录。"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.audit import AuditLog
from app.core.base import Base
from app.core.database import get_db
from app.core.errors import register_exception_handlers
from app.modules.auth.model import LoginSession
from app.modules.auth.router import router
from app.modules.oa import client as oa_client
from app.modules.user.model import User
from tests.conftest import SyncSessionAsAsync, _register_sqlite_bigint_as_integer


def _app(monkeypatch, identity):
    _register_sqlite_bigint_as_integer()
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[User.__table__, LoginSession.__table__, AuditLog.__table__],
    )
    raw = Session(engine)
    raw.add(User(
        id=101, name='刘涛', username='liutao', password_hash='x',
        status='active', oa_user_id='oa-user-101',
    ))
    raw.commit()
    session = SyncSessionAsAsync(raw)

    async def fake_exchange(ticket: str) -> dict:
        return identity

    monkeypatch.setattr(oa_client, 'exchange_sso_ticket', fake_exchange)
    app = FastAPI()
    app.include_router(router)
    register_exception_handlers(app)
    app.dependency_overrides[get_db] = lambda: session
    return TestClient(app)


@pytest.mark.anyio
async def test_oa_ticket_logs_in_bound_user(monkeypatch):
    client = _app(monkeypatch, {'oa_user_id': 'oa-user-101', 'name': '刘涛'})
    response = client.post('/auth/oa/exchange', json={'ticket': 'one-time'})
    assert response.status_code == 200, response.text
    body = response.json()['data']
    assert body['access_token']
    assert body['user']['name'] == '刘涛'


@pytest.mark.anyio
async def test_oa_ticket_rejects_unbound_user(monkeypatch):
    client = _app(monkeypatch, {'oa_user_id': 'no-such-oa-user', 'name': '未绑定'})
    response = client.post('/auth/oa/exchange', json={'ticket': 'one-time'})
    assert response.status_code == 403
    assert response.json()['code'] == 40301
    assert '绑定' in response.json()['message']
