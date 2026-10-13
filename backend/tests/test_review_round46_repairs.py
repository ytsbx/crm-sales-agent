"""第四批最后两项、第六批五项：实际服务/路由 + 独立内存库。"""

from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from starlette.requests import Request

from conftest import SyncSessionAsAsync, make_engine
from app.core.audit import AuditLog
from app.core.errors import AppError
from app.core.timebase import today_business
from app.modules.analytics import router, service, target_actuals, target_bases
from app.modules.analytics.model import ActualSnapshot, ActualSnapshotItem, BasisSnapshot, SalesTarget
from app.modules.approval.rules_engine import evaluate_conditions, payment_terms_days
from app.modules.customer.model import Customer
from app.modules.order.model import SalesOrder
from app.modules.payment.model import PaymentRecord, ReceivablePlan
from app.modules.payment.service import recalc_plan
from app.modules.pricing import router as pricing
from app.modules.pricing.model import CustomerPriceRule, PricePermission, PriceRule, ProductCost
from app.modules.pricing.schema import PricingRequest
from app.modules.product.model import Product, Sku
from app.modules.settings.model import SystemSetting
from app.modules.user.model import Role


D = Decimal
USER = SimpleNamespace(id=1, data_scope="all", department_id=None, roles=[], has=lambda _: True)
REQUEST = Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1)})


class Adapter(SyncSessionAsAsync):
    async def rollback(self):
        self._session.rollback()

    async def delete(self, obj):
        self._session.delete(obj)

    def get_bind(self):
        return self._session.get_bind()


def engine_with(*models):
    engine = make_engine()
    for model in models:
        model.__table__.create(engine, checkfirst=True)
    return engine


@pytest.mark.parametrize("text", [
    "账期6周", "账期1季度", "账期0.5年", "账期1.5个月",
    "款到发货，余款账期另议", "月结30，尾款验收后账期另议",
    "账期6周或30天后付款",
    "款到发货，余款半年后付清", "月结30，余款按合同约定",
    "款到发货，月结",
])
def test_unknown_credit_terms_never_auto_pass(text):
    days = payment_terms_days(text)
    assert days is None
    hit, _ = evaluate_conditions(
        [{"field": "payment_terms_days", "op": "lte", "value": 30}],
        {"payment_terms_days": days},
    )
    assert not hit


@pytest.mark.parametrize("text", ["账期60天，月结", "月结，账期60天"])
def test_monthly_description_keeps_explicit_credit_days(text):
    assert payment_terms_days(text) == 60


@pytest.mark.anyio
async def test_cny_normalization_reaches_single_and_batch_calculation():
    engine = engine_with(Product, Sku, ProductCost, PriceRule, CustomerPriceRule,
                         PricePermission, SystemSetting, Role)
    with Session(engine, expire_on_commit=False) as raw:
        raw.add(Product(id=1, name="产品"))
        raw.add(Sku(id=1, product_id=1, sku_code="CNY"))
        raw.add(ProductCost(sku_id=1, purchase_cost=D(70), production_cost=D(0),
                            package_cost=D(0), processing_cost=D(0), currency="CNY",
                            effective_from=date(2020, 1, 1)))
        raw.commit()
        payload = PricingRequest(sku_id=1, quantity=D(1), logistics_cost=D(0),
                                 currency=" CNY ", exchange_rate=D(7))
        session = Adapter(raw)
        single = (await pricing.calculate(payload, USER, session))["data"]
        batch = (await pricing.batch_calculate([payload], USER, session))["data"][0]
        for result in (single, batch):
            assert result["currency"] == "CNY"
            assert result["recommended_price"] == 100
            assert result["cost_in_quote_currency"] == 70
        payload.currency = "USD"
        with pytest.raises(AppError):
            await pricing.calculate(payload, USER, session)
        with pytest.raises(AppError):
            await pricing.batch_calculate([payload], USER, session)


@pytest.mark.anyio
@pytest.mark.parametrize("interleave", [False, True])
async def test_annual_customer_sets_share_one_order_read(interleave):
    engine = engine_with(BasisSnapshot)
    with Session(engine) as seed:
        seed.add(Customer(id=1, name="客户", owner_id=1))
        for oid, year in ((1, 2024), (2, 2025)):
            seed.add(SalesOrder(id=oid, order_no=str(oid), customer_id=1, owner_id=1,
                                status="pending", total_amount=D(100),
                                created_at=datetime(year, 3, 1, tzinfo=UTC)))
        seed.commit()
    with Session(engine, expire_on_commit=False) as raw:
        class Interleaved(Adapter):
            facts_reads = 0

            async def execute(self, stmt, *args, **kwargs):
                result = await super().execute(stmt, *args, **kwargs)
                columns = list(getattr(stmt, "selected_columns", []))
                if len(columns) == 3 and "sales_orders" in str(stmt):
                    self.facts_reads += 1
                    captured = result.all()
                    if interleave:
                        with Session(engine) as other:
                            other.execute(update(SalesOrder).where(SalesOrder.id == 1)
                                          .values(status="cancelled"))
                            other.commit()

                    class Rows:
                        def all(self):
                            return captured

                    return Rows()
                return result

        session = Interleaved(raw)
        snapshot = await target_bases._store_basis(session, 2025)
        await session.commit()
        assert session.facts_reads == 1
        assert snapshot.veteran_customer_ids == [1]
        assert snapshot.first_deal_month == {}
        assert not set(map(str, snapshot.veteran_customer_ids)) & set(snapshot.first_deal_month)


