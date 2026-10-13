import asyncio
from datetime import UTC, datetime, date, timedelta
from decimal import Decimal as D
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock
from contextlib import asynccontextmanager
import sys

sys.path.insert(0, 'scripts')
from _test_support import require_isolated_db
require_isolated_db()
import app.main
from app.core.base import Base
from app.core.database import engine, SessionLocal
from app.core.audit import AuditLog
from app.core.errors import AppError
from app.core.timebase import today_business
from app.modules.analytics import target_bases, service, router, target_actuals
from app.modules.analytics.model import BasisSnapshot, SalesTarget, ActualSnapshot, ActualSnapshotItem
from app.modules.customer.model import Customer
from app.modules.order.model import SalesOrder
from app.modules.payment.model import ReceivablePlan, PaymentRecord
from app.modules.product.model import Product, Sku
from app.modules.pricing.model import ProductCost, PriceRule, PricePermission, CustomerPriceRule
from app.modules.pricing import router as prices
from app.modules.pricing.schema import PricingRequest
from app.modules.settings.model import SystemSetting
from app.modules.user.model import User, Role
from sqlalchemy import select, update
from starlette.requests import Request


async def main():
    selected = {m.__table__ for m in [User, Role, Customer, SalesOrder, ReceivablePlan,
        PaymentRecord, Product, Sku, ProductCost, PriceRule, PricePermission, CustomerPriceRule,
        SystemSetting, AuditLog, BasisSnapshot, SalesTarget, ActualSnapshot, ActualSnapshotItem]}
    while True:
        deps = {fk.column.table for table in selected for fk in table.foreign_keys}
        if deps <= selected:
            break
        selected |= deps
    async with engine.begin() as c:
        await c.run_sync(lambda sync: Base.metadata.create_all(sync, tables=list(selected)))
    now = datetime.now(UTC)
    version = now.replace(microsecond=200000)
    today = today_business()
    user = SimpleNamespace(id=1, data_scope='all', department_id=None, roles=[], has=lambda _: True)
    request = Request({'type': 'http', 'headers': [], 'client': ('127.0.0.1', 1)})
    async with SessionLocal() as s:
        s.add(User(id=1, username='round46', name='核验', password_hash='fixture'))
        await s.flush()
        s.add(Customer(id=1, name='核验客户', owner_id=1))
        s.add(Product(id=1, name='核验产品'))
        await s.flush()
        s.add(Sku(id=1, product_id=1, sku_code='ROUND46'))
        await s.flush()
        s.add(ProductCost(sku_id=1, purchase_cost=D(70), production_cost=D(0), package_cost=D(0),
                          processing_cost=D(0), effective_from=date(2020, 1, 1)))
        for oid, year in [(1, 2024), (2, 2025)]:
            s.add(SalesOrder(id=oid, order_no=str(oid), customer_id=1, owner_id=1, status='pending',
                            total_amount=D(100), created_at=datetime(year, 3, 1, tzinfo=UTC)))
        s.add(SalesTarget(period='2026-09', user_id=1, sales_target=D(200000), new_customer_target=0,
                          repeat_customer_target=D(0), created_at=version, updated_at=version))
        s.add(BasisSnapshot(year=2025, veteran_customer_ids=[1], first_deal_month={}, first_deal_detail={},
                            metric_basis_version='old', computed_at=now))
        s.add(ActualSnapshot(period='2026-09', scope_key='company', metric='received', actual_value=D(100),
                             metric_basis_version='old', frozen_at=now))
        await s.flush()
        s.add(ReceivablePlan(id=1, order_id=2, plan_name='尾款', due_date=today-timedelta(days=10),
                             amount=D(100), status='partial', created_at=now))
        await s.flush()
        s.add(PaymentRecord(order_id=2, receivable_plan_id=1, received_date=today, received_amount=D(80),
                            status='confirmed', confirmed_at=now, created_at=now))
        await s.commit()
    async with SessionLocal() as s:
        result = (await prices.calculate(PricingRequest(sku_id=1, currency=' CNY ', exchange_rate=D(7),
                                                        logistics_cost=D(0)), user, s))['data']
        assert result['currency'] == 'CNY' and result['recommended_price'] == 100
        old, new, _ = await target_bases._build_basis(s, 2025)
        assert old == [1] and new == {}
        a = await service.payment_stats(s, user)
        b = await service.receivable_stats(s, user)
        assert a['overdue_amount'] == 20 and a['overdue_node_count'] == b['overdue_count'] == 1
        print('PASS PostgreSQL: 人民币规范化、首单窗口归类、逾期余额与计数')
    async with SessionLocal() as s:
        payload = router.SalesTargetUpsert(period='2026-09', user_id=1, sales_target=D(100000),
                                           expected_updated_at=version-timedelta(microseconds=1))
        try:
            await router.upsert_sales_target(payload, request, user, s)
        except AppError as e:
            assert e.http_status == 409
            await s.rollback()
        else:
            raise AssertionError('旧版本被接受')
        assert (await s.execute(select(SalesTarget.sales_target))).scalar_one() == D(200000)
        print('PASS PostgreSQL: 同秒差1微秒旧目标409，金额未被覆盖')
    async with SessionLocal() as s:
        await s.execute(update(SalesOrder).where(SalesOrder.id == 1).values(status='cancelled'))
        await s.commit()
        with patch.object(router, 'write_audit', new=AsyncMock(side_effect=RuntimeError('模拟审计失败'))):
            try:
                await router.refreeze_sales_target_bases(request, year=2025, user=user, session=s)
            except RuntimeError:
                await s.rollback()
            else:
                raise AssertionError('审计没有失败')
    async with SessionLocal() as s:
        assert (await s.get(BasisSnapshot, 2025)).veteran_customer_ids == [1]
        await router.refreeze_sales_target_bases(request, year=2025, user=user, session=s)
    async with SessionLocal() as s:
        assert (await s.get(BasisSnapshot, 2025)).veteran_customer_ids == []
        assert (await s.execute(select(AuditLog.id).where(AuditLog.business_type == 'analytics_basis'))).scalars().all()
        print('PASS PostgreSQL: 年度基准审计失败回滚，成功时共同提交')
    original = target_actuals.consistent_snapshot

    @asynccontextmanager
    async def interleaved(period):
        async with original(period) as snap:
            yield snap
        async with SessionLocal() as other:
            await target_actuals.freeze_period(other, period=period, values={('company', 'received'): D(200)},
                                                basis_version='B', operator_id=1, replace=True, note='B', items=[])
            await other.commit()

    async with SessionLocal() as s:
        with patch.object(target_actuals, 'consistent_snapshot', interleaved), patch.object(
                router, '_collect_period_actuals', new=AsyncMock(return_value={('company', 'received'): D(300)})), patch.object(
                router, '_collect_period_items', new=AsyncMock(return_value=[])):
            await router.refreeze_sales_actuals(request, period='2026-09', reason='A', user=user, session=s)
    async with SessionLocal() as s:
        audit = (await s.execute(select(AuditLog).where(AuditLog.action == 'refreeze_actuals'))).scalar_one()
        assert D(audit.before_data['received_total']) == 200 and D(audit.after_data['received_total']) == 300
        print('PASS PostgreSQL: 月度并发交错审计200→300')
    await engine.dispose()


asyncio.run(main())
