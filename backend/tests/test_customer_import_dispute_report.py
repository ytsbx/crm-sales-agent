"""客户行保存点失败时，不得报告已开待裁定；PG真实错误另留复现证据。"""
from io import BytesIO
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request
from starlette.datastructures import UploadFile

from conftest import SyncSessionAsAsync, make_user
from app.core.audit import AuditLog
from app.modules.customer import io_router, duplicates
from app.modules.customer.model import Customer
from app.modules.settings.model import SystemSetting
from app.modules.user.model import User


class Transaction:
    def __init__(self, tx):
        self.tx = tx

    async def commit(self):
        self.tx.commit()

    async def rollback(self):
        self.tx.rollback()


class ImportSession(SyncSessionAsAsync):
    async def begin_nested(self):
        return Transaction(self._session.begin_nested())

    @property
    def new(self):
        return list(self._session.new)

    def expunge(self, row):
        self._session.expunge(row)


@pytest.fixture
def customer_report_case(db_session, monkeypatch):
    for model in (AuditLog, SystemSetting):
        model.__table__.create(db_session.get_bind(), checkfirst=True)
    db_session.add(User(id=101, name='隔离用户', username='isolated', password_hash='!', status='active'))
    db_session.add(Customer(id=1, name='隔离同名客户', owner_id=101))
    db_session.commit()
    monkeypatch.setattr(io_router, 'find_duplicate_customers', AsyncMock(return_value=[
        {'id': 1, 'name': '隔离同名客户', 'score': 1, 'reasons': ['同名']},
    ]))
    return db_session


@pytest.mark.anyio
@pytest.mark.parametrize('stage', ['customer_insert', 'case_insert', 'success'])
async def test_failed_customer_not_reported_as_disputed(customer_report_case, monkeypatch, stage):
    raw = customer_report_case

    class RowSession(ImportSession):
        async def flush(self):
            if stage == 'customer_insert' and any(isinstance(row, Customer) for row in self.new):
                raise ValueError('controlled customer insert failure')
            await super().flush()

    async def open_cases(session, **kwargs):
        # 用户可见的报告必须等整行保存点成功；此故障点在客户已flush之后。
        if stage == 'case_insert':
            raise ValueError('controlled case insert failure')
        return []

    monkeypatch.setattr(duplicates, 'open_cases_for_customer', open_cases)
    result = await io_router.import_customers(
        Request({'type': 'http', 'headers': [], 'client': ('127.0.0.1', 1)}),
        UploadFile(BytesIO('客户名称\n隔离同名客户\n'.encode('utf-8-sig')), filename='isolated.csv'),
        False, None, make_user(101, permissions={'customer:create'}, roles=['salesperson']), RowSession(raw),
    )
    expected = int(stage == 'success')
    data = result['data']
    assert data['created_count'] == expected
    assert data['failed_count'] == 1 - expected
    assert data.get('disputed_count', 0) == expected
    assert ('已进待裁定' in result['message']) is bool(expected)
    assert raw.query(Customer).count() == 1 + expected
    if expected:
        assert data['disputed'][0]['row'] == 2
        assert data['disputed'][0]['candidate'] is None
    audit = raw.query(AuditLog).one()
    assert audit.after_data['created'] == expected
    assert audit.after_data['failed'] == 1 - expected
