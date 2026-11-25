"""B02–B05 本批修复的针对性回归：订单转移范围、成本同日、价格规则删除、导入审计。"""

from datetime import UTC, date, datetime
from decimal import Decimal as D

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.core.audit import AuditLog
from app.core.base import Base
from app.core.errors import AppError
from app.modules.customer.model import Customer
from app.modules.order import router as order_router
from app.modules.order.model import SalesOrder
from app.modules.order.schema import OrderUpdate
from app.modules.pricing import router as pricing_router
from app.modules.pricing.model import PriceRule, ProductCost
from app.modules.pricing.schema import CostCreate, PriceRuleCreate
from app.modules.product.model import Product, Sku
from app.modules.user.model import User
from tests.conftest import SyncSessionAsAsync, _register_sqlite_bigint_as_integer, make_user


def _request():
    return Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1)})


def _engine(tables):
    _register_sqlite_bigint_as_integer()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[table.__table__ for table in tables])
    return engine


@pytest.mark.anyio
async def test_order_owner_transfer_requires_target_in_scope():
    """B04-04：只能把订单转给操作者数据范围内的人。"""
    engine = _engine([User, Customer, SalesOrder, AuditLog])
    try:
        with Session(engine, expire_on_commit=False) as raw:
            raw.add_all([
                Customer(id=1, name="客户", owner_id=1),
                User(id=1, name="主管", username="u1", password_hash="x", status="active"),
                User(id=2, name="外部门同事", username="u2", password_hash="x", status="active"),
                SalesOrder(id=1, order_no="SO-1", customer_id=1, owner_id=1),
            ])
            raw.commit()
            actor = make_user(1, permissions=("order:manage", "order:assign"), data_scope="self")
            with pytest.raises(AppError) as caught:
                await order_router.update_order(
                    1, OrderUpdate(owner_id=2), _request(), actor, SyncSessionAsAsync(raw)
                )
            assert caught.value.http_status == 403
            assert raw.get(SalesOrder, 1).owner_id == 1
    finally:
        engine.dispose()


def _pricing_engine():
    return _engine([Product, Sku, ProductCost, PriceRule, AuditLog])


@pytest.mark.anyio
async def test_cost_create_rejects_same_day_duplicate():
    """B03-04：同一天只能有一条成本版本，写入口直接 409。"""
    engine = _pricing_engine()
    try:
        with Session(engine, expire_on_commit=False) as raw:
            raw.add(Product(id=1, name="产品"))
            raw.add(Sku(id=1, product_id=1, sku_code="SKU-1", name="SKU"))
            raw.commit()
            actor = make_user(1, permissions=("price:manage",), data_scope="all")
            payload = CostCreate(effective_from=date(2026, 1, 1), purchase_cost=D("10"))
            await pricing_router.create_cost(1, payload, _request(), actor, SyncSessionAsAsync(raw))
            with pytest.raises(AppError) as caught:
                await pricing_router.create_cost(1, payload, _request(), actor, SyncSessionAsAsync(raw))
            assert caught.value.http_status == 409
            assert "同一天" in caught.value.message
    finally:
        engine.dispose()


@pytest.mark.anyio
async def test_price_rule_rejects_deleted_sku():
    """B03-07：已删除的 SKU 不能再建价格规则。"""
    engine = _pricing_engine()
    try:
        with Session(engine, expire_on_commit=False) as raw:
            raw.add(Product(id=1, name="产品"))
            raw.add(Sku(id=1, product_id=1, sku_code="SKU-1", name="SKU",
                        deleted_at=datetime.now(UTC)))
            raw.commit()
            actor = make_user(1, permissions=("price:manage",), data_scope="all")
            with pytest.raises(AppError) as caught:
                await pricing_router.create_price_rule(
                    PriceRuleCreate(sku_id=1), _request(), actor, SyncSessionAsAsync(raw)
                )
            assert caught.value.http_status == 404
            assert "已删除" in caught.value.message
    finally:
        engine.dispose()


def test_import_audit_rows_keep_changes_and_truncate():
    """B03-20：导入审计带逐行明细，超限截断并标记。"""
    from app.modules.pricing.io_router import _AUDIT_ROWS_LIMIT, _audit_rows

    class Report:
        pass

    report = Report()
    report.created = [{"row": index} for index in range(_AUDIT_ROWS_LIMIT + 5)]
    report.updated = [
        {"row": 1, "changes": {"purchase_cost": {"before": D("1"), "after": D("2")}}}
    ]
    report.skipped = []

    rows = _audit_rows(report)["rows"]
    assert len(rows["created"]) == _AUDIT_ROWS_LIMIT
    assert rows["truncated"] is True
    assert rows["updated"][0]["changes"]["purchase_cost"]["after"] == D("2")
