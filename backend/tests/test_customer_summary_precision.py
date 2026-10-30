"""客户聚合与确定性摘要不得把已有十进制金额变成浮点误差。"""

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from conftest import SyncSessionAsAsync, make_user
from app.modules.agent import insights
from app.modules.customer import service
from app.modules.customer.model import Customer
from app.modules.file.model import BusinessFile, FileRecord
from app.modules.order.model import SalesOrder


@pytest.fixture
def customer_db(db_session):
    for model in (BusinessFile, FileRecord):
        model.__table__.create(db_session.bind, checkfirst=True)
    customer = Customer(id=1, name="隔离金额客户", owner_id=101)
    db_session.add(customer)
    db_session.flush()
    return db_session, SyncSessionAsAsync(db_session)


@pytest.mark.anyio
async def test_overview_exact_amounts_keep_order_permission_and_scope(customer_db):
    raw, session = customer_db
    raw.add_all([
        SalesOrder(id=i, order_no=f"ISO-{i}", customer_id=1, owner_id=owner,
                   currency="CNY", total_amount=Decimal(amount))
        for i, owner, amount in [(1, 101, "1.01"), (2, 101, "2.02"), (3, 999, "100.00")]
    ])
    raw.flush()
    user = make_user(101, permissions=["customer:view", "order:view"])
    own = await service.customer_overview(session, user, 1)
    assert own["counts"]["orders"] == 2
    assert own["counts"]["order_amounts"][0]["amount_exact"] == "3.03"
    assert {r["total_amount_exact"] for r in own["orders"]} == {"1.01", "2.02"}
    all_scope = await service.customer_overview(
        session, make_user(101, permissions=["order:view"], data_scope="all"), 1,
    )
    assert all_scope["counts"]["order_amounts"][0]["amount_exact"] == "103.03"
    denied = await service.customer_overview(session, make_user(101), 1)
    assert denied["counts"]["order_amounts"] is None
    assert denied["orders"] == []


@pytest.mark.anyio
async def test_recent_order_keeps_large_decimal(customer_db):
    raw, session = customer_db
    # SQLite SUM 自身使用二进制，聚合大额精度另由真实PG接口验证。
    # 保留ORM引用，避免把SQLite重新加载Numeric的误差误认成序列化问题。
    order = SalesOrder(id=1, order_no="ISO-LARGE", customer_id=1, owner_id=101,
                       currency="CNY", total_amount=Decimal("99999999999999.99"))
    raw.add(order)
    raw.flush()
    data = await service.customer_overview(session, make_user(101, permissions=["order:view"]), 1)
    assert data["orders"][0]["total_amount_exact"] == "99999999999999.99"


@pytest.mark.anyio
@pytest.mark.parametrize("amount,exact", [
    ("99999999999999.99", True), ("199999999999999.98", True),
    ("1.01", True), ("0.00", True), ("1.01", False), ("0.00", False),
])
async def test_summary_formats_exact_aggregate(customer_db, monkeypatch, amount, exact):
    _, session = customer_db
    facts = {
        "counts": {"open_opportunities": None, "order_amounts": [
            {"currency": "CNY", "amount": float(amount)},
        ]},
        "restricted": [], "opportunities": [], "quotes": [], "orders": [], "followups": [],
    }
    if exact:
        facts["counts"]["order_amounts"][0]["amount_exact"] = amount
    monkeypatch.setattr(service, "customer_overview", AsyncMock(return_value=facts))
    result = await insights.customer_summary(session, make_user(101), customer_id=1)
    assert f"累计成交 {amount} CNY" in result["insights"]
