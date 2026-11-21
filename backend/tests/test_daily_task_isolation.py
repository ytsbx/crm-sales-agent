from unittest.mock import AsyncMock

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.core import scheduler
from app.core.audit import AuditLog, write_audit
from app.modules.contract import customer_flow, service as contracts
from app.modules.notification import service as notifications
from app.modules.payment import service as payments
from app.modules.quote import service as quotes
from app.modules.settings import service as settings
from tests.conftest import SyncSessionAsAsync, make_engine


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
    monkeypatch.setattr(customer_flow, "remind_countersigns", AsyncMock(return_value=0))
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
