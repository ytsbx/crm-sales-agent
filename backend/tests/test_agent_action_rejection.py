"""拒绝与取消必须重读动作状态；真PG的双事务竞态由check_agent_action_race验证。"""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import update
from starlette.requests import Request

from conftest import SyncSessionAsAsync, make_user
from app.core.audit import AuditLog
from app.core.errors import AppError
from app.modules.agent import runtime
from app.modules.agent.model import AgentAction, AgentExecution, AgentMessage, AgentSession
from app.modules.agent.router import (
    cancel_action, confirm_action, get_action, get_session_detail, list_actions, reject_action,
)
from app.modules.agent.schema import ActionReject

REQUEST = Request({'type': 'http', 'headers': [], 'client': ('127.0.0.1', 1)})


@pytest.fixture
def action_db(db_session):
    for model in (AgentSession, AgentAction, AgentMessage, AgentExecution, AuditLog):
        model.__table__.create(db_session.get_bind(), checkfirst=True)
    db_session.add(AgentSession(id=1, user_id=101, title='隔离动作'))
    db_session.add(AgentAction(id=1, session_id=1, action_type='create_task', tool_name='create_task',
                               title='创建隔离任务', risk_level='L2', status='awaiting_confirmation'))
    db_session.commit()
    return db_session


class ActionSession(SyncSessionAsAsync):
    async def refresh(self, obj, **kwargs):
        self._session.refresh(obj, **kwargs)


@pytest.mark.anyio
@pytest.mark.parametrize("status", ["awaiting_confirmation", "approval_required"])
@pytest.mark.parametrize("expired", [False, True])
async def test_action_confirmation_expires_at_24_hours(action_db, monkeypatch, status, expired):
    now = datetime(2026, 11, 19, 8, tzinfo=UTC)

    class FrozenTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz is not None else now.replace(tzinfo=None)

    monkeypatch.setattr(runtime, "datetime", FrozenTime)
    handler = AsyncMock(return_value={})
    monkeypatch.setitem(runtime.TOOLS, "create_task", replace(runtime.TOOLS["create_task"], handler=handler))
    row = action_db.get(AgentAction, 1)
    row.created_at = now - timedelta(hours=24)
    if not expired:
        row.created_at += timedelta(microseconds=1)
    row.status = status
    action_db.commit()
    session = ActionSession(action_db)
    user = make_user(roles=("admin",))

    if not expired:
        result = await confirm_action(1, user, session)
        assert result["data"]["action"]["status"] == "executed"
        handler.assert_awaited_once()
        now += timedelta(days=2)
        history = await get_action(1, user, session)
        assert history["data"]["status"] == "executed"
        return

    with pytest.raises(AppError, match="已过期.*重新发起"):
        await confirm_action(1, user, session)
    with pytest.raises(AppError, match="已过期"):
        await reject_action(1, ActionReject(reason="取消"), REQUEST, user, session)
    with pytest.raises(AppError, match="已过期"):
        await cancel_action(1, ActionReject(reason="撤回"), user, session)
    handler.assert_not_awaited()
    assert action_db.query(AgentExecution).count() == 0
    assert action_db.query(AuditLog).count() == 0
    assert action_db.get(AgentAction, 1).status == status

    detail = (await get_action(1, user, session))["data"]
    assert detail["status"] == "expired" and detail["status_label"] == "已过期"
    assert not detail["display"]["can_confirm"]
    assert "重新发起" in detail["display"]["result_text"]
    history = (await get_session_detail(1, user, session))["data"]["actions"]
    assert history[0]["status"] == "expired"
    assert (await list_actions(status, 1, 20, user, session))["data"]["total"] == 0
    assert (await list_actions("expired", 1, 20, user, session))["data"]["total"] == 1


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
