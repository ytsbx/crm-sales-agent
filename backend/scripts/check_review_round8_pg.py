"""第八批 PostgreSQL 验证；只接受空的一次性测试库，不调用外部系统。"""

import asyncio
import sys
from pathlib import Path
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from starlette.requests import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _test_support import require_isolated_db

require_isolated_db()
import app.main
from app.core.audit import AuditLog
from app.core.base import Base
from app.core.database import SessionLocal, engine
from app.core.errors import AppError
from app.modules.approval import router as approval
from app.modules.approval.model import ApprovalDefinition, ApprovalInstance, ApprovalRecord
from app.modules.approval.schema import ApprovalTransfer
from app.modules.customer.model import Customer
from app.modules.erp import collection, reconcile
from app.modules.erp.adapter import ExternalPage
from app.modules.integration.model import (
    ExternalRecord, ExternalObjectMapping, ExternalSourceRegistry, ExternalSyncWatermark,
    FinancialAdjustment, IntegrationDiff, IntegrationLog, ReconciliationRun,
)
from app.modules.order.model import SalesOrder
from app.modules.pricing.model import PricePermission
from app.modules.quote.model import Quote, QuoteVersion
from app.modules.settings.model import SystemSetting
from app.modules.user.model import Permission, Role, User, role_permissions, user_roles

NOW = datetime(2026, 10, 10, tzinfo=UTC)


def fact(shop, kind, key, *, amount=None, quantity=None, order_key=None, code=None):
    return ExternalRecord(
        system_type="ERP", shop_id=shop, object_type=kind, dedupe_key=key,
        external_id=key, external_code=code, order_key=order_key, amount=amount,
        quantity=quantity, currency="CNY", raw_digest="probe", first_seen_at=NOW,
        last_seen_at=NOW, occurred_at=NOW,
    )


async def run(s, shop):
    return await reconcile.run_reconciliation(
        s, period_start=date(2026, 10, 1), period_end=date(2026, 11, 1),
        shop_id=shop, system_type="ERP", adapter=object(),
    )


