from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from app.core.jsonio import DecimalJSONResponse
from starlette.requests import Request

from app.core.audit import AuditLog
from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.errors import AppError, register_exception_handlers
from app.modules.customer.model import Customer
from app.modules.erp import router as erp_router
from app.modules.order import milestones, router, service
from app.modules.order.model import (
    OrderShipmentBatch,
    OrderShipmentBatchItem,
    OrderStatusHistory,
    SalesOrder,
    SalesOrderItem,
)
from app.modules.order.schema import OrderCreate, OrderStatusChange, ShipmentBatchShip
from app.modules.product.model import Product, Sku
from conftest import SyncSessionAsAsync, make_user


@pytest.fixture
def manual_order_db(db_session, monkeypatch):
    for model in (
        Product, Sku, SalesOrderItem, OrderStatusHistory,
        OrderShipmentBatch, OrderShipmentBatchItem, AuditLog,
    ):
        model.__table__.create(db_session.get_bind(), checkfirst=True)
    db_session.add_all([
        Customer(id=1, name="测试客户", owner_id=101),
        Product(id=1, name="测试产品"),
        Sku(id=1, product_id=1, sku_code="SKU-MANUAL", name="测试规格"),
    ])
    db_session.commit()
    monkeypatch.setattr(service, "generate_order_no", AsyncMock(side_effect=["SO-1", "SO-2", "SO-3"]))
    followup = AsyncMock()
    monkeypatch.setattr(router.followup_service, "record_and_notify", followup)
    monkeypatch.setattr(router.notification_service, "dispatch_after_commit", AsyncMock())
    return db_session, SyncSessionAsAsync(db_session), followup


@pytest.mark.anyio
@pytest.mark.parametrize("method,path", [
    ("POST", "/orders/{id}/refresh-status"),
    ("GET", "/integrations/erp/orders/{id}/status"),
])
async def test_erp_status_sync_requires_order_management_in_scope(manual_order_db, monkeypatch, method, path):
    raw, session, _ = manual_order_db
    raw.add_all([
        SalesOrder(id=1, order_no="SO-OWN", customer_id=1, owner_id=101),
        SalesOrder(id=2, order_no="SO-OTHER", customer_id=1, owner_id=102),
        OrderStatusHistory(order_id=1, old_status="pending", new_status="in_production",
                           source="ERP", created_at=datetime.now(UTC)),
    ])
    raw.commit()
    actor = make_user(101, permissions={"order:view"})
    refresh = AsyncMock(return_value={"changed": False})
    monkeypatch.setattr(erp_router.svc, "refresh_status", refresh)
    app = FastAPI(default_response_class=DecimalJSONResponse)
    register_exception_handlers(app)
    app.include_router(router.router)
    app.include_router(erp_router.router)
    app.dependency_overrides[get_current_user] = lambda: actor
    app.dependency_overrides[get_db] = lambda: session

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        history = await client.get("/orders/1/status-history")
        assert history.status_code == 200
        assert history.json()["data"][0]["new_status"] == "in_production"
        assert (await client.request(method, path.format(id=1))).status_code == 403
        actor.permissions.add("order:manage")
        assert (await client.request(method, path.format(id=2))).status_code == 403
        refresh.assert_not_awaited()
        assert (await client.request(method, path.format(id=1))).status_code == 200
        refresh.assert_awaited_once_with(session, order=raw.get(SalesOrder, 1), operator_id=101)


@pytest.mark.anyio
async def test_manual_order_replays_same_request_and_keeps_legacy_create(manual_order_db):
    raw, session, followup = manual_order_db
    user = make_user(101, permissions={"order:assign"})
    request = Request({"type": "http", "headers": [(b"x-request-key", b"manual-1")]})
    payload = OrderCreate(customer_id=1, items=[{"sku_id": 1, "quantity": 2, "unit_price": "1.25"}])
    first = await router.create_order(payload, request, user, session)
    retry = await router.create_order(payload.model_copy(update={"request_key": "manual-1"}), request, user, session)
    assert retry["data"] == first["data"]
    assert raw.query(SalesOrder).count() == raw.query(SalesOrderItem).count() == 1
    assert raw.query(AuditLog).count() == followup.await_count == 1

    with pytest.raises(AppError, match="内容不一致"):
        await router.create_order(payload.model_copy(update={"remark": "另一个请求"}), request, user, session)
    await session.rollback()
    assert raw.query(SalesOrder).count() == 1

    legacy = Request({"type": "http", "headers": []})
    fresh = await router.create_order(payload, legacy, user, session)
    assert fresh["data"]["order_id"] != first["data"]["order_id"]
    assert raw.query(SalesOrder).count() == 2

    raw.get(SalesOrder, first["data"]["order_id"]).owner_id = 102
    raw.commit()
    with pytest.raises(AppError) as denied:
        await router.create_order(payload, request, user, session)
    assert denied.value.http_status == 403
    assert raw.query(SalesOrder).count() == 2


