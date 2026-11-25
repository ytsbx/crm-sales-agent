"""结账Numeric(16,2)边界：明确拒绝且在任何存档改写之前退出。"""
import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import CheckConstraint, text
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.analytics import target_actuals, target_bases, targets
from app.modules.analytics.model import ActualSnapshot, ActualSnapshotItem, SalesTarget
from app.modules.order.model import OrderShipmentBatch, OrderShipmentBatchItem, SalesOrder, SalesOrderItem
from app.modules.payment.model import PaymentRecord
from app.modules.product.model import Product, Sku
from conftest import SyncSessionAsAsync, make_engine, make_user


@pytest.mark.parametrize("amount", ["0", "3.03", "99999999999999.99", "-99999999999999.99", "99999999999999.994", "-99999999999999.994"])
def test_representable_amounts_keep_postgres_rounding(amount):
    target_actuals._check_snapshot_amount("2025-01", "sales", "汇总", Decimal(amount))


@pytest.mark.parametrize("amount", ["99999999999999.995", "-99999999999999.995", "100000000000000", "NaN", "Infinity"])
def test_overflow_has_clear_period_and_metric(amount):
    with pytest.raises(AppError) as error:
        target_actuals._check_snapshot_amount("2025-01", "sales", "汇总", Decimal(amount))
    assert error.value.http_status == 422
    assert "2025-01" in error.value.message and "签单额汇总金额超出可保存范围" in error.value.message


@pytest.mark.parametrize("replace, bad_detail", [(False, False), (False, True), (True, False), (True, True)])
def test_whole_batch_is_checked_before_any_snapshot_mutation(monkeypatch, replace, bad_detail):
    async def scenario():
        session = AsyncMock()
        monkeypatch.setattr(target_actuals, "lock_period", AsyncMock())
        existing = {("company", "sales"): Decimal("8.88")} if replace else {}
        monkeypatch.setattr(target_actuals, "frozen_for", AsyncMock(return_value=existing))
        values = {("user:1", "sales"): Decimal("1.01"), ("company", "sales"): Decimal("100000000000000") if not bad_detail else Decimal("1.01")}
        items = [{"metric": "signed", "amount": Decimal("1.01")}, {"metric": "signed", "amount": Decimal("100000000000000")}] if bad_detail else []
        with pytest.raises(AppError) as error:
            await target_actuals.freeze_period(session, period="2025-01", values=values,
                                              basis_version="test", operator_id=1,
                                              replace=replace, note="隔离重算原因" if replace else None, items=items)
        assert error.value.http_status == 422
        assert "明细" in error.value.message if bad_detail else "汇总" in error.value.message
        session.get.assert_not_called()
        session.add.assert_not_called()
        session.delete.assert_not_called()
        session.execute.assert_not_called()
        session.flush.assert_not_called()
    asyncio.run(scenario())


@pytest.mark.anyio
@pytest.mark.parametrize("archived,with_target", [
    ({"sales": "400"}, True),
    ({"received": "0"}, True),
    ({"received": "0"}, False),
    ({"sales": "400", "received": "0", "shipped": "0", "new_customer": "0", "repeat_net": "0"}, True),
    ({}, True),
    ({"received": "80000000000000.03", "repeat_net": "80000000000000.03"}, True),
])
async def test_summary_and_drilldown_agree_on_individual_archive_metrics(monkeypatch, archived, with_target):
    engine = make_engine()
    for model in (ActualSnapshot, ActualSnapshotItem, Product, Sku, SalesOrderItem, OrderShipmentBatch, OrderShipmentBatchItem):
        model.__table__.create(engine, checkfirst=True)
    # SQLite uses a function for regular expressions; the application uses PostgreSQL's infix operator.
    for constraint in SalesTarget.__table__.constraints:
        if isinstance(constraint, CheckConstraint) and constraint.name == "ck_sales_targets_period":
            monkeypatch.setattr(constraint, "sqltext", text("length(period) = 7"))
    SalesTarget.__table__.create(engine)
    monkeypatch.setattr(target_bases, "new_customer_rows", AsyncMock(return_value=[]))
    monkeypatch.setattr(target_bases, "repeat_net_by_owner", AsyncMock(return_value={}))
    at = datetime(2025, 5, 10, tzinfo=UTC)
    with Session(engine) as db:
        db.connection().connection.create_function("timezone", 2, lambda _tz, value: value)
        large_value = "repeat_net" in archived and archived["repeat_net"] != "0"
        if with_target:
            target = Decimal("80000000000000.02" if large_value else "500")
            db.add(SalesTarget(period="2025-05", sales_target=target, repeat_customer_target=target))
        db.add(SalesOrder(id=1, customer_id=1, owner_id=1, order_no="snapshot-test",
                          total_amount=Decimal("600"), status="pending", created_at=at))
        db.add(PaymentRecord(order_id=1, received_date=date(2025, 5, 10), received_amount=Decimal("120"),
                             status="confirmed", confirmed_at=at, created_at=at))
        db.add_all(ActualSnapshot(period="2025-05", scope_key="company", metric=metric,
                                  actual_value=Decimal(value), metric_basis_version="test")
                   for metric, value in archived.items())
        if "sales" in archived:
            db.add(ActualSnapshotItem(period="2025-05", metric="signed", record_type="order",
                                      record_id=1, owner_id=1, amount=Decimal(archived["sales"]),
                                      metric_basis_version="test"))
        if large_value:
            db.add(ActualSnapshotItem(period="2025-05", metric="received", record_type="payment",
                                      record_id=1, owner_id=1, amount=Decimal(archived["received"]),
                                      metric_basis_version="test"))
        db.commit()
        session = SyncSessionAsAsync(db)
        user = make_user(1, data_scope="all")
        report = await targets.targets_with_actuals(session, user, 2025)
        row = next(r for r in report["rows"] if r["period"] == "2025-05" and r["user_id"] is None)
        assert row["period_frozen"] is bool(archived)
        assert row["actual_frozen"] is (len(archived) == 5)
        assert set(row["live_actual_metrics"]) == (
            {"sales", "received", "shipped", "new_customer", "repeat_net"} - archived.keys() if archived else set()
        )
        if large_value:
            for key in ("sales_variance", "repeat_customer_variance"):
                assert Decimal(str(row[key])) == Decimal(row[f"{key}_exact"]) == Decimal("0.01")
        for metric, row_field, snapshot_key, live_value in (
            ("received", "received_actual_exact", "received", "120"),
            ("signed", "sales_actual_exact", "sales", "600"),
        ):
            detail = await targets.drilldown(session, user, period="2025-05", metric=metric)
            assert Decimal(row[row_field]) == Decimal(detail["total_exact"]) == Decimal(archived.get(snapshot_key, live_value))
            assert detail["source"] == ("snapshot" if snapshot_key in archived else "live")
            assert detail["archive_missing_metric"] is (bool(archived) and snapshot_key not in archived)
    engine.dispose()