async def main():
    models = (AuditLog, User, Role, Permission, PricePermission, SystemSetting,
              Customer, Quote, QuoteVersion, ApprovalDefinition, ApprovalInstance,
              ApprovalRecord, SalesOrder, ExternalRecord, ExternalObjectMapping,
              ExternalSourceRegistry, ExternalSyncWatermark, FinancialAdjustment,
              IntegrationDiff, IntegrationLog, ReconciliationRun)
    selected = {model.__table__ for model in models} | {user_roles, role_permissions}
    while True:
        deps = {fk.column.table for table in selected for fk in table.foreign_keys}
        if deps <= selected:
            break
        selected |= deps
    async with engine.begin() as connection:
        # 空库保护，避免给已有测试库或业务库混入本次夹具。
        from sqlalchemy import inspect
        assert not await connection.run_sync(lambda c: inspect(c).get_table_names()), "需要全新空测试库"
        await connection.run_sync(lambda c: Base.metadata.create_all(c, tables=list(selected)))

    async with SessionLocal() as s:
        s.add_all([User(id=i, username=f"round8-{i}", name=f"核验{i}", password_hash="fixture") for i in (1, 2, 3)])
        await s.flush()
        s.add(Customer(id=1, name="核验客户", owner_id=1))
        await s.commit()

    async with SessionLocal() as s:
        for shop, qty in (("A", 10), ("B", 20)):
            s.add(fact(shop, "order", "O1", amount=qty * 10))
            s.add(fact(shop, "order_item", "O1#1", quantity=qty, order_key="O1", code="S"))
        await s.commit()
        result = await run(s, "*")
        rows = (await s.scalars(select(IntegrationDiff).where(IntegrationDiff.diff_type == "missing_local"))).all()
        assert result["counters"]["missing_local"] == 2
        assert {row.shop_id: float(row.incoming_value["ordered"]) for row in rows} == {"A": 10, "B": 20}
        print("PASS PG: two shops keep separate quantities and differences")

    async with SessionLocal() as s:
        s.add(SalesOrder(id=1, order_no="L1", customer_id=1, owner_id=1, total_amount=100, created_at=NOW))
        external = fact("STATE", "order", "STATE-O1", amount=200, code="L1")
        s.add(external)
        await s.commit()
        await run(s, "STATE")
        old = (await s.scalars(select(IntegrationDiff).where(IntegrationDiff.diff_type == "amount_mismatch"))).one()
        old_id, original_value, original_evidence = old.id, dict(old.incoming_value), dict(old.evidence)
        await reconcile.confirm_diff(s, old, resolution="keep_local", note="核实200", operator_id=1)
        await s.commit()
        external.amount = 300
        await s.commit()
        await run(s, "STATE")
        await s.refresh(old)
        rows = (await s.scalars(select(IntegrationDiff).where(IntegrationDiff.diff_type == "amount_mismatch"))).all()
        assert len(rows) == 2 and old.incoming_value == original_value and old.evidence == original_evidence
        assert any(row.id != old_id and row.status == "open" and float(row.incoming_value["amount"]) == 300 for row in rows)
        await run(s, "STATE")
        assert len((await s.scalars(select(IntegrationDiff).where(IntegrationDiff.diff_type == "amount_mismatch"))).all()) == 2
        print("PASS PG: resolved evidence stays unchanged, changed amount opens one new difference")

    async with SessionLocal() as s:
        s.add(SalesOrder(id=2, order_no="OLD", customer_id=1, owner_id=1, status="completed", total_amount=100, created_at=datetime(2025, 1, 1, tzinfo=UTC)))
        s.add(ExternalObjectMapping(system_type="ERP", shop_id="PERIOD", object_type="order", external_id="OLD", internal_id=2, match_status="matched", first_seen_at=NOW, last_seen_at=NOW))
        await s.commit()
        assert (await run(s, "PERIOD"))["counters"]["missing_external"] == 0
        s.add(fact("STATE", "aftersale", "R1", amount=20, order_key="STATE-O1"))
        await s.commit()
        await run(s, "STATE")
        diff = (await s.scalars(select(IntegrationDiff).where(IntegrationDiff.diff_type == "aftersale_needs_review"))).one()
        result = await reconcile.confirm_diff(s, diff, resolution="manual", note="核实退款20", operator_id=1)
        await s.commit()
        assert result["financial_adjustment"]["order_id"] == 1
        print("PASS PG: historical orders excluded, refund preserves matched order")

    # 两个真实连接：B接管A过期租约并完成，A迟到的旧页必须被拒。
    clock = [NOW]
    class Newer:
        system_type = "ERP"
        label = "probe"
        async def fetch_records(self, **kwargs):
            return ExternalPage(items=[{"external_id": "LEASE-O1", "amount": "200"}], watermark="200")
    class Older(Newer):
        async def fetch_records(self, **kwargs):
            clock[0] += timedelta(seconds=301)
            async with SessionLocal() as other:
                await collection.collect_once(other, object_type="order", shop_id="LEASE", adapter=Newer(), now=clock[0])
            return ExternalPage(items=[{"external_id": "LEASE-O1", "amount": "100"}], watermark="100")
    with patch.object(collection, "_now", lambda: clock[0]):
        async with SessionLocal() as s:
            try:
                await collection.collect_once(s, object_type="order", shop_id="LEASE", adapter=Older(), now=NOW)
                raise AssertionError("旧租约未被拒绝")
            except AppError as exc:
                assert exc.http_status == 409
        async with SessionLocal() as s:
            row = (await s.scalars(select(ExternalRecord).where(ExternalRecord.shop_id == "LEASE"))).one()
            cursor = (await s.scalars(select(ExternalSyncWatermark).where(ExternalSyncWatermark.shop_id == "LEASE"))).one()
            assert row.amount == 200 and cursor.watermark == "200" and cursor.status == "idle"
    print("PASS PG: expired lease fencing prevents old facts, watermark and cleanup overwrites")

    # A已开始写一页时，B即使认为租约到期，也必须等待该页事实和断点一起提交。
    entered, release, newer_fetch = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original_upsert = collection._upsert_record
    async def pause_page(*args, **kwargs):
        result = await original_upsert(*args, **kwargs)
        if kwargs["batch_id"] == "LOCK-A":
            entered.set()
            await release.wait()
        return result
    class LockPage:
        system_type = "ERP"
        label = "probe"
        def __init__(self, amount):
            self.amount = amount
        async def fetch_records(self, **kwargs):
            if self.amount == 200:
                newer_fetch.set()
            return ExternalPage(items=[{"external_id": "LOCK-O1", "amount": str(self.amount)}], watermark=str(self.amount))
    async def collect_lock(amount, batch):
        async with SessionLocal() as s:
            return await collection.collect_once(s, object_type="order", shop_id="LOCK", adapter=LockPage(amount), now=clock[0], batch_id=batch)
    clock[0] = NOW
    with patch.object(collection, "_now", lambda: clock[0]), patch.object(collection, "_upsert_record", pause_page):
        task_a = asyncio.create_task(collect_lock(100, "LOCK-A"))
        await asyncio.wait_for(entered.wait(), 5)
        clock[0] += timedelta(seconds=301)
        task_b = asyncio.create_task(collect_lock(200, "LOCK-B"))
        try:
            await asyncio.sleep(.05)
            assert not newer_fetch.is_set(), "B不能在A页事务提交前获得租约并调外部"
        finally:
            release.set()
        results = await asyncio.wait_for(asyncio.gather(task_a, task_b, return_exceptions=True), 10)
        assert not isinstance(results[1], Exception), results[1]
        assert not isinstance(results[0], Exception) or isinstance(results[0], AppError), results[0]
    async with SessionLocal() as s:
        row = (await s.scalars(select(ExternalRecord).where(ExternalRecord.shop_id == "LOCK"))).one()
        cursor = (await s.scalars(select(ExternalSyncWatermark).where(ExternalSyncWatermark.shop_id == "LOCK"))).one()
        assert row.amount == 200 and cursor.watermark == "200" and cursor.status == "idle"
    print("PASS PG: competing claimant waits for atomic page transaction; final facts match watermark")

    class BadPage:
        system_type = "ERP"
        label = "probe"
        async def fetch_records(self, **kwargs):
            return ExternalPage(items=[{"external_id": "GOOD", "amount": "100"}, {"external_id": "BAD", "amount": "bad"}], watermark="invalid")
    async with SessionLocal() as s:
        try:
            await collection.collect_once(s, object_type="order", shop_id="FAILED-PAGE", adapter=BadPage())
            raise AssertionError("非法页未失败")
        except AppError:
            pass
        assert not (await s.scalars(select(ExternalRecord).where(ExternalRecord.shop_id == "FAILED-PAGE"))).all()
        cursor = (await s.scalars(select(ExternalSyncWatermark).where(ExternalSyncWatermark.shop_id == "FAILED-PAGE"))).one()
        assert cursor.status == "failed" and cursor.watermark is None
    print("PASS PG: failed page rolls back facts and saves resumable failure")

    async with SessionLocal() as s:
        s.add(Role(id=1, code="finance", name="财务", data_scope="all"))
        for pid, code in ((1, "quote:view"), (2, "quote:approve")):
            s.add(Permission(id=pid, code=code, name=code))
        await s.flush()
        await s.execute(user_roles.insert().values(user_id=3, role_id=1))
        await s.execute(role_permissions.insert(), [{"role_id": 1, "permission_id": pid} for pid in (1, 2)])
        quote = Quote(id=1, quote_no="Q8", customer_id=1, owner_id=1)
        s.add(quote)
        await s.flush()
        s.add(QuoteVersion(id=10, quote_id=1, version_no=1, created_at=NOW, total_amount=100, approval_status="pending"))
        await s.flush()
        quote.current_version_id = 10
        s.add(ApprovalDefinition(id=1, code="quote_low_price", name="低价", business_type="quote_version"))
        await s.flush()
        s.add(ApprovalInstance(id=1, definition_id=1, business_type="quote_version", business_id=10, applicant_id=1, status="pending", current_node="co_sign", summary={"co_sign": {"status": "pending", "role_codes": ["finance"]}}))
        await s.commit()
    actor = lambda uid: SimpleNamespace(id=uid, name=f"财务{uid}", data_scope="all", department_id=None, roles=["finance"], has=lambda code: code in {"quote:view", "quote:approve"})
    request = Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1)})
    notify = AsyncMock()
    with patch.object(approval.notification_service, "notify", notify), patch.object(approval.notification_service, "dispatch_pending", AsyncMock()):
        async with SessionLocal() as s:
            await approval.transfer(1, ApprovalTransfer(to_user_id=3), request, actor(2), s)
            assert notify.await_args.kwargs["business_id"] == 1
            try:
                await approval.approve(1, request, actor(2), s)
                raise AssertionError("原处理人仍能批准")
            except AppError as exc:
                assert exc.http_status == 403
            await s.rollback()
        async with SessionLocal() as s:
            await approval.approve(1, request, actor(3), s)
    print("PASS PG: transferred cosign denies sender, allows recipient; notice links quote")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
