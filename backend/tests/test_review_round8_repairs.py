"""第八批：实际路由/服务 + 独立内存库，外部调用只用替身。"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.requests import Request

from conftest import SyncSessionAsAsync, make_engine, make_user
from app.core.audit import AuditLog
from app.core.errors import AppError
from app.modules.approval import router as approval
from app.modules.approval.access import approval_denial
from app.modules.approval.model import ApprovalDefinition, ApprovalInstance, ApprovalRecord
from app.modules.approval.schema import ApprovalTransfer
from app.modules.customer.model import Customer
from app.modules.erp import collection, reconcile
from app.modules.erp.adapter import ExternalPage
from app.modules.integration.model import (
    ExternalObjectMapping, ExternalRecord, ExternalSourceRegistry, ExternalSyncWatermark,
    FinancialAdjustment, IntegrationDiff, IntegrationLog, ReconciliationRun,
)
from app.modules.order.model import SalesOrder
from app.modules.pricing.model import PricePermission
from app.modules.quote.model import Quote, QuoteVersion
from app.modules.settings.model import SystemSetting
from app.modules.user.model import Permission, Role, User, role_permissions, user_roles

NOW = datetime(2026, 10, 10, tzinfo=UTC)
REQUEST = Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1)})
ERP_MODELS = (ExternalObjectMapping, ExternalRecord, ExternalSourceRegistry,
              ExternalSyncWatermark, FinancialAdjustment, IntegrationDiff,
              IntegrationLog, ReconciliationRun)


def engine_with(*models):
    engine = make_engine()
    for model in models:
        getattr(model, "__table__", model).create(engine, checkfirst=True)
    return engine


def fact(shop, kind, key, *, amount=None, quantity=None, order_key=None,
         parent_key=None, code=None, occurred=NOW, business_kind=None):
    return ExternalRecord(
        system_type="ERP", shop_id=shop, object_type=kind, dedupe_key=key,
        external_id=key, external_code=code, order_key=order_key, parent_key=parent_key,
        amount=amount, quantity=quantity, kind=business_kind, currency="CNY",
        raw_digest="probe", first_seen_at=NOW, last_seen_at=NOW, occurred_at=occurred,
    )


def mapping(key, oid, *, shop="A"):
    return ExternalObjectMapping(
        system_type="ERP", shop_id=shop, object_type="order", external_id=key,
        internal_id=oid, match_status="matched", first_seen_at=NOW, last_seen_at=NOW,
    )


async def run(raw, shop="*"):
    return await reconcile.run_reconciliation(
        SyncSessionAsAsync(raw), period_start=date(2026, 10, 1),
        period_end=date(2026, 11, 1), shop_id=shop, system_type="ERP", adapter=object(),
    )


@pytest.mark.anyio
async def test_transferred_cosign_rejects_sender_and_notification_uses_quote(monkeypatch):
    engine = engine_with(AuditLog, SystemSetting, Role, Permission, user_roles,
                         role_permissions, PricePermission, ApprovalDefinition,
                         ApprovalInstance, ApprovalRecord)
    with Session(engine, expire_on_commit=False) as raw:
        for uid in (2, 3):
            raw.add(User(id=uid, name=f"财务{uid}", username=f"u{uid}", password_hash="x"))
        raw.add(Role(id=1, code="finance", name="财务", data_scope="all"))
        for pid, code in ((1, "quote:view"), (2, "quote:approve")):
            raw.add(Permission(id=pid, code=code, name=code))
        raw.flush()
        raw.execute(user_roles.insert().values(user_id=3, role_id=1))
        raw.execute(role_permissions.insert(), [
            {"role_id": 1, "permission_id": 1}, {"role_id": 1, "permission_id": 2},
        ])
        raw.add(Customer(id=1, name="客户", owner_id=1))
        raw.add(Quote(id=1, quote_no="Q", customer_id=1, owner_id=1,
                      current_version_id=10, status="pending"))
        raw.add(QuoteVersion(id=10, quote_id=1, version_no=1, created_at=NOW,
                             approval_status="pending", currency="CNY", total_amount=100))
        raw.add(ApprovalDefinition(id=1, code="quote_low_price", name="低价", business_type="quote_version"))
        instance = ApprovalInstance(
            id=1, definition_id=1, business_type="quote_version", business_id=10,
            applicant_id=1, status="pending", current_node="co_sign",
            summary={"co_sign": {"status": "pending", "role_codes": ["finance"]}},
        )
        raw.add(instance)
        raw.commit()
        session = SyncSessionAsAsync(raw)
        sender = make_user(2, permissions=("quote:view", "quote:approve"), roles=("finance",), data_scope="all")
        recipient = make_user(3, permissions=("quote:view", "quote:approve"), roles=("finance",), data_scope="all")
        notify = AsyncMock()
        monkeypatch.setattr(approval.notification_service, "notify", notify)
        monkeypatch.setattr(approval.notification_service, "dispatch_pending", AsyncMock())
        await approval.transfer(1, ApprovalTransfer(to_user_id=3), REQUEST, sender, session)
        assert notify.await_args.kwargs["business_id"] == 1
        with pytest.raises(AppError, match="已转交"):
            await approval.approve(1, REQUEST, sender, session)
        await approval.approve(1, REQUEST, recipient, session)
        assert instance.status == "approved"


@pytest.mark.anyio
async def test_node_transition_discards_previous_assignee(monkeypatch):
    engine = engine_with(AuditLog, SystemSetting, Role, PricePermission,
                         ApprovalDefinition, ApprovalInstance, ApprovalRecord)
    with Session(engine, expire_on_commit=False) as raw:
        raw.add(Customer(id=1, name="客户", owner_id=1))
        raw.add(Quote(id=1, quote_no="Q", customer_id=1, owner_id=1, current_version_id=1))
        raw.add(QuoteVersion(id=1, quote_id=1, created_at=NOW, approval_status="pending", total_amount=100))
        raw.add(ApprovalDefinition(id=1, code="quote_low_price", name="低价", business_type="quote_version"))
        instance = ApprovalInstance(
            id=1, definition_id=1, business_type="quote_version", business_id=1,
            applicant_id=1, status="pending", current_node="manager",
            summary={"current_assignee_id": 2, "current_assignee_name": "主管",
                     "co_sign": {"status": "pending", "role_codes": ["finance"]}},
        )
        raw.add(instance)
        raw.commit()
        monkeypatch.setattr(approval.pricing_service, "resolve_min_margin", AsyncMock(return_value=(Decimal(".15"), True)))
        monkeypatch.setattr(approval, "_user_ids_by_roles", AsyncMock(return_value=[]))
        monkeypatch.setattr(approval.notification_service, "dispatch_pending", AsyncMock())
        await approval.approve(1, REQUEST, make_user(2, permissions=("quote:approve",), roles=("sales_manager",), data_scope="all"), SyncSessionAsAsync(raw))
        assert "current_assignee_id" not in instance.summary
        assert approval_denial(make_user(3, roles=("finance",)), instance, price_can_approve=False) is None


@pytest.mark.anyio
async def test_resolved_evidence_stays_fixed_new_value_opens_new_diff():
    engine = engine_with(*ERP_MODELS)
    with Session(engine, expire_on_commit=False) as raw:
        raw.add(Customer(id=1, name="客户"))
        raw.add(SalesOrder(id=1, order_no="L1", customer_id=1, total_amount=100, created_at=NOW))
        external = fact("A", "order", "O1", amount=Decimal(200), code="L1")
        raw.add(external)
        raw.commit()
        await run(raw)
        old = raw.scalars(select(IntegrationDiff)).one()
        await reconcile.confirm_diff(SyncSessionAsAsync(raw), old, resolution="keep_local", note="核实200", operator_id=1)
        raw.commit()
        old_value, old_evidence = dict(old.incoming_value), dict(old.evidence)
        external.amount = Decimal(300)
        raw.commit()
        await run(raw)
        rows = raw.scalars(select(IntegrationDiff).order_by(IntegrationDiff.id)).all()
        assert len(rows) == 2
        assert old.incoming_value == old_value and old.evidence == old_evidence
        assert rows[1].status == "open" and Decimal(rows[1].incoming_value["amount"]) == 300
        await run(raw)
        assert raw.query(IntegrationDiff).count() == 2


@pytest.mark.anyio
async def test_all_shops_preserve_orders_and_do_not_mix_quantities():
    engine = engine_with(*ERP_MODELS)
    with Session(engine, expire_on_commit=False) as raw:
        for shop, qty in (("A", 10), ("B", 20)):
            raw.add(fact(shop, "order", "O1", amount=qty * 10))
            raw.add(fact(shop, "order_item", "O1#1", quantity=qty, order_key="O1", code="S"))
        raw.commit()
        result = await run(raw)
        assert result["counters"]["missing_local"] == 2
        rows = raw.scalars(select(IntegrationDiff).where(IntegrationDiff.diff_type == "missing_local")).all()
        assert {d.shop_id: Decimal(d.incoming_value["ordered"]) for d in rows} == {"A": 10, "B": 20}


@pytest.mark.anyio
async def test_equal_head_numbers_in_different_object_types_do_not_mix():
    engine = engine_with(*ERP_MODELS)
    with Session(engine, expire_on_commit=False) as raw:
        raw.add_all([
            fact("A", "order", "O1"), fact("A", "order", "O2"),
            fact("A", "shipment", "H1", order_key="O1"),
            fact("A", "aftersale", "H1", order_key="O2"),
            fact("A", "shipment_item", "H1#1", parent_key="H1", quantity=10, code="S"),
            fact("A", "aftersale_item", "H1#1", parent_key="H1", quantity=2, code="S", business_kind="return"),
        ])
        raw.commit()
        facts = await reconcile.load_period_facts(SyncSessionAsAsync(raw), system_type="ERP", shop_id="*", start=NOW - timedelta(days=10), end=NOW + timedelta(days=10))
        aggregate = reconcile.build_order_aggregates(facts)
        assert aggregate[("ERP", "A", "O1")]["lines"]["S"]["shipped"] == 10
        assert aggregate[("ERP", "A", "O2")]["lines"]["S"]["returned"] == 2


@pytest.mark.anyio
async def test_missing_external_only_checks_current_period_and_correct_shop():
    engine = engine_with(*ERP_MODELS)
    with Session(engine, expire_on_commit=False) as raw:
        raw.add(Customer(id=1, name="客户"))
        for oid, created in ((1, datetime(2025, 1, 1, tzinfo=UTC)), (2, NOW)):
            raw.add(SalesOrder(id=oid, order_no=f"L{oid}", customer_id=1, total_amount=100, created_at=created))
            raw.add(mapping(f"O{oid}", oid, shop="B"))
        # A店同号不能证明B店订单已采到。
        raw.add(fact("A", "order", "O2", amount=100))
        raw.commit()
        result = await run(raw)
        assert result["counters"]["missing_external"] == 1
        diff = raw.scalars(select(IntegrationDiff).where(IntegrationDiff.diff_type == "missing_external")).one()
        assert diff.internal_id == 2 and diff.shop_id == "B"


@pytest.mark.anyio
@pytest.mark.parametrize("ambiguous", [False, True])
async def test_aftersale_preserves_unique_order_or_explicitly_leaves_ambiguous(ambiguous):
    engine = engine_with(*ERP_MODELS)
    with Session(engine, expire_on_commit=False) as raw:
        raw.add(Customer(id=1, name="客户"))
        for oid in (1, 2):
            raw.add(SalesOrder(id=oid, order_no=f"L{oid}", customer_id=1, total_amount=100, created_at=NOW))
            raw.add(mapping(f"O{oid}", oid))
            raw.add(fact("A", "order", f"O{oid}", amount=100, code=f"L{oid}"))
        raw.add(fact("A", "aftersale", "R1", order_key="O1", amount=20, business_kind="refund"))
        if ambiguous:
            raw.add(fact("A", "aftersale_item", "R1#1", order_key="O2", parent_key="R1", quantity=1))
        raw.commit()
        await run(raw)
        diff = raw.scalars(select(IntegrationDiff).where(IntegrationDiff.diff_type == "aftersale_needs_review")).one()
        result = await reconcile.confirm_diff(SyncSessionAsAsync(raw), diff, resolution="manual", note="核实退款20", operator_id=1)
        assert result["financial_adjustment"]["order_id"] == (None if ambiguous else 1)


@pytest.mark.anyio
async def test_expired_lease_old_request_cannot_overwrite_new_owner(monkeypatch):
    engine = engine_with(*ERP_MODELS)
    clock = [NOW]
    monkeypatch.setattr(collection, "_now", lambda: clock[0])

    class Newer:
        system_type = "ERP"
        label = "probe"

        async def fetch_records(self, **kwargs):
            return ExternalPage(items=[{"external_id": "O1", "amount": "200"}], watermark="200")

    class Older(Newer):
        async def fetch_records(self, **kwargs):
            clock[0] += timedelta(seconds=301)
            with Session(engine, expire_on_commit=False) as other:
                row = other.scalars(select(ExternalSyncWatermark)).one()
                # SQLite DateTime无时区；恢复生产PostgreSQL timestamptz表示。
                row.lease_expires_at = row.lease_expires_at.replace(tzinfo=UTC)
                await collection.collect_once(SyncSessionAsAsync(other), object_type="order", shop_id="A", adapter=Newer(), now=clock[0])
            return ExternalPage(items=[{"external_id": "O1", "amount": "100"}], watermark="100", has_more=True, next_page_token="old-page2")

    with Session(engine, expire_on_commit=False) as raw:
        with pytest.raises(AppError, match="租约已被"):
            await collection.collect_once(SyncSessionAsAsync(raw), object_type="order", shop_id="A", adapter=Older(), now=NOW)
        raw.expire_all()
        assert raw.scalars(select(ExternalRecord)).one().amount == 200
        cursor = raw.scalars(select(ExternalSyncWatermark)).one()
        assert cursor.watermark == "200" and cursor.page_token is None and cursor.status == "idle"


@pytest.mark.anyio
async def test_failed_page_rolls_back_all_facts_and_keeps_retry_point():
    engine = engine_with(*ERP_MODELS)

    class BadPage:
        system_type = "ERP"
        label = "probe"

        async def fetch_records(self, **kwargs):
            return ExternalPage(items=[{"external_id": "O1", "amount": "100"}, {"external_id": "O2", "amount": "bad"}], watermark="should-not-advance")

    with Session(engine, expire_on_commit=False) as raw:
        with pytest.raises(AppError):
            await collection.collect_once(SyncSessionAsAsync(raw), object_type="order", adapter=BadPage())
        assert raw.query(ExternalRecord).count() == 0
        cursor = raw.scalars(select(ExternalSyncWatermark)).one()
        assert cursor.status == "failed" and cursor.watermark is None and cursor.page_token is None
