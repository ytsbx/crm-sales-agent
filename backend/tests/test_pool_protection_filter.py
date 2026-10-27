"""定向保护查询与全量口径一致；扫描保持最长保护期及禁用上限语义。"""
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.orm import Session

from app.core.audit import AuditLog
from app.core.timebase import today_business
from app.modules.customer.model import Customer
from app.modules.order.model import SalesOrder
from app.modules.payment.model import ReceivablePlan
from app.modules.quote.model import Quote
from app.modules.sample.model import SampleRequest
from app.modules.settings import service
from app.modules.settings.model import PublicPoolRecycleCandidate, PublicPoolRule, SystemSetting
from tests.conftest import SyncSessionAsAsync, make_engine


@pytest.fixture
def pool_db():
    engine = make_engine()
    for model in (SampleRequest, SystemSetting, PublicPoolRule, PublicPoolRecycleCandidate, AuditLog):
        model.__table__.create(engine, checkfirst=True)
    with Session(engine, expire_on_commit=False) as db:
        yield db
    engine.dispose()


@pytest.mark.anyio
async def test_targeted_matches_global_for_documents_and_revision_chains(pool_db):
    db = pool_db
    now = datetime.now(UTC)
    today = today_business()
    for cid in (1, 2):
        db.add(Customer(id=cid, name=f'隔离客户{cid}'))
        db.add(Quote(id=cid, quote_no=f'ISO-Q-{cid}', customer_id=cid, status='sent', valid_until=today))
        db.add(SalesOrder(id=cid, order_no=f'ISO-O-{cid}', customer_id=cid, status='pending'))
        db.add(ReceivablePlan(id=cid, order_id=cid, plan_name='隔离应收', due_date=today, status='pending', created_at=now))
        db.add(SampleRequest(id=cid, customer_id=cid, status='pending', created_at=now))
    # Latest accepted child suppresses its parent, including legacy cross-customer chains.
    db.add(SampleRequest(id=3, customer_id=1, status='pending', created_at=now))
    db.add(SampleRequest(id=4, customer_id=2, parent_id=3, status='signed', confirm_status='accepted', created_at=now))
    db.add(SampleRequest(id=5, customer_id=1, status='signed', confirm_status='accepted', created_at=now))
    db.add(SampleRequest(id=6, customer_id=1, status='pending', deleted_at=now, created_at=now))
    db.add(Quote(id=3, quote_no='ISO-Q-expired', customer_id=1, status='sent', valid_until=today-timedelta(days=1)))
    db.flush()
    session = SyncSessionAsAsync(db)
    all_reasons = await service.protection_detail(session)
    assert set(all_reasons) == {1, 2}
    assert all(len(reasons) == 4 for reasons in all_reasons.values())
    for cid in (1, 2, 999):
        result = await service.protection_detail(session, customer_id=cid)
        assert result == ({cid: all_reasons[cid]} if cid in all_reasons else {})


@pytest.mark.anyio
@pytest.mark.parametrize('idle_days,max_days,nominated', [(60,90,0),(100,90,1),(100,0,0)])
async def test_scan_keeps_protection_cap(pool_db, monkeypatch, idle_days, max_days, nominated):
    db = pool_db
    now = datetime.now(UTC)
    db.add(Customer(id=1, name='隔离客户', owner_id=1, pool_status='private', level='B',
                    last_contact_unknown=False, last_followup_at=now-timedelta(days=idle_days)))
    db.add(SampleRequest(id=1, customer_id=1, status='pending', created_at=now))
    db.add(SystemSetting(key='pool_recycle_max_protect_days',value={'days':max_days}))
    db.add(PublicPoolRule(level='B', days=30, enabled=True))
    db.flush()
    monkeypatch.setattr(service.duplicates, 'is_disputed', AsyncMock(return_value=False))
    monkeypatch.setattr(service, '_notify_recycle_candidate', AsyncMock(return_value=0))
    result = await service.run_public_pool_recycle(SyncSessionAsAsync(db), 1)
    assert result['nominated_count'] == nominated
    assert result['protected_count'] == 1-nominated
    assert result['protection_overridden_count'] == nominated
    if nominated:
        row = db.query(PublicPoolRecycleCandidate).one()
        assert row.protection_snapshot and result['candidates'][0]['protection_reasons'] == row.protection_snapshot
