"""Dashboard drill-down must include pending/doing and preserve owner scope."""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from conftest import SyncSessionAsAsync, make_engine, make_user
from app.modules.task.model import Task
from app.modules.task.router import list_tasks


@pytest.mark.anyio
@pytest.mark.parametrize('scope,mine,overdue,expected', [
    ('all', False, False, {1, 2, 3}),
    ('all', False, True, {1, 2}),
    ('all', True, True, {2}),
    ('self', False, True, {2}),
])
async def test_dashboard_active_tasks(scope, mine, overdue, expected):
    engine = make_engine()
    Task.__table__.create(engine)
    now = datetime.now(UTC)
    try:
        with Session(engine) as raw:
            for id, owner, status, days in [
                (1, 2, 'pending', -2), (2, 1, 'doing', -1),
                (3, 1, 'pending', 2), (4, 1, 'done', -2),
                (5, 2, 'cancelled', -2),
            ]:
                raw.add(Task(id=id, owner_id=owner, title=f'task {id}',
                             status=status, due_at=now + timedelta(days=days)))
            raw.commit()
            result = await list_tasks(
                status='active', mine=mine, overdue=overdue, owner_id=None,
                opportunity_id=None, customer_id=None, page=1, page_size=20,
                user=make_user(1, data_scope=scope), session=SyncSessionAsAsync(raw),
            )
            assert {item['id'] for item in result['data']['items']} == expected
            assert result['data']['total'] == len(expected)
    finally:
        engine.dispose()
