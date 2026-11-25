"""未分级与独立规则回归：只使用内存数据库。"""
import asyncio
from datetime import UTC, datetime, timedelta
import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.requests import Request
from conftest import SyncSessionAsAsync, make_engine
from app.core.base import Base
from app.core.audit import AuditLog
from app.core.deps import CurrentUser
from app.modules.customer.model import Customer
from app.modules.customer.schema import CustomerCreate, CustomerUpdate
from app.modules.customer.levels import level_filter
from app.modules.customer.service import build_list_stmt
from app.modules.settings.model import PublicPoolRule, TaskRule, SystemSetting, PublicPoolRecycleCandidate
from app.modules.settings.schema import UnratedRulesInput
from app.modules.settings.router import save_unrated_rules
from app.modules.settings.service import run_auto_tasks
from app.modules.task.model import Task
from app.modules.user.model import User


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def db():
    engine = make_engine()
    Base.metadata.create_all(engine, tables=[x.__table__ for x in (PublicPoolRule, TaskRule, SystemSetting, PublicPoolRecycleCandidate, AuditLog, Task)])
    with Session(engine) as raw:
        s = SyncSessionAsAsync(raw)
        user = User(id=1, username='test', password_hash='x', name='测试', status='active')
        s.add(user)
        run(s.flush())
        yield s, CurrentUser(user, {'settings:manage'}, ['admin'], 'all')
    engine.dispose()


def save(db, **kwargs):
    s, user = db
    return run(save_unrated_rules(UnratedRulesInput(**kwargs), Request({'type': 'http', 'headers': []}), user, s))


def test_default_and_explicit_unrate():
    assert CustomerCreate(name='新客户').level is None
    assert CustomerUpdate(level=None).model_dump(exclude_unset=True) == {'level': None}


@pytest.mark.parametrize('levels,expected', [('UNRATED', [1,2,3]), ('A', [4]), (['A','UNRATED'], [1,2,3,4]), ([], [])])
def test_null_blank_and_rated_filters(db, levels, expected):
    s, _ = db
    for i, level in enumerate([None, '', '  ', 'A', 'B'], 1):
        s.add(Customer(id=i, name=f'客户{i}', level=level))
    run(s.flush())
    found = run(s.execute(select(Customer.id).where(level_filter(Customer.level, levels)).order_by(Customer.id))).scalars().all()
    assert found == expected
    if levels == 'UNRATED':
        assert sorted(c.id for c in run(s.execute(build_list_stmt(level='UNRATED'))).scalars()) == expected


def test_closed_without_days_creates_no_rule(db):
    save(db)
    s, _ = db
    assert not run(s.execute(select(TaskRule))).scalars().all()
    assert not run(s.execute(select(PublicPoolRule))).scalars().all()


@pytest.mark.parametrize('kwargs', [{'reminder_enabled': True}, {'recycle_enabled': True}, {'reminder_days': 0}, {'recycle_days': 36501}])
def test_enable_needs_valid_days(kwargs):
    with pytest.raises(ValidationError):
        UnratedRulesInput(**kwargs)


def test_repeat_close_and_other_grades(db):
    s, _ = db
    s.add(PublicPoolRule(level='A', days=30, enabled=True))
    s.add(TaskRule(code='rated', name='A提醒', trigger_type='customer_silent', trigger_config={'days':14,'levels':['A']}, status='active'))
    run(s.flush())
    save(db, reminder_days=7, recycle_days=20)
    task=run(s.execute(select(TaskRule).where(TaskRule.code=='customer_unrated_silent'))).scalar_one()
    pool=run(s.execute(select(PublicPoolRule).where(PublicPoolRule.level=='UNRATED'))).scalar_one()
    assert task.status=='inactive' and not pool.enabled
    ids=task.id,pool.id
    save(db, reminder_days=9, recycle_days=25, reminder_enabled=True, recycle_enabled=True)
    assert (task.id,pool.id)==ids and task.trigger_config=={'days':9,'levels':['UNRATED']}
    assert task.status=='active' and pool.enabled and pool.days==25
    save(db)
    assert task.status=='inactive' and task.trigger_config['days']==9
    assert not pool.enabled and pool.days==25
    assert run(s.execute(select(PublicPoolRule).where(PublicPoolRule.level=='A'))).scalar_one().enabled
    assert len(run(s.execute(select(TaskRule))).scalars().all())==2
    assert len(run(s.execute(select(PublicPoolRule))).scalars().all())==2
    assert len(run(s.execute(select(AuditLog))).scalars().all())==6