@pytest.mark.anyio
async def test_manual_shipped_status_requires_actual_quantity_but_erp_can_report_status(manual_order_db):
    raw, session, _ = manual_order_db
    user = make_user(101, permissions={"order:manage"})
    request = Request({"type": "http", "headers": []})
    order = SalesOrder(id=1, order_no="SO-SHIP", customer_id=1, owner_id=101, status="pending")
    erp_order = SalesOrder(id=2, order_no="SO-ERP", customer_id=1, owner_id=101, status="pending")
    line = SalesOrderItem(id=1, order_id=1, sku_id=1, quantity=2, unit_price=1, amount=2)
    batch = OrderShipmentBatch(id=1, order_id=1, status="planned", created_at=datetime.now(UTC))
    batch_line = OrderShipmentBatchItem(batch_id=1, order_item_id=1, planned_qty=2, shipped_qty=0)
    raw.add_all([order, erp_order, line, batch, batch_line])
    raw.commit()

    change = OrderStatusChange(status="shipped")
    with pytest.raises(AppError, match="登记实际发货数量"):
        await router.change_status(order.id, change, request, user, session)
    assert order.status == "pending" and raw.query(OrderStatusHistory).count() == 0
    batch.status = "shipped"
    batch_line.shipped_qty = 1
    raw.flush()
    await router.change_status(order.id, change, request, user, session)
    assert order.status == "shipped"

    await service.change_status(session, erp_order, new_status="shipped", operator_id=None, source="ERP")
    assert erp_order.status == "shipped"


@pytest.mark.anyio
@pytest.mark.parametrize("items", [
    [{"order_item_id": 1, "shipped_qty": 3}],
    [],
    None,
])
async def test_ship_batch_requires_complete_actuals_when_items_are_supplied(manual_order_db, monkeypatch, items):
    raw, session, followup = manual_order_db
    mark_milestone = AsyncMock()
    monkeypatch.setattr(milestones, "mark_batch_shipped", mark_milestone)
    user = make_user(101, permissions={"order:manage"})
    request = Request({"type": "http", "headers": []})
    order = SalesOrder(id=1, order_no="SO-ACTUAL", customer_id=1, owner_id=101, status="shipped")
    batch = OrderShipmentBatch(id=1, order_id=1, status="planned", created_at=datetime.now(UTC))
    lines = [
        OrderShipmentBatchItem(batch_id=1, order_item_id=i, planned_qty=5, shipped_qty=0, sku_snapshot=name)
        for i, name in ((1, "甲货"), (2, "乙货"))
    ]
    raw.add_all([order, batch, *lines])
    raw.add_all(SalesOrderItem(id=i, order_id=1, sku_id=1, quantity=5, unit_price=1, amount=5)
                for i in (1, 2))
    raw.commit()

    if items is not None:
        with pytest.raises(AppError, match="请填齐本批实发数量，未发货请填 0") as error:
            await router.ship_batch(1, 1, ShipmentBatchShip(items=items), request, user, session)
        assert error.value.http_status == 422
        assert batch.status == "planned" and batch.actual_ship_date is None
        assert [line.shipped_qty for line in lines] == [0, 0]
        assert raw.query(AuditLog).count() == 0
        mark_milestone.assert_not_awaited()
        followup.assert_not_awaited()
        payload = ShipmentBatchShip(items=[
            {"order_item_id": 1, "shipped_qty": 3}, {"order_item_id": 2, "shipped_qty": 0},
        ])
        expected = [3, 0]
    else:
        payload = ShipmentBatchShip()
        expected = [5, 5]

    response = await router.ship_batch(1, 1, payload, request, user, session)
    assert batch.status == "shipped"
    assert [line.shipped_qty for line in lines] == expected
    assert Decimal(response["data"]["summary"]["remaining_exact"]) == 10 - sum(expected)
    assert raw.query(AuditLog).count() == 1
    mark_milestone.assert_awaited_once()
    followup.assert_awaited_once()
