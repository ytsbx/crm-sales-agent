"""客户列表「久未联系」筛选：按最近联系/进展排序，含从未联系的客户。"""

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.modules.customer.model import Customer
from app.modules.customer.service import build_list_stmt
from conftest import make_engine


def test_stale_filter_orders_by_last_activity_and_includes_never_contacted():
    engine = make_engine()
    now = datetime.now(UTC)
    with Session(engine) as session:
        session.add_all([
            Customer(id=1, name="联系很早", owner_id=1,
                     created_at=now - timedelta(days=200),
                     last_followup_at=now - timedelta(days=90)),
            Customer(id=2, name="从未联系", owner_id=1,
                     created_at=now - timedelta(days=60)),
            Customer(id=3, name="最近联系", owner_id=1,
                     created_at=now - timedelta(days=200),
                     last_followup_at=now - timedelta(days=5)),
            Customer(id=4, name="进展较早", owner_id=1,
                     created_at=now - timedelta(days=200),
                     last_progress_at=now - timedelta(days=120)),
        ])
        session.commit()
        rows = session.execute(
            build_list_stmt(activity="stale", stale_before=now - timedelta(days=30))
        ).scalars().all()
        # 最久没联系的排最前；5 天前联系过的（id=3）不算久未联系
        assert [row.id for row in rows] == [4, 1, 2]
    engine.dispose()


def test_no_activity_filter_keeps_original_order():
    engine = make_engine()
    with Session(engine) as session:
        session.add_all([
            Customer(id=1, name="甲", owner_id=1),
            Customer(id=2, name="乙", owner_id=1),
        ])
        session.commit()
        rows = session.execute(build_list_stmt()).scalars().all()
        assert [row.id for row in rows] == [2, 1]
    engine.dispose()
