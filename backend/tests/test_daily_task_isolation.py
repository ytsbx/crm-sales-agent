from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.core import database, scheduler
from app.core.audit import AuditLog, write_audit
from app.modules.contract import support, service as contracts
from app.modules.notification import service as notifications
from app.modules.notification.model import Notification
from app.modules.payment import service as payments
from app.modules.quote import service as quotes
from app.modules.settings import service as settings
from app.modules.user.model import User
from app.modules.wecom import client as wecom
from tests.conftest import SyncSessionAsAsync, make_engine


@pytest.mark.anyio
async def test_retry_job_drains_pending_instant_notifications_without_waiting_for_daily_scan(monkeypatch):
    engine = make_engine()
    Notification.__table__.create(engine)
    with Session(engine) as session:
        session.add(User(id=1, name="接手人", username="test", password_hash="unused", wecom_userid="test-recipient"))
        session.add_all([
            Notification(user_id=1, type="task", title="交接待办", level=notifications.LEVEL_NORMAL, wecom_status="pending"),
            Notification(user_id=1, type="task", title="汇总提醒", level=notifications.LEVEL_DIGEST, wecom_status="pending"),
        ])
        session.commit()

    class SessionScope:
        async def __aenter__(self):
            self.session = Session(engine)
            return SyncSessionAsAsync(self.session)

        async def __aexit__(self, *args):
            self.session.close()

    send = AsyncMock()
    monkeypatch.setattr(scheduler, "SessionLocal", SessionScope)
    monkeypatch.setattr(database, "SessionLocal", SessionScope)
    monkeypatch.setattr(wecom, "get_client", lambda: SimpleNamespace(send_text_card=send))
    monkeypatch.setattr(scheduler.settings, "wecom_push_off", False)
    monkeypatch.setattr(scheduler.settings, "wecom_agent_id", "test")
    monkeypatch.setattr(scheduler.settings, "wecom_corp_id", "test")
    monkeypatch.setattr(scheduler.settings, "wecom_contact_secret", "test")
    monkeypatch.setattr(notifications, "materialize_business_notifications", AsyncMock())
    monkeypatch.setattr(notifications, "retry_policy", AsyncMock(return_value={"enabled": False}))

    await scheduler.run_notification_retry_job()

    send.assert_awaited_once_with(to_user="test-recipient", title="交接待办", description="交接待办")
    with Session(engine) as session:
        assert {row.title: row.wecom_status for row in session.query(Notification)} == {
            "交接待办": "sent", "汇总提醒": "pending",
        }
    engine.dispose()


@pytest.mark.anyio
async def test_daily_tasks_rollback_failed_step_and_commit_later_reminders(monkeypatch):
    engine = make_engine()
    AuditLog.__table__.create(engine)
    sessions = sessionmaker(engine)

    class SessionScope:
        async def __aenter__(self):
            self.session = sessions()
            return SyncSessionAsAsync(self.session)

        async def __aexit__(self, *args):
            self.session.close()

    async def failing_reminder(session):
        await write_audit(session, operator_id=None, action="partial_monthly")
        await session.flush()
        raise RuntimeError("月结步骤中断")

    async def quote_reminder(session):
        await write_audit(session, operator_id=None, action="quote_reminder")
        return 1

    followups = AsyncMock(return_value=2)
    dispatch = AsyncMock()
    monkeypatch.setattr(scheduler, "SessionLocal", SessionScope)
    monkeypatch.setattr(payments, "refresh_overdue_plans", AsyncMock(return_value=0))
    monkeypatch.setattr(settings, "run_auto_tasks", AsyncMock(return_value={}))
    monkeypatch.setattr(support, "remind_countersigns", AsyncMock(return_value=0))
    monkeypatch.setattr(contracts, "notify_expiring_monthly", failing_reminder)
    monkeypatch.setattr(quotes, "notify_expired_quotes", quote_reminder)
    monkeypatch.setattr(settings, "notify_due_followups", followups)
    monkeypatch.setattr(notifications, "dispatch_pending", dispatch)

    await scheduler.run_auto_tasks_job()
    followups.assert_awaited_once()
    dispatch.assert_awaited_once()
    with Session(engine) as session:
        rows = {row.action: row for row in session.query(AuditLog).all()}
        assert set(rows) == {"quote_reminder", "run_followup_deadlines"}
        summary = rows["run_followup_deadlines"].after_data
        assert summary["failed_steps"] == ["月结到期提醒"]
        assert summary["monthly_expiring_count"] is None
        assert summary["expired_quotes_count"] == 1 and summary["due_followups_count"] == 2
    engine.dispose()
