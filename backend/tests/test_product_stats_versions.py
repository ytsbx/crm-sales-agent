"""真实 SQL 聚合回归：版本修订不重复计毛利，订单独立记录不能漏行。"""
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tests.conftest import SyncSessionAsAsync, make_user
from app.modules.analytics.service import product_stats
from app.modules.customer.model import Customer
from app.modules.opportunity.model import OpportunityItem
from app.modules.order.model import SalesOrder, SalesOrderItem
from app.modules.product.model import Product, Sku
from app.modules.quote.model import Quote, QuoteItem, QuoteVersion


@pytest.fixture
def stats_db(db_session):
    for model in (Product, Sku, OpportunityItem, QuoteItem, SalesOrderItem):
        model.__table__.create(db_session.get_bind(), checkfirst=True)
    db_session.add_all([Customer(id=1, name='客户'), Product(id=1, name='产品'),
                        Sku(id=1, product_id=1, sku_code='SKU-1', name='产品')])
    db_session.flush()
    return db_session


def version(db, number, *, formal=True, sku_id=1, profits=('80',), quote_id=1):
    if db.get(Quote, quote_id) is None:
        db.add(Quote(id=quote_id, quote_no=f'Q-{quote_id}', customer_id=1, owner_id=101))
        db.flush()
    row = QuoteVersion(quote_id=quote_id, version_no=number, created_at=datetime.now(UTC),
                       submitted_at=datetime.now(UTC) if formal else None)
    db.add(row)
    db.flush()
    for profit in profits:
        db.add(QuoteItem(quote_version_id=row.id, sku_id=sku_id, quantity=1,
                         quoted_price=100, profit_snapshot=Decimal(profit)))
    db.flush()


async def read_stats(db, permissions=('price:manage',)):
    return await product_stats(SyncSessionAsAsync(db), make_user(permissions=permissions, data_scope='all'))


@pytest.mark.anyio
async def test_revision_counts_stay_historical_but_profit_uses_latest_formal(stats_db):
    version(stats_db, 1)
    version(stats_db, 2)
    result = (await read_stats(stats_db))[0]
    assert result['quote_formal_times'] == 1
    assert result['version_revision_times'] == 2
    assert result['quote_quantity'] == 2
    assert result['quote_estimated_profit'] == 80


@pytest.mark.anyio
async def test_latest_formal_sums_all_lines_and_separate_quotes(stats_db):
    version(stats_db, 1, profits=('80',))
    version(stats_db, 2, profits=('30', '40'))
    version(stats_db, 3, formal=False, profits=('999',))
    version(stats_db, 1, quote_id=2, profits=('10',))
    result = (await read_stats(stats_db))[0]
    assert result['quote_estimated_profit'] == 80
    assert result['quote_formal_times'] == 2
    assert result['version_revision_times'] == 4
    assert (await read_stats(stats_db, permissions=()))[0]['quote_estimated_profit'] is None


@pytest.mark.anyio
async def test_custom_quote_without_sku_does_not_crash_statistics(stats_db):
    version(stats_db, 1, sku_id=None)
    assert isinstance(await read_stats(stats_db), list)


@pytest.mark.anyio
async def test_product_with_only_order_is_included_and_cancelled_order_excluded(stats_db):
    for i, status in [(1, 'pending'), (2, 'cancelled')]:
        stats_db.add(SalesOrder(id=i, order_no=f'O-{i}', customer_id=1, owner_id=101, status=status))
        stats_db.flush()
        stats_db.add(SalesOrderItem(order_id=i, sku_id=1, quantity=3 if i==1 else 100, unit_price=10, amount=30))
    stats_db.flush()
    result = (await read_stats(stats_db))[0]
    assert result['order_won_quantity'] == 3
    assert result['quote_formal_times'] == 0
