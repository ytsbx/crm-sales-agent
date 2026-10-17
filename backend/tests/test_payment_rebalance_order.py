"""自动分配重算必须遵守先到账先冲抵，不能让后笔旧分配抢占前笔额度。"""
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from app.modules.customer.model import Customer
from app.modules.order.model import SalesOrder
from app.modules.payment import service
from app.modules.payment.model import PaymentAllocation, PaymentRecord, ReceivablePlan
from tests.conftest import SyncSessionAsAsync


@pytest.fixture
def rebalance_db(db_session):
    now = datetime.now(UTC)
    db_session.add(Customer(id=1, name="隔离客户"))
    db_session.flush()
    db_session.add(SalesOrder(id=1, order_no="REBALANCE-TEST", customer_id=1, total_amount=200))
    db_session.flush()
    for i in (1, 2):
        db_session.add(ReceivablePlan(id=i, order_id=1, plan_name=f"第{i}期", amount=100,
                                      due_date=date(2026, 12, i), created_at=now))
        db_session.add(PaymentRecord(id=i, order_id=1, receivable_plan_id=i, received_amount=100,
                                     received_date=date(2026, 10, i), created_at=now,
                                     allocation_mode="auto", status="confirmed"))
    db_session.flush()
    return db_session


async def initialize(db):
    session = SyncSessionAsAsync(db)
    for i in (1, 2):
        await service.allocate_payment(session, db.get(PaymentRecord, i), operator_id=None)
    return session


def allocations(db):
    return {(row.payment_id, row.plan_id): row.amount for row in db.scalars(select(PaymentAllocation))}


@pytest.mark.anyio
async def test_reducing_receivable_leaves_excess_on_later_receipt(rebalance_db):
    db = rebalance_db
    session = await initialize(db)
    db.get(ReceivablePlan, 1).amount = Decimal(50)
    db.flush()
    await service.rebalance_order_allocations(session, 1)
    assert allocations(db) == {(1, 1): Decimal(50), (1, 2): Decimal(50), (2, 2): Decimal(50)}
    totals = await service.allocation_totals(session, [1])
    assert totals[1]["received_amount"] == 200
    assert totals[1]["allocated_amount"] == 150
    assert totals[1]["unallocated_amount"] == 50
    await service.rebalance_order_allocations(session, 1)
    assert allocations(db) == {(1, 1): Decimal(50), (1, 2): Decimal(50), (2, 2): Decimal(50)}


@pytest.mark.anyio
async def test_changing_due_order_respects_receipt_priority(rebalance_db):
    db = rebalance_db
    session = await initialize(db)
    db.get(ReceivablePlan, 2).due_date = date(2026, 11, 1)
    db.flush()
    await service.rebalance_order_allocations(session, 1)
    assert allocations(db) == {(1, 2): Decimal(100), (2, 1): Decimal(100)}


@pytest.mark.anyio
@pytest.mark.parametrize("mode,source", [("manual", "auto"), ("legacy", "manual")])
async def test_complete_manual_decision_keeps_priority_and_original_rows(rebalance_db, mode, source):
    db = rebalance_db
    session = await initialize(db)
    protected = db.scalars(select(PaymentAllocation).where(PaymentAllocation.payment_id == 2)).one()
    protected.source = source
    protected_id = protected.id
    db.get(PaymentRecord, 2).allocation_mode = mode
    db.get(ReceivablePlan, 1).amount = Decimal(50)
    db.flush()
    await service.rebalance_order_allocations(session, 1)
    assert allocations(db) == {(1, 1): Decimal(50), (2, 2): Decimal(100)}
    assert db.get(PaymentAllocation, protected_id).payment_id == 2
    assert db.get(PaymentRecord, 2).allocation_mode == "manual"


@pytest.mark.anyio
async def test_empty_manual_decision_remains_unallocated(rebalance_db):
    db = rebalance_db
    session = await initialize(db)
    db.execute(delete(PaymentAllocation).where(PaymentAllocation.payment_id == 2))
    db.get(PaymentRecord, 2).allocation_mode = "manual"
    db.get(ReceivablePlan, 1).amount = Decimal(50)
    db.flush()
    await service.rebalance_order_allocations(session, 1)
    assert allocations(db) == {(1, 1): Decimal(50), (1, 2): Decimal(50)}
    assert db.get(PaymentRecord, 2).allocation_mode == "manual"


@pytest.mark.anyio
async def test_legacy_implicit_allocation_does_not_occupy_earlier_receipt_capacity(rebalance_db):
    db = rebalance_db
    for i in (1, 2):
        db.get(PaymentRecord, i).allocation_mode = "legacy"
    db.get(ReceivablePlan, 2).due_date = date(2026, 11, 1)
    db.flush()
    await service.rebalance_order_allocations(SyncSessionAsAsync(db), 1)
    assert allocations(db) == {(1, 2): Decimal(100), (2, 1): Decimal(100)}


@pytest.mark.anyio
async def test_failed_rebalance_rolls_back_original_allocations(rebalance_db, monkeypatch):
    db = rebalance_db
    session = await initialize(db)
    db.commit()
    original = allocations(db)

    async def interrupted(*args, **kwargs):
        raise RuntimeError("isolated failure after automatic allocations were released")

    monkeypatch.setattr(service, "allocate_payment", interrupted)
    with pytest.raises(RuntimeError):
        await service.rebalance_order_allocations(session, 1)
    await session.rollback()
    assert allocations(db) == original
