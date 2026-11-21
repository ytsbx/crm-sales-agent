"""逾期扫描保持既有状态/去重规则；真实PG锁序另由check_overdue_reminder_race验证。"""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from conftest import SyncSessionAsAsync
from app.modules.customer.model import Customer
from app.modules.notification.model import Notification
from app.modules.order import milestones
from app.modules.order.model import OrderMilestone, OrderShipmentBatch, SalesOrder
from app.modules.settings.model import SystemSetting
from app.modules.user.model import Role, User, user_roles
from app.core.timebase import today_business


@pytest.fixture
def reminder_db(db_session):
    for table in (Role.__table__, user_roles, Notification.__table__, SystemSetting.__table__, OrderShipmentBatch.__table__):
        table.create(db_session.get_bind(), checkfirst=True)
    db_session.add(User(id=101, username='isolated', name='隔离用户', password_hash='!', status='active'))
    db_session.add(Customer(id=1, name='隔离客户', owner_id=101))
    db_session.add(SalesOrder(id=1, order_no='REM-1', customer_id=1, owner_id=101, status='pending', currency='CNY'))
    db_session.commit()
    return db_session


@pytest.mark.anyio
@pytest.mark.parametrize('kind', ['milestone', 'batch'])
@pytest.mark.parametrize('state', ['overdue', 'done', 'future', 'no_plan', 'notified', 'cancelled_order', 'skipped_or_cancelled'])
async def test_reminder_keeps_status_and_dedup_rules(reminder_db, kind, state):
    raw = reminder_db
    today = today_business()
    model = OrderMilestone if kind == 'milestone' else OrderShipmentBatch
    values = dict(order_id=1, planned_date=today - timedelta(days=2), created_at=datetime.now(UTC))
    values.update(node='contract') if kind == 'milestone' else values.update(batch_no=1, status='planned')
    row = model(**values)
    if state == 'done':
        if kind == 'milestone':
            row.actual_date = today
        else:
            row.status = 'shipped'
    elif state == 'future':
        row.planned_date = today + timedelta(days=1)
    elif state == 'no_plan':
        row.planned_date = None
    elif state == 'notified':
        row.overdue_notified_at = datetime.now(UTC)
    elif state == 'cancelled_order':
        raw.get(SalesOrder, 1).status = 'cancelled'
    elif state == 'skipped_or_cancelled':
        if kind == 'milestone':
            row.skipped_at = datetime.now(UTC)
        else:
            row.status = 'cancelled'
    raw.add(row)
    raw.commit()
    fn = milestones.notify_overdue_milestones if kind == 'milestone' else milestones.notify_overdue_batches
    session = SyncSessionAsAsync(raw)
    expected = int(state == 'overdue')
    assert await fn(session) == expected
    await session.commit()
    assert raw.query(Notification).count() == expected
    if expected:
        notification = raw.query(Notification).one()
        assert notification.user_id == 101 and notification.business_id == 1
        assert notification.business_type == 'order' and row.overdue_notified_at is not None
        assert await fn(session) == 0
        assert raw.query(Notification).count() == 1


@pytest.mark.anyio
@pytest.mark.parametrize('kind', ['milestone', 'batch'])
async def test_child_state_rechecked_after_order_selection(reminder_db, kind):
    raw = reminder_db
    model = OrderMilestone if kind == 'milestone' else OrderShipmentBatch
    values = dict(order_id=1, planned_date=today_business() - timedelta(days=2), created_at=datetime.now(UTC))
    values.update(node='contract') if kind == 'milestone' else values.update(batch_no=1, status='planned')
    row = model(**values)
    raw.add(row)
    raw.commit()

    class StateChanged(SyncSessionAsAsync):
        changed = False

        async def execute(self, stmt, *args, **kwargs):
            result = await super().execute(stmt, *args, **kwargs)
            if not self.changed and stmt.column_descriptions[0].get('entity') is SalesOrder:
                self.changed = True
                # 模拟父行候选读取后的新事实；SQLite不证明并发行锁。
                values = {'actual_date': today_business()} if kind == 'milestone' else {'status': 'shipped'}
                raw.execute(update(model).where(model.id == row.id).values(**values))
            return result

    fn = milestones.notify_overdue_milestones if kind == 'milestone' else milestones.notify_overdue_batches
    session = StateChanged(raw)
    assert await fn(session) == 0
    assert raw.query(Notification).count() == 0
