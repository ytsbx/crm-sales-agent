"""真实 HTTP 参数和 SQL 筛选：先排除登录，再计数及分页。"""
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI

from app.core.jsonio import DecimalJSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.audit import AuditLog
from app.core.database import get_db
from app.core.deps import get_current_user
from app.modules.audit_router import router
from app.modules.user.model import User, Department
from tests.conftest import SyncSessionAsAsync, _register_sqlite_bigint_as_integer, make_user


@pytest.fixture
def client():
    _register_sqlite_bigint_as_integer()
    engine = create_engine('sqlite+pysqlite:///:memory:', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    from app.modules.bizdoc.model import BizDoc
    from app.modules.cases.model import SalesCase
    from app.modules.contract.model import ContractDocument
    from app.modules.dingtalk.model import OaInstance
    from app.modules.inquiry.model import CustomInquiry
    from app.modules.order.model import OrderDraft
    from app.modules.quote.model import Quote, QuoteCharge, QuoteItem, QuoteVersion
    from app.modules.sample.model import SampleItem, SampleRequest

    for model in (Department, User, AuditLog, BizDoc, SalesCase, ContractDocument, OaInstance,
                  CustomInquiry, OrderDraft, Quote, QuoteCharge, QuoteItem, QuoteVersion, SampleItem, SampleRequest):
        model.__table__.create(engine)
    with Session(engine) as session:
        for identity, action, kind in [(1,'update','customer'), (2,'create','quote'), (3,'approve','quote'),
            (4,'send','quote'), (5,'future_business_action','customer'), (6,'login','auth'),
            (7,'logout','auth'), (8,'refresh','auth')]:
            session.add(AuditLog(id=identity, action=action, business_type=kind, business_id=1,
                operator_id=2 if identity == 2 else 1, created_at=datetime(2026,11,16,12,tzinfo=UTC)))
        session.commit()
        app = FastAPI(default_response_class=DecimalJSONResponse)
        app.include_router(router)
        app.dependency_overrides[get_current_user] = lambda: make_user(1, permissions=("audit:view",))
        app.dependency_overrides[get_db] = lambda: SyncSessionAsAsync(session)
        with TestClient(app) as http:
            yield http
    engine.dispose()


def test_default_remains_all_actions(client):
    response = client.get('/audit-logs', params={'page_size': 2})
    assert response.status_code == 200
    assert response.json()['data']['total'] == 8
    assert [row['id'] for row in response.json()['data']['items']] == [8,7]


def test_exclusion_counts_and_pages_all_business_actions_including_future_ones(client):
    for page, expected in [(1,[5,4]), (2,[3,2]), (3,[1])]:
        response = client.get('/audit-logs', params={'exclude_actions':'login,logout,refresh', 'page':page, 'page_size':2})
        assert response.status_code == 200
        data = response.json()['data']
        assert data['total'] == 5
        assert [row['id'] for row in data['items']] == expected


@pytest.mark.parametrize('params,total,expected', [
    ({'action':'approve'},1,[3]),
    ({'business_type':'quote','operator_id':1},2,[4,3]),
    ({'keyword':'客户'},2,[5,1]),
    ({'action':'login'},0,[]),
    ({'start':'2026-11-17T00:00:00Z'},0,[]),
])
def test_exclusion_combines_with_existing_filters(client, params, total, expected):
    response = client.get('/audit-logs', params={'exclude_actions':'login,logout,refresh', **params})
    assert response.status_code == 200
    assert response.json()['data']['total'] == total
    assert [row['id'] for row in response.json()['data']['items']] == expected


@pytest.mark.parametrize('excluded,total', [(' , , ',8), ('unknown',8), (' login, logout,login,refresh, ',5)])
def test_exclusion_normalizes_empty_duplicate_and_unknown_values(client, excluded, total):
    response = client.get('/audit-logs', params={'exclude_actions': excluded})
    assert response.status_code == 200
    assert response.json()['data']['total'] == total


def test_exclusion_parameter_length_is_bounded(client):
    assert client.get('/audit-logs', params={'exclude_actions':'x'*1001}).status_code == 422