@pytest.mark.anyio
@pytest.mark.parametrize("audit_failure", [False, True])
async def test_year_refreeze_and_audit_commit_together(monkeypatch, audit_failure):
    engine = engine_with(BasisSnapshot, AuditLog)
    with Session(engine, expire_on_commit=False) as raw:
        raw.add(BasisSnapshot(year=2025, veteran_customer_ids=[1], first_deal_month={},
                              first_deal_detail={}, metric_basis_version="old",
                              computed_at=datetime(2025, 12, 31, tzinfo=UTC)))
        raw.commit()
        if audit_failure:
            monkeypatch.setattr(router, "write_audit", AsyncMock(side_effect=RuntimeError("audit")))
            with pytest.raises(RuntimeError):
                await router.refreeze_sales_target_bases(REQUEST, year=2025, user=USER, session=Adapter(raw))
            raw.rollback()
        else:
            await router.refreeze_sales_target_bases(REQUEST, year=2025, user=USER, session=Adapter(raw))
        with Session(engine) as verify:
            snapshot = verify.get(BasisSnapshot, 2025)
            assert snapshot.veteran_customer_ids == ([1] if audit_failure else [])
            audits = verify.scalars(select(AuditLog)).all()
            assert len(audits) == (0 if audit_failure else 1)


@pytest.mark.anyio
@pytest.mark.parametrize("confirmed,amount,expected", [
    (False, 80, 100), (True, 80, 20), (True, 100, 0), (True, 120, 0),
])
async def test_overdue_balance_and_counts(confirmed, amount, expected):
    engine = make_engine()
    with Session(engine, expire_on_commit=False) as raw:
        today = today_business()
        raw.add(Customer(id=1, name="客户", owner_id=1))
        raw.add(SalesOrder(id=1, order_no="AGING", customer_id=1, owner_id=1,
                           status="pending", total_amount=D(100)))
        plan = ReceivablePlan(id=1, order_id=1, plan_name="尾款", due_date=today-timedelta(days=10),
                              amount=D(100), created_at=datetime.now(UTC))
        raw.add(plan)
        raw.add(PaymentRecord(order_id=1, receivable_plan_id=1, received_date=today,
                              received_amount=D(amount), status="confirmed" if confirmed else "pending",
                              confirmed_at=datetime.now(UTC) if confirmed else None,
                              created_at=datetime.now(UTC)))
        raw.commit()
        session = Adapter(raw)
        await recalc_plan(session, plan)
        await session.flush()
        aging = await service.payment_stats(session, USER)
        summary = await service.receivable_stats(session, USER)
        assert aging["overdue_amount"] == expected
        assert aging["overdue_node_count"] == summary["overdue_count"] == int(expected > 0)


@pytest.mark.anyio
@pytest.mark.parametrize("delta,accepted", [(-1, False), (-1000000, False), (0, True)])
async def test_target_version_full_precision(delta, accepted):
    for constraint in SalesTarget.__table__.constraints:
        if getattr(constraint, "name", None) == "ck_sales_targets_period":
            constraint.ddl_if(dialect="postgresql")
    engine = engine_with(SalesTarget, AuditLog)
    current = datetime(2026, 10, 12, 10, 0, 0, 200000, UTC)
    with Session(engine, expire_on_commit=False) as raw:
        raw.add(SalesTarget(id=1, period="2026-09", user_id=1, sales_target=D(200000),
                            new_customer_target=0, repeat_customer_target=D(0),
                            created_at=current, updated_at=current))
        raw.commit()
        seen = (current + timedelta(microseconds=delta)).astimezone(timezone(timedelta(hours=8)))
        payload = router.SalesTargetUpsert(period="2026-09", user_id=1, sales_target=D(100000),
                                          expected_updated_at=seen)
        if accepted:
            await router.upsert_sales_target(payload, REQUEST, USER, Adapter(raw))
        else:
            with pytest.raises(AppError) as error:
                await router.upsert_sales_target(payload, REQUEST, USER, Adapter(raw))
            assert error.value.http_status == 409
            raw.rollback()
        raw.expire_all()
        assert raw.get(SalesTarget, 1).sales_target == D(100000 if accepted else 200000)


@pytest.mark.anyio
async def test_month_refreeze_audit_uses_locked_previous_value(monkeypatch):
    engine = engine_with(ActualSnapshot, ActualSnapshotItem, AuditLog)
    with Session(engine) as seed:
        seed.add(ActualSnapshot(period="2026-09", scope_key="company", metric="received",
                                actual_value=D(100), metric_basis_version="old", frozen_at=datetime.now(UTC)))
        seed.commit()
    with Session(engine, expire_on_commit=False) as raw:
        @asynccontextmanager
        async def snapshot(period):
            # 并发重算在 A 的旧读之后完整提交，A 写入前必须以 200 为审计旧值。
            with Session(engine) as other:
                other.execute(update(ActualSnapshot).values(actual_value=D(200)))
                other.commit()
            yield object()

        monkeypatch.setattr(target_actuals, "consistent_snapshot", snapshot)
        monkeypatch.setattr(router, "_collect_period_actuals", AsyncMock(return_value={('company', 'received'): D(300)}))
        monkeypatch.setattr(router, "_collect_period_items", AsyncMock(return_value=[]))
        await router.refreeze_sales_actuals(REQUEST, period="2026-09", reason="重算",
                                          user=USER, session=Adapter(raw))
        with Session(engine) as verify:
            audit = verify.scalars(select(AuditLog)).one()
            assert D(audit.before_data["received_total"]) == D(200)
            assert D(audit.after_data["received_total"]) == D(300)
            assert verify.get(ActualSnapshot, ("2026-09", "company", "received")).actual_value == D(300)
