"""拒绝与取消必须重读动作状态；真PG的双事务竞态由check_agent_action_race验证。"""
from datetime import UTC, datetime

import pytest
from sqlalchemy import update
from starlette.requests import Request

from conftest import SyncSessionAsAsync, make_user
from app.core.audit import AuditLog
from app.core.errors import AppError
from app.modules.agent.model import AgentAction, AgentSession
from app.modules.agent.router import cancel_action, reject_action
from app.modules.agent.schema import ActionReject

REQUEST = Request({'type': 'http', 'headers': [], 'client': ('127.0.0.1', 1)})


@pytest.fixture
def action_db(db_session):
    for model in (AgentSession, AgentAction, AuditLog):
        model.__table__.create(db_session.get_bind(), checkfirst=True)
    db_session.add(AgentSession(id=1, user_id=101, title='隔离动作'))
    db_session.add(AgentAction(id=1, session_id=1, action_type='create_task', tool_name='create_task',
                               title='创建隔离任务', risk_level='L2', status='awaiting_confirmation'))
    db_session.commit()
    return db_session


class ActionSession(SyncSessionAsAsync):
    async def refresh(self, obj, **kwargs):
        self._session.refresh(obj, **kwargs)


class ExecutionCommittedDuringOwnershipCheck(ActionSession):
    async def get(self, entity, ident, *args, **kwargs):
        row = await super().get(entity, ident, *args, **kwargs)
        if entity is AgentSession:
            # 只模拟另一事务已提交的状态变化，不声称SQLite能验证行锁。
            with self._session.get_bind().begin() as connection:
                connection.execute(update(AgentAction).where(AgentAction.id == 1).values(
                    status='executed', result={'task_id': 9}, confirmed_by=101,
                    confirmed_at=datetime.now(UTC),
                ))
        return row


@pytest.mark.anyio
async def test_stale_rejection_cannot_overwrite_committed_execution(action_db):
    session = ExecutionCommittedDuringOwnershipCheck(action_db)
    with pytest.raises(AppError, match='已经处理'):
        await reject_action(1, ActionReject(reason='取消'), REQUEST, make_user(), session)
    action_db.expire_all()
    row = action_db.get(AgentAction, 1)
    assert row.status == 'executed' and row.result == {'task_id': 9}
    assert row.confirmed_at is not None
    assert action_db.query(AuditLog).count() == 0


@pytest.mark.anyio
@pytest.mark.parametrize('status', ['awaiting_confirmation', 'approval_required'])
async def test_pending_action_can_still_be_rejected(action_db, status):
    action_db.get(AgentAction, 1).status = status
    action_db.commit()
    result = await reject_action(1, ActionReject(reason='取消'), REQUEST, make_user(), ActionSession(action_db))
    assert result['code'] == 0
    assert action_db.get(AgentAction, 1).status == 'rejected'
    assert action_db.query(AuditLog).count() == 1


@pytest.mark.anyio
@pytest.mark.parametrize('status', ['executed', 'rejected', 'failed', 'cancelled'])
async def test_terminal_action_cannot_be_rejected(action_db, status):
    action_db.get(AgentAction, 1).status = status
    action_db.commit()
    with pytest.raises(AppError, match='已经处理'):
        await reject_action(1, ActionReject(reason='取消'), REQUEST, make_user(), ActionSession(action_db))
    assert action_db.get(AgentAction, 1).status == status
    assert action_db.query(AuditLog).count() == 0


@pytest.mark.anyio
async def test_other_user_cannot_reject_action(action_db):
    with pytest.raises(AppError) as exc:
        await reject_action(1, ActionReject(reason='取消'), REQUEST, make_user(102), ActionSession(action_db))
    assert exc.value.http_status == 404
    assert action_db.get(AgentAction, 1).status == 'awaiting_confirmation'
    assert action_db.query(AuditLog).count() == 0


@pytest.mark.anyio
async def test_stale_cancellation_cannot_overwrite_committed_execution(action_db):
    session = ExecutionCommittedDuringOwnershipCheck(action_db)
    with pytest.raises(AppError, match='不能取消'):
        await cancel_action(1, ActionReject(reason='撤回'), make_user(), session)
    action_db.expire_all()
    row = action_db.get(AgentAction, 1)
    assert row.status == 'executed' and row.result == {'task_id': 9}
    assert row.confirmed_at is not None
    assert action_db.query(AuditLog).count() == 0


@pytest.mark.anyio
@pytest.mark.parametrize('status', ['awaiting_confirmation', 'approval_required'])
async def test_pending_action_can_still_be_cancelled(action_db, status):
    action_db.get(AgentAction, 1).status = status
    action_db.commit()
    result = await cancel_action(1, ActionReject(reason='撤回'), make_user(), ActionSession(action_db))
    assert result['code'] == 0
    assert action_db.get(AgentAction, 1).status == 'cancelled'
    audit = action_db.query(AuditLog).one()
    assert audit.action == 'cancel' and audit.after_data == {'status': 'cancelled', 'reason': '撤回'}


@pytest.mark.anyio
@pytest.mark.parametrize('status', ['executed', 'rejected', 'failed', 'cancelled'])
async def test_terminal_action_cannot_be_cancelled(action_db, status):
    action_db.get(AgentAction, 1).status = status
    action_db.commit()
    with pytest.raises(AppError, match='不能取消'):
        await cancel_action(1, ActionReject(reason='撤回'), make_user(), ActionSession(action_db))
    assert action_db.get(AgentAction, 1).status == status
    assert action_db.query(AuditLog).count() == 0


@pytest.mark.anyio
async def test_other_user_cannot_cancel_action(action_db):
    with pytest.raises(AppError) as exc:
        await cancel_action(1, ActionReject(reason='撤回'), make_user(102), ActionSession(action_db))
    assert exc.value.http_status == 404
    assert action_db.get(AgentAction, 1).status == 'awaiting_confirmation'
    assert action_db.query(AuditLog).count() == 0
