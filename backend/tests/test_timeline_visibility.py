"""聚合时间线必须遵守任务/跟进原模块权限，过滤先于LIMIT。"""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.core.audit import AuditLog
from app.modules.customer.model import Customer, CustomerOwnerHistory
from app.modules.followup.model import FollowUp
from app.modules.lead.model import Lead
from app.modules.opportunity.model import Opportunity, OpportunityStage, OpportunityStageHistory
from app.modules.sample.model import SampleRequest
from app.modules.task.model import Task
from app.modules.timeline.service import build_timeline
from tests.conftest import SyncSessionAsAsync, make_engine, make_user


@pytest.fixture
def timeline_db():
    engine = make_engine()
    for model in (AuditLog, FollowUp, Task, CustomerOwnerHistory, OpportunityStageHistory, SampleRequest):
        model.__table__.create(engine, checkfirst=True)
    with Session(engine, expire_on_commit=False) as db:
        db.add_all([Customer(id=1, name='本人客户', owner_id=101), Customer(id=2, name='范围外客户', owner_id=999),
                    Lead(id=1, name='本人线索', owner_id=101), OpportunityStage(id=1, code='open', name='初步沟通')])
        db.flush()
        db.add(Opportunity(id=1, title='本人商机', customer_id=1, owner_id=101, stage_id=1))
        now = datetime.now(UTC)
        common = dict(customer_id=1, lead_id=1, opportunity_id=1)
        db.add_all([
            Task(id=1, title='OWN_TASK', owner_id=101, created_at=now, **common),
            Task(id=2, title='OTHER_TASK', owner_id=999, created_at=now, completed_at=now,
                 completion_note='OTHER_COMPLETION', **common),
            FollowUp(id=1, owner_id=999, followup_type='电话', content='COLLABORATIVE_FOLLOWUP', created_at=now, **common),
        ])
        db.flush()
        yield db
    engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize('kind', ['customer', 'lead', 'opportunity'])
async def test_no_module_permission_excludes_task_and_followup(timeline_db, kind):
    user = make_user(101, permissions=set())
    events = await build_timeline(SyncSessionAsAsync(timeline_db), kind, 1, user=user)
    assert not [event for event in events if event['kind'] in ('task', 'followup')]


@pytest.mark.anyio
@pytest.mark.parametrize('kind', ['customer', 'lead', 'opportunity'])
async def test_self_scope_tasks_and_existing_followup_collaboration(timeline_db, kind):
    user = make_user(101, permissions={'task:view', 'followup:view'})
    events = await build_timeline(SyncSessionAsAsync(timeline_db), kind, 1, user=user)
    text = str(events)
    assert 'OWN_TASK' in text and 'COLLABORATIVE_FOLLOWUP' in text
    assert 'OTHER_TASK' not in text and 'OTHER_COMPLETION' not in text


@pytest.mark.anyio
@pytest.mark.parametrize('admin', [False, True])
async def test_all_scope_and_admin_keep_authorized_tasks(timeline_db, admin):
    user = make_user(101, permissions=set() if admin else {'task:view', 'followup:view'},
                     roles=('admin',) if admin else ('salesperson',), data_scope='all')
    text = str(await build_timeline(SyncSessionAsAsync(timeline_db), 'customer', 1, user=user))
    assert all(value in text for value in ('OWN_TASK', 'OTHER_TASK', 'OTHER_COMPLETION', 'COLLABORATIVE_FOLLOWUP'))


@pytest.mark.anyio
async def test_task_owner_filter_precedes_limit(timeline_db):
    db = timeline_db
    now = datetime.now(UTC)
    for i in range(3, 106):
        db.add(Task(id=i, title=f'OUTSIDE_{i}', owner_id=999, customer_id=1,
                    created_at=now+timedelta(seconds=i)))
    db.flush()
    events = await build_timeline(SyncSessionAsAsync(db), 'customer', 1, limit=1,
                                  user=make_user(101, permissions={'task:view'}))
    assert len(events) == 1 and events[0]['detail'] == 'OWN_TASK'


@pytest.mark.anyio
async def test_followup_priority_scope_precedes_limit(timeline_db):
    db = timeline_db
    now = datetime.now(UTC)
    for i in range(2, 105):
        db.add(FollowUp(id=i, customer_id=2, opportunity_id=1, owner_id=101,
                       followup_type='电话', content=f'OUTSIDE_FOLLOWUP_{i}', created_at=now+timedelta(seconds=i)))
    db.flush()
    events = await build_timeline(SyncSessionAsAsync(db), 'opportunity', 1, limit=1,
                                  user=make_user(101, permissions={'followup:view'}))
    assert len(events) == 1 and events[0]['detail'] == 'COLLABORATIVE_FOLLOWUP'
