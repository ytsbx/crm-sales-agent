"""核价审计历史必须继承原单权限；过滤发生在分页与计数之前。"""
from datetime import UTC, datetime

import pytest

from app.core.audit import AuditLog
from app.modules.customer.model import Customer
from app.modules.pricing.router import pricing_history
from app.modules.pricing.model import ProductCost, PriceRule, CustomerPriceRule
from app.modules.product.model import Product, Sku
from app.modules.quote.model import Quote, QuoteItem, QuoteVersion
from tests.conftest import SyncSessionAsAsync, make_user


@pytest.fixture
def history_db(db_session):
    for model in (AuditLog, Product, Sku, QuoteItem, ProductCost, PriceRule, CustomerPriceRule):
        model.__table__.create(db_session.get_bind(), checkfirst=True)
    db_session.add_all([
        Customer(id=1, name="本人客户", owner_id=101),
        Customer(id=2, name="他人客户", owner_id=999),
        Customer(id=3, name="公海客户", owner_id=None),
        Product(id=1, name="产品"),
    ])
    db_session.flush()
    db_session.add(Sku(id=1, product_id=1, sku_code="SKU-1"))
    for qid, owner in ((1, 101), (2, 999)):
        db_session.add(Quote(id=qid, quote_no=f"Q-{qid}", customer_id=qid, owner_id=owner))
    db_session.flush()
    db_session.add_all([QuoteVersion(id=i, quote_id=i, version_no=1,
                                    created_at=datetime.now(UTC)) for i in (1, 2)])
    db_session.flush()
    return db_session


def log(db, kind, bid, **data):
    row = AuditLog(business_type=kind, business_id=bid, action="update", after_data=data)
    db.add(row)
    db.flush()
    return row.id


async def read(db, *, permissions=("product:view", "quote:view", "customer:view", "price:manage"),
               scope="self", page=1, page_size=20):
    result = await pricing_history(sku_id=None, customer_id=None, page=page,
                                   page_size=page_size, session=SyncSessionAsAsync(db),
                                   user=make_user(101, permissions=permissions, data_scope=scope))
    return result["data"]


@pytest.mark.anyio
async def test_quote_scope_applies_before_pagination_and_total(history_db):
    visible = log(history_db, "quote", 1, quote_no="Q-1", total_amount=100)
    log(history_db, "quote", 2, quote_no="PRIVATE-Q-2", total_amount=9876)
    result = await read(history_db, page_size=1)
    assert result["total"] == 1
    assert [r["id"] for r in result["items"]] == [visible]


@pytest.mark.anyio
@pytest.mark.parametrize("kind,data", [("quote", {}), ("quote_item", {"quote_version_id": 1})])
async def test_quote_history_requires_module_view_even_with_full_price_scope(history_db, kind, data):
    log(history_db, kind, 1, **data)
    result = await read(history_db, permissions=("product:view", "price:manage"), scope="all")
    assert result["total"] == 0


@pytest.mark.anyio
async def test_deleted_item_still_inherits_original_quote_scope(history_db):
    visible = log(history_db, "quote_item", 88, quote_version_id=1, sku_id=1)
    log(history_db, "quote_item", 89, quote_version_id=2, sku_id=1)
    result = await read(history_db)
    assert [r["id"] for r in result["items"]] == [visible]


@pytest.mark.anyio
async def test_customer_price_history_follows_customer_scope_including_public_pool(history_db):
    own = log(history_db, "customer_price_rule", 11, customer_id=1, sku_id=1)
    log(history_db, "customer_price_rule", 12, customer_id=2, sku_id=1)
    public = log(history_db, "customer_price_rule", 13, customer_id=3, sku_id=1)
    result = await read(history_db)
    assert {r["id"] for r in result["items"]} == {own, public}
    result = await read(history_db, permissions=("product:view", "price:manage"), scope="all")
    assert result["total"] == 0


@pytest.mark.anyio
@pytest.mark.parametrize("scope,permissions,expected", [
    ("self", ("product:view", "price:manage"), 0),
    ("all", ("product:view",), 0),
    ("all", ("product:view", "price:manage"), 1),
])
async def test_orphan_logs_require_both_full_scope_and_price_management(history_db, scope, permissions, expected):
    log(history_db, "quote", 9999, quote_no="OLD-QUOTE")
    assert (await read(history_db, scope=scope, permissions=permissions))["total"] == expected


@pytest.mark.anyio
async def test_soft_deleted_quote_becomes_restricted_history(history_db):
    history_db.get(Quote, 1).deleted_at = datetime.now(UTC)
    log(history_db, "quote", 1, quote_no="Q-1")
    assert (await read(history_db))["total"] == 0
    assert (await read(history_db, scope="all"))["total"] == 1


@pytest.mark.anyio
async def test_visible_quote_still_redacts_snapshots_without_cost_permission(history_db):
    log(history_db, "quote", 1, quote_no="Q-1", total_amount=100)
    result = await read(history_db, permissions=("product:view", "quote:view"))
    assert result["total"] == 1
    assert result["items"][0]["after"] is None


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["quote_item", "customer_price_rule", "product_cost", "price_rule"])
async def test_other_orphan_types_follow_same_full_manager_rule(history_db, kind):
    log(history_db, kind, 9999)
    assert (await read(history_db))["total"] == 0
    assert (await read(history_db, scope="all"))["total"] == 1


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["product_cost", "price_rule"])
async def test_shared_product_history_keeps_existing_visibility_and_redaction(history_db, kind):
    log(history_db, kind, 9999, sku_id=1, minimum_price=25)
    ordinary = await read(history_db, permissions=("product:view",))
    assert ordinary["total"] == 1
    assert ordinary["items"][0]["after"] is None
    manager = await read(history_db)
    assert manager["items"][0]["after"]["minimum_price"] == 25


@pytest.mark.anyio
async def test_live_item_without_snapshot_uses_authoritative_parent(history_db):
    history_db.add(QuoteItem(id=50, quote_version_id=2, sku_id=1, quantity=1))
    history_db.flush()
    log(history_db, "quote_item", 50)
    # 即使旧快照缺字段，也不能被当作孤立日志让无报价权限的全量价格管理员看到。
    assert (await read(history_db, scope="all", permissions=("product:view", "price:manage")))["total"] == 0
    assert (await read(history_db, scope="all"))["total"] == 1


@pytest.mark.anyio
async def test_deleted_item_before_only_snapshot_keeps_traceability(history_db):
    history_db.add(AuditLog(business_type="quote_item", business_id=50, action="delete",
                            before_data={"quote_version_id": 1, "sku_id": 1}))
    history_db.flush()
    result = await read(history_db)
    assert result["total"] == 1
    assert result["items"][0]["target_link"] == "/quotes/1"
