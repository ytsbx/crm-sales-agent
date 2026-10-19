"""Real SQL regression for stale thresholds, totals, paging and visibility."""
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from conftest import SyncSessionAsAsync, make_engine, make_user
from app.modules.analytics import service
from app.modules.customer.model import Customer


@pytest.mark.anyio
async def test_new_customers_wait_until_threshold(monkeypatch):
    now = datetime(2026, 10, 19, 4, tzinfo=UTC)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(service, 'datetime', Clock)
    monkeypatch.setattr(service.settings_service, 'get_number', AsyncMock(return_value=30))
    engine = make_engine()
    try:
        with Session(engine) as raw:
            for id, age, followup, progress in [
                (1, 3, None, None), (2, 31, None, None),
                (3, 60, 3, None), (4, 60, None, 3),
                (5, 60, 31, 40), (6, 30, None, None),
            ]:
                raw.add(Customer(id=id, name=f'customer {id}', owner_id=1,
                                 pool_status='private', created_at=now-timedelta(days=age),
                                 last_followup_at=now-timedelta(days=followup) if followup else None,
                                 last_progress_at=now-timedelta(days=progress) if progress else None))
            raw.commit()
            cutoff = now-timedelta(days=30)
            ids = set(raw.scalars(select(Customer.id).where(service._customer_stale_clause(cutoff))))
            assert ids == {2, 5}
            result = await service.stale_customers(SyncSessionAsAsync(raw), make_user(1))
            assert result['total'] == 2
            assert {row['id'] for row in result['items']} == ids
            assert all(row['days_since'] >= 30 for row in result['items'])
    finally:
        engine.dispose()


@pytest.mark.anyio
async def test_stale_pagination_keeps_true_total_and_scope(monkeypatch):
    monkeypatch.setattr(service.settings_service, 'get_number', AsyncMock(return_value=30))
    engine = make_engine()
    try:
        with Session(engine) as raw:
            old = datetime.now(UTC)-timedelta(days=40)
            raw.add_all(Customer(id=id, name=f'customer {id}', owner_id=1,
                                 pool_status='private', created_at=old) for id in range(1, 206))
            raw.add(Customer(id=206, name='other owner', owner_id=2, pool_status='private', created_at=old))
            raw.add(Customer(id=207, name='public', owner_id=1, pool_status='public', created_at=old))
            raw.add(Customer(id=208, name='deleted', owner_id=1, pool_status='private', created_at=old, deleted_at=old))
            raw.commit()
            s = SyncSessionAsAsync(raw)
            user = make_user(1)
            first = await service.stale_customers(s, user, page=1, page_size=200)
            second = await service.stale_customers(s, user, page=2, page_size=200)
            assert first['total'] == second['total'] == 205
            assert len(first['items']) == 200
            assert {row['id'] for row in second['items']} == set(range(201, 206))
            assert first['page'] == 1 and second['page'] == 2
            assert second['page_size'] == 200
            empty = await service.stale_customers(s, user, page=3, page_size=200)
            assert empty['total'] == 205 and empty['items'] == []
    finally:
        engine.dispose()
