"""客户列表「在途业务停滞」筛选：有在途业务且久未活动才列出。"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.modules.customer.model import Customer
from app.modules.customer.service import build_list_stmt
from app.modules.order.model import SalesOrder
from conftest import make_engine


def _order(order_id: int, customer_id: int, *, status: str = "pending") -> SalesOrder:
    return SalesOrder(
        id=order_id, order_no=f"SO-TEST-{order_id}", customer_id=customer_id,
        total_amount=Decimal("100"), currency="CNY", status=status,
    )


def test_stale_filter_requires_in_flight_business_and_orders_by_last_activity():
    engine = make_engine()
    now = datetime.now(UTC)
    with Session(engine) as session:
        session.add_all([
            Customer(id=1, name="联系很早", owner_id=1,
                     created_at=now - timedelta(days=200),
                     last_followup_at=now - timedelta(days=90)),
            Customer(id=2, name="从未联系但有单", owner_id=1,
                     created_at=now - timedelta(days=60)),
            Customer(id=3, name="最近联系", owner_id=1,
                     created_at=now - timedelta(days=200),
                     last_followup_at=now - timedelta(days=5)),
            Customer(id=4, name="进展较早", owner_id=1,
                     created_at=now - timedelta(days=200),
                     last_progress_at=now - timedelta(days=120)),
            # 没有在途业务：再久没联系也不催
            Customer(id=5, name="只剩导入档案", owner_id=1,
                     created_at=now - timedelta(days=200)),
            Customer(id=6, name="订单已完成", owner_id=1,
                     created_at=now - timedelta(days=200),
                     last_followup_at=now - timedelta(days=90)),
        ])
        session.add_all([
            _order(1, 1), _order(2, 2), _order(3, 3), _order(4, 4),
            _order(5, 6, status="completed"),
        ])
        session.commit()
        rows = session.execute(
            build_list_stmt(activity="stale", stale_before=now - timedelta(days=30))
        ).scalars().all()
        # 最久没联系的排最前；5 天前联系过的、没有在途业务的、
        # 业务已结束的都不算
        assert [row.id for row in rows] == [4, 1, 2]
    engine.dispose()


def test_unknown_contact_filter_only_returns_history_rows_without_activity():
    """待补核只看「历史导入标记为未知」且确实没有任何活动时间的客户。

    已有报价/订单/回款的客户，`last_progress_at` 已经能证明有过业务往来，
    即使当年导入时没给联系日期，也不该再挂在待补核名单里。
    """
    engine = make_engine()
    now = datetime.now(UTC)
    with Session(engine) as session:
        session.add_all([
            Customer(id=1, name="历史导入无活动", owner_id=1,
                     last_contact_unknown=True),
            Customer(id=2, name="历史导入但有业务进展", owner_id=1,
                     last_contact_unknown=True,
                     last_progress_at=now - timedelta(days=10)),
            Customer(id=3, name="新客户未跟进", owner_id=1,
                     last_contact_unknown=False),
        ])
        session.commit()
        rows = session.execute(
            build_list_stmt(activity="unknown_contact")
        ).scalars().all()
        assert [row.id for row in rows] == [1]
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
