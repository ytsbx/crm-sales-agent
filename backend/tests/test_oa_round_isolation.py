"""旧轮次迟到事件只能更新历史OA，不能覆盖重提后的业务主单。"""
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.orm import Session

from app.core.config import settings
from app.modules.contract.model import ContractDocument, ContractTemplate
from app.modules.customer.model import Customer
from app.modules.dingtalk import service
from app.modules.dingtalk.model import OaInstance
from app.modules.sample.model import SampleRequest
from tests.conftest import SyncSessionAsAsync, make_engine


@pytest.fixture
def round_db(monkeypatch):
    engine = make_engine()
    for table in (SampleRequest.__table__, ContractTemplate.__table__,
                  ContractDocument.__table__, OaInstance.__table__):
        table.create(engine)
    monkeypatch.setattr(settings, 'dingtalk_push_off', False)
    with Session(engine, expire_on_commit=False) as db:
        yield db
    engine.dispose()


def seed(db, kind):
    now = datetime.now(UTC)
    db.add(Customer(id=1, name='隔离OA客户'))
    db.flush()
    if kind == 'sample':
        business = SampleRequest(id=7, status='pending', version=1, created_at=now,
            source_context={'dingtalk_oa': {'mode': 'standalone', 'submitted': True}})
    else:
        business = ContractDocument(id=7, doc_no='ISO-OA-7', title='隔离合同',
            customer_id=1, content_snapshot='synthetic', created_at=now,
            filled_data={'_dingtalk_oa': {'oa_instance_id': 2, 'instance_id': 'NEW', 'status': 'pending'}})
    db.add(business)
    for i, status in ((1, 'rejected'), (2, 'pending')):
        db.add(OaInstance(id=i, inquiry_id=7, inquiry_version=1, oa_type=kind,
            submit_round=i, idempotency_key=f'iso-{kind}-{i}',
            instance_id='OLD' if i == 1 else 'NEW', status=status, created_at=now))
    db.commit()
    return business


@pytest.mark.anyio
@pytest.mark.parametrize('kind', ['sample', 'contract'])
@pytest.mark.parametrize('snapshot', [
    {'status': 'COMPLETED', 'result': 'refuse'},
    {'status': 'COMPLETED', 'result': 'agree'},
    {'status': 'TERMINATED'},
])
async def test_old_round_sync_does_not_rewrite_current_business(round_db, monkeypatch, kind, snapshot):
    db = round_db
    business = seed(db, kind)
    client = type('FakeClient', (), {'get_process_instance': AsyncMock(return_value=snapshot)})()
    monkeypatch.setattr(service, 'get_client', lambda: client)
    notify = AsyncMock()
    monkeypatch.setattr(service, '_notify_contract_review_outcome', notify)
    await service.sync_pending_instances(SyncSessionAsAsync(db), oa_id=1)
    db.commit()
    assert db.get(OaInstance, 1).result['payload'] == snapshot
    assert db.get(OaInstance, 2).status == 'pending'
    if kind == 'sample':
        assert business.status == 'pending'
        assert business.approved_at is None
        assert business.reject_reason is None
    else:
        assert business.filled_data == {'_dingtalk_oa': {
            'oa_instance_id': 2, 'instance_id': 'NEW', 'status': 'pending'}}
    notify.assert_not_awaited()
    client.get_process_instance.assert_awaited_once_with('OLD')


@pytest.mark.anyio
@pytest.mark.parametrize('kind', ['sample', 'contract'])
@pytest.mark.parametrize('result,expected', [('agree', 'approved'), ('refuse', 'rejected')])
async def test_latest_round_still_updates_business_once(round_db, monkeypatch, kind, result, expected):
    db = round_db
    business = seed(db, kind)
    client = type('FakeClient', (), {'get_process_instance': AsyncMock(return_value={
        'status': 'COMPLETED', 'result': result})})()
    monkeypatch.setattr(service, 'get_client', lambda: client)
    notify = AsyncMock()
    monkeypatch.setattr(service, '_notify_contract_review_outcome', notify)
    adapter = SyncSessionAsAsync(db)
    await service.sync_pending_instances(adapter, oa_id=2)
    await service.sync_pending_instances(adapter, oa_id=2)
    assert db.get(OaInstance, 2).status == expected
    assert db.get(OaInstance, 1).status == 'rejected'
    if kind == 'sample':
        assert business.status == expected
        assert business.approved_at is not None
        notify.assert_not_awaited()
    else:
        assert business.filled_data['_dingtalk_oa']['oa_instance_id'] == 2
        assert business.filled_data['_dingtalk_oa']['status'] == expected
        notify.assert_awaited_once()