def test_reminder_only_unrated_repeat_and_rating(db):
    s, _ = db
    old=datetime.now(UTC)-timedelta(days=20)
    for i, level in enumerate([None,'','A'],1):
        s.add(Customer(id=i,name=f'客户{i}',level=level,owner_id=1,pool_status='private',last_followup_at=old))
    run(s.flush())
    save(db,reminder_days=7)
    assert not run(run_auto_tasks(s,1))['tasks']
    save(db,reminder_days=7,reminder_enabled=True)
    assert len(run(run_auto_tasks(s,1))['tasks'])==2
    assert not run(run_auto_tasks(s,1))['tasks']
    tasks=run(s.execute(select(Task))).scalars().all()
    assert {t.customer_id for t in tasks}=={1,2}
    for t in tasks: t.status='done'
    c=run(s.get(Customer,1));c.level='B'
    run(s.flush())
    assert len(run(run_auto_tasks(s,1))['tasks'])==1
    assert run(s.execute(select(Task).where(Task.status=='pending'))).scalar_one().customer_id==2


def test_recycle_only_unrated_and_no_automatic_release(db, monkeypatch):
    from app.modules.settings import service as svc
    s, _ = db
    Base.metadata.create_all(s._session.get_bind(), tables=[PublicPoolRecycleCandidate.__table__])
    async def protection(*args, **kwargs): return {}
    async def disputed(*args, **kwargs): return False
    async def notify(*args, **kwargs): return 0
    monkeypatch.setattr(svc, 'protection_detail', protection)
    monkeypatch.setattr(svc.duplicates, 'is_disputed', disputed)
    monkeypatch.setattr(svc, '_notify_recycle_candidate', notify)
    old=datetime.now(UTC)-timedelta(days=20)
    for i, level in enumerate([None,'','A'],1):
        s.add(Customer(id=i,name=f'客户{i}',level=level,owner_id=1,pool_status='private',last_followup_at=old))
    run(s.flush())
    save(db,recycle_days=7)
    assert run(svc.run_public_pool_recycle(s,1))['nominated_count']==0
    save(db,recycle_days=7,recycle_enabled=True)
    assert run(svc.run_public_pool_recycle(s,1))['nominated_count']==2
    assert run(svc.run_public_pool_recycle(s,1))['nominated_count']==0
    assert {c.customer_id for c in run(s.execute(select(PublicPoolRecycleCandidate))).scalars()}=={1,2}
    assert all(c.owner_id==1 and c.pool_status=='private' for c in run(s.execute(select(Customer))).scalars())


@pytest.mark.parametrize('level,status', [(None,422), ('',422), ('A',409)])
def test_recycle_candidate_rechecks_rating(db, level, status):
    from app.modules.settings import service as svc
    from app.core.errors import AppError
    s, _ = db
    Base.metadata.create_all(s._session.get_bind(), tables=[PublicPoolRecycleCandidate.__table__])
    s.add(Customer(id=1,name='客户',level=level,owner_id=1,pool_status='private'))
    candidate=PublicPoolRecycleCandidate(customer_id=1,owner_id=1,level='UNRATED',status='pending',
        notice_at=datetime.now(UTC),due_at=datetime.now(UTC)+timedelta(days=7))
    s.add(candidate);run(s.flush())
    with pytest.raises(AppError) as err:
        run(svc.decide_candidate(s,candidate=candidate,decision='approve',operator_id=1))
    assert err.value.http_status==status
    assert run(s.get(Customer,1)).owner_id==1


@pytest.mark.parametrize('kind', ['quote_no_followup', 'customer_silent', 'receivable_due'])
@pytest.mark.parametrize('days', [4000000, 1000000000])
def test_unrepresentable_rule_date_is_reported_without_aborting_scan(db, kind, days):
    s, user = db
    s.add(TaskRule(code='DATE-OVERFLOW',name='日期越界',trigger_type=kind,
                   trigger_config={'days':days},status='active'))
    run(s.commit())
    result=run(run_auto_tasks(s,user.id))
    assert result['failed_rule_count']==1
    assert result['rule_errors'][0]['code']=='DATE-OVERFLOW'
    assert result['rule_errors'][0]['error']=='days 超出日期可计算范围'
    assert result['created_count']==0
    audit=run(s.execute(select(AuditLog).where(AuditLog.action=='run_auto_tasks'))).scalar_one()
    assert audit.after_data['failed_rule_count']==1
