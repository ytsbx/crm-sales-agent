import asyncio
from datetime import datetime, UTC, timedelta
from decimal import Decimal as D
from uuid import uuid4
from unittest.mock import patch
from sqlalchemy import select, delete
from scripts._test_support import require_api_base, require_isolated_db
require_api_base()
require_isolated_db()
import app.main
from app.core.database import SessionLocal, engine
from app.core.timebase import today_business
from app.modules.customer.model import Customer
from app.modules.product.model import Product, Sku
from app.modules.pricing.model import ProductCost, PriceRule, PricePermission
from app.modules.user.model import Role, User
from app.modules.quote.model import Quote, QuoteVersion, QuoteItem, QuoteCharge
from app.modules.quote import service as qs
from app.modules.pricing import service as ps
from app.modules.order import service as orders
from app.modules.order.model import SalesOrderItem
from app.modules.integration.model import IntegrationDiff
from app.modules.erp import reconcile

TAG = 'AUDIT_' + uuid4().hex[:10]

async def fixture(session, *, cost=D('10')):
    user = (await session.execute(select(User).where(User.username == 'admin'))).scalar_one()
    role = Role(code=TAG + str(uuid4())[:8], name='Audit discount role', data_scope='all')
    product = Product(name=TAG)
    customer = Customer(name=TAG, owner_id=user.id)
    session.add_all([role, product, customer])
    await session.flush()
    sku = Sku(product_id=product.id, sku_code=TAG + str(uuid4())[:8], name=TAG, unit='件')
    session.add(sku)
    await session.flush()
    session.add_all([
        PricePermission(role_id=role.id, minimum_margin=D('.15'), discount_limit=D('.3'), can_approve=False),
        ProductCost(sku_id=sku.id, purchase_cost=cost, production_cost=D(0), package_cost=D(0), processing_cost=D(0), effective_from=today_business()),
        PriceRule(sku_id=sku.id, standard_price=D(100), guide_price=D(100), minimum_price=D(0)),
    ])
    quote = Quote(quote_no=TAG + str(uuid4())[:8], customer_id=customer.id, owner_id=user.id, valid_until=today_business()+timedelta(days=30))
    session.add(quote)
    await session.flush()
    version = QuoteVersion(quote_id=quote.id, currency='CNY', created_at=datetime.now(UTC))
    session.add(version)
    await session.flush()
    quote.current_version_id = version.id
    return user, role, sku, customer, quote, version

async def snapshot(session, role, sku, customer, version, price):
    return await qs.build_item_snapshot(session, version=version, sku_id=sku.id, quantity=D(1), customer_id=customer.id,
        quoted_price=price, logistics_cost=D(0), opportunity_item_id=None, spec_snapshot=None, remark=None, role_codes=[role.code])

async def main():
    async with SessionLocal() as s:
        user, role, sku, customer, quote, version = await fixture(s)
        item = await snapshot(s, role, sku, customer, version, D(50))
        s.add(item)
        await s.flush()
        await qs.recalc_version(s, version)
        before = item.approval_required
        instance, required = await qs.submit_for_approval(s, quote=quote, version=version, applicant_id=user.id, user_roles=[role.code])
        print('DISCOUNT_BYPASS', {'discount': '50%', 'limit': '30%', 'before': before, 'after': item.approval_required, 'required': required, 'status': version.approval_status})
        await s.rollback()

    async with SessionLocal() as s:
        user, role, sku, customer, quote, version = await fixture(s, cost=D(100))
        item = await snapshot(s, role, sku, customer, version, D(200))
        s.add_all([item, QuoteCharge(quote_version_id=version.id, charge_type='logistics', amount=D(200), is_discount=False, logistics_confirmed_at=datetime.now(UTC)),
                   QuoteCharge(quote_version_id=version.id, charge_type='other', amount=D(-150), is_discount=True)])
        await s.flush()
        await qs.recalc_version(s, version)
        original = qs.settings_service.get_text
        async def hard_on(session, key, field, default=''):
            return 'cost' if key == 'hard_floor' and field == 'mode' else await original(session, key, field, default)
        with patch.object(qs.settings_service, 'get_text', hard_on):
            instance, required = await qs.submit_for_approval(s, quote=quote, version=version, applicant_id=user.id, user_roles=[role.code])
        print('HARD_FLOOR_BYPASS', {'cost': '100', 'net_goods_revenue': str(version.total_amount-version.logistics_amount), 'total': str(version.total_amount), 'approval_created': instance is not None, 'status': version.approval_status})
        await s.rollback()

    async with SessionLocal() as s:
        user, role, sku, customer, quote, version = await fixture(s)
        await s.execute(delete(ProductCost).where(ProductCost.sku_id == sku.id))
        await s.execute(delete(PriceRule).where(PriceRule.sku_id == sku.id))
        try:
            await ps.calculate_price(s, sku_id=sku.id, quantity=D(1), quoted_price=D(10), role_codes=[role.code], currency='USD', exchange_rate=D(7), logistics_cost=D(0))
        except Exception as exc:
            print('FOREIGN_NONE', type(exc).__name__, str(exc))
        await s.rollback()

    async with SessionLocal() as s:
        user, role, sku, customer, quote, version = await fixture(s)
        for n in range(3):
            s.add(await snapshot(s, role, sku, customer, version, D('33.3333')))
        await s.flush()
        await qs.recalc_version(s, version)
        quote.status, version.approval_status = 'accepted', 'approved'
        version.sent_at = version.accepted_at = datetime.now(UTC)
        order = await orders.create_order_from_quote(s, version=version, user_id=user.id)
        lines = list((await s.execute(select(SalesOrderItem).where(SalesOrderItem.order_id==order.id))).scalars())
        print('ROUNDING_MISMATCH', {'quote_goods': str(version.subtotal_amount), 'order_goods': str(order.goods_amount), 'sum_order_lines': str(sum((i.amount for i in lines), D(0))), 'order_total': str(order.total_amount)})
        await s.rollback()

    async with SessionLocal() as s:
        diff = IntegrationDiff(domain='order', system_type='jushuitan', object_type='order', diff_type='amount_mismatch', diff_key=TAG, current_value={'amount':'100'}, incoming_value={'amount':'200'}, status='open')
        s.add(diff)
        await s.commit()
        ident = diff.id
    try:
        async with SessionLocal() as first, SessionLocal() as second:
            a = await first.get(IntegrationDiff, ident)
            b = await second.get(IntegrationDiff, ident)
            r1 = await reconcile.confirm_diff(first, a, resolution='keep_local', note='first confirmation', operator_id=None)
            await first.commit()
            try:
                r2 = await reconcile.confirm_diff(second, b, resolution='take_external', note='stale second confirmation', operator_id=None)
                await second.commit()
                print('DIFF_CONFIRM_OVERWRITE', {'first':r1['diff']['resolution'], 'second':r2['diff']['resolution'], 'second_succeeded':True})
            except Exception as exc:
                print('DIFF_CONFIRM_REJECTED', type(exc).__name__, str(exc))
                await second.rollback()
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(IntegrationDiff).where(IntegrationDiff.id==ident))
            await s.commit()
    await engine.dispose()

if __name__ == '__main__':
    asyncio.run(main())
