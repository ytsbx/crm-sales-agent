from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from conftest import SyncSessionAsAsync, make_engine, make_user
from app.core.deps import CurrentUser
from app.modules.customer.model import Customer
from app.modules.followup.model import FollowUp
from app.modules.followup.service import record_progress_event
from app.modules.lead.model import Lead
from app.modules.notification.model import BusinessEvent, Notification
from app.modules.notification.router import (
    dismiss_notification,
    dismiss_read_notifications,
    list_notifications,
    mark_all_read,
    unread_count,
)


@pytest.mark.anyio
async def test_concurrent_event_replay_preserves_the_callers_transaction():
    engine = make_engine()
    BusinessEvent.__table__.create(engine)
    FollowUp.__table__.create(engine, checkfirst=True)
    with Session(engine) as db:
        db.add(BusinessEvent(event_key="order:create:9", created_at=datetime.now(UTC)))
        db.commit()

        class StaleEventRead(SyncSessionAsAsync):
            async def execute(self, statement, *args, **kwargs):
                if statement.is_select and "business_events.event_key" in str(statement):
                    return SimpleNamespace(scalar_one_or_none=lambda: None)
                return await super().execute(statement, *args, **kwargs)

        db.add(Customer(id=90, name="本次事务创建的客户"))
        recorded = await record_progress_event(
            StaleEventRead(db), customer_id=90, operator_id=None,
            title="创建订单", content="创建订单", event_key="order:create:9",
        )
        assert recorded is False
        db.commit()
    with Session(engine) as db:
        assert db.get(Customer, 90) is not None
        assert db.query(BusinessEvent).count() == 1
        assert db.query(FollowUp).count() == 0
    engine.dispose()


@pytest.mark.anyio
async def test_personal_dismissal_persists_and_preserves_delivery_history():
    engine = make_engine()
    BusinessEvent.__table__.create(engine)
    Notification.__table__.create(engine)
    now = datetime.now(UTC)
    with Session(engine) as db:
        db.add_all([
            Notification(id=1, user_id=10, type="task", title="Remove this",
                         channel="both", wecom_status="failed", wecom_attempts=2),
            Notification(id=2, user_id=10, type="task", title="Already read", read_at=now),
            Notification(id=3, user_id=10, type="task", title="Keep unread"),
            Notification(id=4, user_id=20, type="task", title="Colleague's notice", read_at=now),
        ])
        db.commit()

    owner = CurrentUser(SimpleNamespace(id=10, name="A", username="a", department_id=None), set(), [], "self")
    other = CurrentUser(SimpleNamespace(id=20, name="B", username="b", department_id=None), set(), [], "self")
    with Session(engine) as db:
        session = SyncSessionAsAsync(db)
        await dismiss_notification(1, other, session)
        assert db.get(Notification, 1).dismissed_at is None
        await dismiss_notification(1, owner, session)
        dismissed_at = db.get(Notification, 1).dismissed_at
        await dismiss_notification(1, owner, session)
        assert db.get(Notification, 1).dismissed_at == dismissed_at
        await dismiss_read_notifications(owner, session)

    with Session(engine) as db:
        session = SyncSessionAsAsync(db)
        owner_list = await list_notifications(False, None, None, 1, 10, owner, session)
        assert [item["id"] for item in owner_list["data"]["items"]] == [3]
        assert (await unread_count(owner, session))["data"]["count"] == 1
        other_list = await list_notifications(False, None, None, 1, 10, other, session)
        assert [item["id"] for item in other_list["data"]["items"]] == [4]
        assert db.scalars(select(Notification.id).order_by(Notification.id)).all() == [1, 2, 3, 4]
        assert (db.get(Notification, 1).wecom_status, db.get(Notification, 1).wecom_attempts) == ("failed", 2)
    engine.dispose()


@pytest.mark.anyio
async def test_read_all_covers_all_visible_pages_and_keeps_hidden_notice_unread():
    engine = make_engine()
    BusinessEvent.__table__.create(engine)
    Notification.__table__.create(engine)
    now = datetime.now(UTC)
    with Session(engine) as db:
        db.add_all([
            Customer(id=1, name="Current customer", owner_id=10),
            Lead(id=1, name="Hidden lead", owner_id=10),
            BusinessEvent(id=1, event_key="visible", created_at=now),
            BusinessEvent(id=2, event_key="hidden", created_at=now),
            BusinessEvent(id=3, event_key="visible-first", created_at=now),
        ])
        db.add_all([
            Notification(id=1, user_id=10, type="followup", title="Visible page 2",
                         business_event_id=1, business_type="customer", business_id=1),
            Notification(id=2, user_id=10, type="followup", title="Visible first page",
                         business_event_id=3, business_type="customer", business_id=1),
            Notification(id=3, user_id=10, type="followup", title="Hidden",
                         business_event_id=2, business_type="lead", business_id=1),
            Notification(id=4, user_id=20, type="task", title="Another user"),
        ])
        db.commit()
        session = SyncSessionAsAsync(db)
        viewer = make_user(10, permissions=["customer:view"], data_scope="self")
        page = await list_notifications(True, None, None, 1, 1, viewer, session)
        assert page["data"]["total"] == 2
        await mark_all_read(viewer, session)
        assert db.get(Notification, 1).read_at is not None
        assert db.get(Notification, 2).read_at is not None
        assert db.get(Notification, 4).read_at is None
        restored = make_user(10, permissions=["customer:view", "lead:view"], data_scope="self")
        unread = await list_notifications(True, None, None, 1, 10, restored, session)
        assert [row["id"] for row in unread["data"]["items"]] == [3]
    engine.dispose()
