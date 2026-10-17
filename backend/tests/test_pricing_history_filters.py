"""核价历史筛选必须匹配完整编号，不能把 1 当成 12 的前缀。"""
import pytest
from sqlalchemy import select

from app.core.audit import AuditLog
from app.modules.customer.model import Customer
from app.modules.pricing.router import _filter_history_by_sku, pricing_history
from tests.conftest import SyncSessionAsAsync, make_user
from tests.test_pricing_history_scope import history_db as scope_history_db


@pytest.fixture
def history_db(db_session):
    return scope_history_db.__wrapped__(db_session)


@pytest.mark.parametrize("snapshot", ["before_data", "after_data"])
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("has_more_fields", [False, True])
def test_sku_filter_does_not_match_longer_id_prefix(db_session, snapshot, nested, has_more_fields):
    AuditLog.__table__.create(db_session.get_bind(), checkfirst=True)
    for number in (1, 12, 100):
        data = {"sku_id": number}
        if has_more_fields:
            data["quantity"] = 1
        if nested:
            data = {"items": [data]}
        db_session.add(AuditLog(id=number, action="update", business_type="quote",
                                business_id=number, **{snapshot: data}))
    db_session.flush()
    stmt = _filter_history_by_sku(select(AuditLog), AuditLog, 1)
    assert [r.id for r in db_session.scalars(stmt)] == [1]


@pytest.mark.anyio
async def test_customer_filter_matches_complete_id_and_delete_before_snapshot(history_db):
    history_db.add(Customer(id=12, name="另一个本人客户", owner_id=101))
    history_db.flush()
    for log_id, customer_id in ((10, 1), (11, 12)):
        history_db.add(AuditLog(id=log_id, business_type="customer_price_rule", business_id=log_id,
                                action="update", after_data={"customer_id": customer_id, "sku_id": 1}))
    history_db.add(AuditLog(id=12, business_type="customer_price_rule", business_id=12,
                            action="delete", before_data={"customer_id": 1, "sku_id": 1}))
    history_db.flush()
    result = await pricing_history(sku_id=None, customer_id=1, page=1, page_size=20,
                                   user=make_user(101, permissions=("product:view", "customer:view")),
                                   session=SyncSessionAsAsync(history_db))
    assert {r["id"] for r in result["data"]["items"]} == {10, 12}
    assert result["data"]["total"] == 2
