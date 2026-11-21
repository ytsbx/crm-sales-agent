from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from conftest import SyncSessionAsAsync, make_engine
from app.core.deps import CurrentUser
from app.modules.notification.model import BusinessEvent, Notification
from app.modules.notification.router import (
    dismiss_notification,
    dismiss_read_notifications,
    list_notifications,
    unread_count,
)


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
