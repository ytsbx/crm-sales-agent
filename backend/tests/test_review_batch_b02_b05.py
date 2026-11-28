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


@pytest.mark.anyio
async def test_multi_role_margin_takes_strictest():
    """B02-05：多角色时最低利润率取最严（最高要求），不能被宽松角色放宽。"""
    from app.modules.pricing import service as pricing_service
    from app.modules.pricing.model import PricePermission
    from app.modules.settings.model import SystemSetting
    from app.modules.user.model import Role

    engine = _engine([Role, PricePermission, SystemSetting])
    try:
        with Session(engine, expire_on_commit=False) as raw:
            raw.add_all([
                Role(id=1, code="strict", name="严"),
                Role(id=2, code="loose", name="宽"),
                PricePermission(role_id=1, minimum_margin=D("0.30"), status="active"),
                PricePermission(role_id=2, minimum_margin=D("0.10"), status="active"),
            ])
            raw.commit()
            margin, _ = await pricing_service.resolve_min_margin(
                SyncSessionAsAsync(raw), ["strict", "loose"]
            )
            assert margin == D("0.30")
    finally:
        engine.dispose()


@pytest.mark.anyio
async def test_completed_order_cannot_create_schedule_change(monkeypatch):
    """B04-05：已完成订单不能再发起交期变更。"""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.modules.order import schedule

    order = SimpleNamespace(id=1, status="completed", delivery_date=date(2026, 11, 1))
    monkeypatch.setattr(schedule, "lock_order", AsyncMock(return_value=order))
    actor = make_user(1, permissions=("order:manage",), data_scope="all")
    with pytest.raises(AppError) as caught:
        await schedule.create_change(
            SimpleNamespace(), order, user=actor,
            new_delivery_date=date(2026, 12, 1), reason="改期",
        )
    assert caught.value.http_status == 422
    assert "已完成或已取消" in caught.value.message


@pytest.mark.anyio
async def test_cancelled_receivable_only_allows_remark(monkeypatch):
    """B05-08：已取消的应收只允许改备注。"""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.modules.payment import router as payment_router
    from app.modules.payment.schema import ReceivableUpdate

    plan = SimpleNamespace(
        id=1, status="cancelled", plan_name="一期", due_date=None,
        amount=D("100"), remark=None, order_id=1,
    )
    monkeypatch.setattr(payment_router.svc, "get_visible_plan", AsyncMock(return_value=plan))
    monkeypatch.setattr(payment_router.svc, "serialize_plan", AsyncMock(return_value={}))
    actor = make_user(1, permissions=("payment:manage",), data_scope="all")
    with pytest.raises(AppError) as caught:
        await payment_router.update_receivable(
            1, ReceivableUpdate(amount=D("5")), _request(), actor, SimpleNamespace()
        )
    assert caught.value.http_status == 422
    assert "只能修改备注" in caught.value.message


@pytest.mark.anyio
async def test_cancelled_order_can_be_restored():
    """恢复已取消订单：保留编号与明细，状态回到取消前。"""
    from app.modules.order.model import OrderShipmentBatch, OrderStatusHistory
    from app.modules.payment.model import PaymentRecord

    engine = _engine([User, Customer, SalesOrder, OrderStatusHistory,
                      OrderShipmentBatch, PaymentRecord, AuditLog])
    try:
        with Session(engine, expire_on_commit=False) as raw:
            raw.add_all([
                Customer(id=1, name="客户", owner_id=1),
                SalesOrder(id=1, order_no="SO-1", customer_id=1, owner_id=1, status="cancelled"),
                OrderStatusHistory(order_id=1, old_status="pending", new_status="cancelled",
                                   operator_id=1, created_at=datetime.now(UTC)),
            ])
            raw.commit()
            actor = make_user(1, permissions=("order:manage",), data_scope="all")
            result = await order_router.restore_order(1, _request(), actor, SyncSessionAsAsync(raw))
            assert result["data"]["status"] == "pending"
            assert raw.get(SalesOrder, 1).status == "pending"
            audit = raw.query(AuditLog).filter_by(action="restore", business_id=1).one()
            assert audit.before_data["status"] == "cancelled"
    finally:
        engine.dispose()


@pytest.mark.anyio
async def test_shipped_order_cannot_be_restored():
    """已发生发货的取消单不能恢复（履约事实不复活）。"""
    from app.modules.order.model import OrderShipmentBatch, OrderStatusHistory
    from app.modules.payment.model import PaymentRecord

    engine = _engine([User, Customer, SalesOrder, OrderStatusHistory,
                      OrderShipmentBatch, PaymentRecord, AuditLog])
    try:
        with Session(engine, expire_on_commit=False) as raw:
            raw.add_all([
                Customer(id=1, name="客户", owner_id=1),
                SalesOrder(id=1, order_no="SO-1", customer_id=1, owner_id=1, status="cancelled"),
                OrderStatusHistory(order_id=1, old_status="pending", new_status="cancelled",
                                   operator_id=1, created_at=datetime.now(UTC)),
                OrderShipmentBatch(order_id=1, batch_no=1, status="shipped",
                                   created_at=datetime.now(UTC)),
            ])
            raw.commit()
            actor = make_user(1, permissions=("order:manage",), data_scope="all")
            with pytest.raises(AppError) as caught:
                await order_router.restore_order(1, _request(), actor, SyncSessionAsAsync(raw))
            assert caught.value.http_status == 422
            assert "发货" in caught.value.message
            assert raw.get(SalesOrder, 1).status == "cancelled"
    finally:
        engine.dispose()


@pytest.mark.anyio
async def test_sent_quote_can_be_voided_but_not_deleted():
    """报价作废：已发送可作废保留；作废后仍不能删除。"""
    from app.modules.quote import router as quote_router
    from app.modules.quote.model import Quote, QuoteVersion
    from app.modules.quote.schema import QuoteVoid

    engine = _engine([User, Customer, Quote, QuoteVersion, SalesOrder, AuditLog])
    try:
        with Session(engine, expire_on_commit=False) as raw:
            raw.add_all([
                Customer(id=1, name="客户", owner_id=1),
                Quote(id=1, quote_no="BJ-1", customer_id=1, owner_id=1,
                      status="sent", created_at=datetime.now(UTC)),
            ])
            raw.commit()
            actor = make_user(1, permissions=("quote:manage",), data_scope="all")
            result = await quote_router.void_quote(
                1, QuoteVoid(reason="客户取消合作"), _request(), actor, SyncSessionAsAsync(raw)
            )
            assert result["data"]["status"] == "voided"
            audit = raw.query(AuditLog).filter_by(action="void", business_id=1).one()
            assert audit.after_data["reason"] == "客户取消合作"
            with pytest.raises(AppError) as caught:
                await quote_router.delete_quote(1, _request(), actor, SyncSessionAsAsync(raw))
            assert caught.value.http_status == 422
            assert "不能删除" in caught.value.message
    finally:
        engine.dispose()


@pytest.mark.anyio
async def test_quote_void_requires_reason_and_rejects_converted():
    """作废必须填原因；已转订单的报价不能作废。"""
    from app.modules.quote import router as quote_router
    from app.modules.quote.model import Quote, QuoteVersion
    from app.modules.quote.schema import QuoteVoid

    engine = _engine([User, Customer, Quote, QuoteVersion, SalesOrder, AuditLog])
    try:
        with Session(engine, expire_on_commit=False) as raw:
            raw.add_all([
                Customer(id=1, name="客户", owner_id=1),
                Quote(id=1, quote_no="BJ-1", customer_id=1, owner_id=1,
                      status="accepted", created_at=datetime.now(UTC)),
                SalesOrder(id=1, order_no="SO-1", customer_id=1, owner_id=1, quote_id=1),
            ])
            raw.commit()
            actor = make_user(1, permissions=("quote:manage",), data_scope="all")
            with pytest.raises(AppError) as caught:
                await quote_router.void_quote(
                    1, QuoteVoid(reason="   "), _request(), actor, SyncSessionAsAsync(raw)
                )
            assert caught.value.http_status == 422
            assert "作废原因" in caught.value.message
            with pytest.raises(AppError) as caught:
                await quote_router.void_quote(
                    1, QuoteVoid(reason="作废"), _request(), actor, SyncSessionAsAsync(raw)
                )
            assert "已转成订单" in caught.value.message
    finally:
        engine.dispose()


@pytest.mark.anyio
async def test_schedule_preview_names_batches_needing_manual_adjustment():
    """旧交期没有可换算的发货日时，系统不猜批次该挪几天，只点名交给人改。"""
    from types import SimpleNamespace

    from app.modules.order import schedule
    from app.modules.order.model import OrderMilestone, OrderShipmentBatch

    engine = _engine([OrderMilestone, OrderShipmentBatch])
    try:
        with Session(engine, expire_on_commit=False) as raw:
            raw.add(OrderShipmentBatch(
                id=1, order_id=1, batch_no=1, status="planned",
                planned_date=date(2026, 12, 5), created_at=datetime.now(UTC),
            ))
            raw.commit()
            order = SimpleNamespace(
                id=1, order_no="SO-1", delivery_date=None, delivery_kind=None,
                transit_days=None, plan_offsets=None,
            )
            result = await schedule.preview(
                SyncSessionAsAsync(raw), order, date(2026, 12, 20),
                delivery_kind="shipping",
            )
            assert result["shift_days"] is None
            assert result["batches"] == []
            assert result["batches_to_confirm"] == [
                {"batch_id": 1, "batch_no": 1, "planned_date": "2026-12-05"}
            ]
    finally:
        engine.dispose()


@pytest.mark.anyio
async def test_reschedule_planned_batch_syncs_milestone_and_audits():
    """PATCH 批次计划日：只动计划中批次，同步跟单节点，前后版本进审计。"""
    from app.modules.order import milestones as milestones_svc
    from app.modules.order.model import OrderMilestone, OrderShipmentBatch
    from app.modules.order.schema import ShipmentBatchReschedule

    engine = _engine([User, Customer, SalesOrder, OrderMilestone,
                      OrderShipmentBatch, AuditLog])
    try:
        with Session(engine, expire_on_commit=False) as raw:
            raw.add_all([
                Customer(id=1, name="客户", owner_id=1),
                SalesOrder(id=1, order_no="SO-1", customer_id=1, owner_id=1,
                           status="in_production"),
                OrderShipmentBatch(id=1, order_id=1, batch_no=1, status="planned",
                                   planned_date=date(2026, 12, 5),
                                   created_at=datetime.now(UTC)),
                OrderMilestone(order_id=1, node=milestones_svc.batch_node_key(1),
                               planned_date=date(2026, 12, 5),
                               created_at=datetime.now(UTC)),
            ])
            raw.commit()
            actor = make_user(1, permissions=("order:manage",), data_scope="all")
            result = await order_router.reschedule_shipment(
                1, 1, ShipmentBatchReschedule(
                    planned_date=date(2026, 12, 18), reason="客户改期"),
                _request(), actor, SyncSessionAsAsync(raw),
            )
            assert result["data"]["planned_date"] == "2026-12-18"
            assert raw.get(OrderShipmentBatch, 1).planned_date == date(2026, 12, 18)
            node = raw.query(OrderMilestone).filter_by(order_id=1).one()
            assert node.planned_date == date(2026, 12, 18)
            audit = raw.query(AuditLog).filter_by(
                action="reschedule_shipment_batch", business_id=1).one()
            assert audit.before_data["planned_date"] == "2026-12-05"
            assert audit.after_data["planned_date"] == "2026-12-18"
            assert audit.after_data["reason"] == "客户改期"
    finally:
        engine.dispose()


@pytest.mark.anyio
async def test_reschedule_rejects_shipped_batch_and_blank_reason():
    """已发货的批次是事实，不能再改计划日；原因不能只填空格。"""
    from app.modules.order.model import OrderMilestone, OrderShipmentBatch
    from app.modules.order.schema import ShipmentBatchReschedule

    engine = _engine([User, Customer, SalesOrder, OrderMilestone,
                      OrderShipmentBatch, AuditLog])
    try:
        with Session(engine, expire_on_commit=False) as raw:
            raw.add_all([
                Customer(id=1, name="客户", owner_id=1),
                SalesOrder(id=1, order_no="SO-1", customer_id=1, owner_id=1,
                           status="shipped"),
                OrderShipmentBatch(id=1, order_id=1, batch_no=1, status="shipped",
                                   planned_date=date(2026, 12, 5),
                                   actual_ship_date=date(2026, 12, 6),
                                   created_at=datetime.now(UTC)),
            ])
            raw.commit()
            actor = make_user(1, permissions=("order:manage",), data_scope="all")
            with pytest.raises(AppError) as caught:
                await order_router.reschedule_shipment(
                    1, 1, ShipmentBatchReschedule(
                        planned_date=date(2026, 12, 18), reason="  "),
                    _request(), actor, SyncSessionAsAsync(raw),
                )
            assert "原因" in caught.value.message
            with pytest.raises(AppError) as caught:
                await order_router.reschedule_shipment(
                    1, 1, ShipmentBatchReschedule(
                        planned_date=date(2026, 12, 18), reason="客户改期"),
                    _request(), actor, SyncSessionAsAsync(raw),
                )
            assert "已登记发货" in caught.value.message
            assert raw.get(OrderShipmentBatch, 1).planned_date == date(2026, 12, 5)
    finally:
        engine.dispose()


@pytest.mark.anyio
async def test_quote_delete_blocks_historical_customer_evidence_and_declined():
    """B02-04：历史版本已发送/已接受/已拒绝，或当前已拒绝，都不能删；纯草稿仍可删。"""
    from app.modules.quote import router as quote_router
    from app.modules.quote.model import Quote, QuoteVersion

    engine = _engine([User, Customer, Quote, QuoteVersion, SalesOrder, AuditLog])
    try:
        with Session(engine, expire_on_commit=False) as raw:
            raw.add_all([
                User(id=1, name="业务员", username="u1", password_hash="x", status="active"),
                Customer(id=1, name="客户", owner_id=1),
            ])
            quote_with_history = Quote(
                id=1, quote_no="BJ-1", customer_id=1, owner_id=1, created_by=1,
                status="draft", created_at=datetime.now(UTC),
            )
            raw.add(quote_with_history)
            raw.flush()
            raw.add_all([
                QuoteVersion(id=1, quote_id=1, version_no=1, created_at=datetime.now(UTC),
                             sent_at=datetime.now(UTC), accepted_at=datetime.now(UTC)),
                QuoteVersion(id=2, quote_id=1, version_no=2, created_at=datetime.now(UTC)),
            ])
            quote_with_history.current_version_id = 2
            raw.add_all([
                Quote(id=2, quote_no="BJ-2", customer_id=1, owner_id=1,
                      status="declined", created_at=datetime.now(UTC)),
                Quote(id=3, quote_no="BJ-3", customer_id=1, owner_id=1, created_by=1,
                      status="draft", current_version_id=3, created_at=datetime.now(UTC)),
                QuoteVersion(id=3, quote_id=3, version_no=1, created_at=datetime.now(UTC)),
            ])
            raw.commit()
            actor = make_user(1, permissions=("quote:manage",), data_scope="all")

            with pytest.raises(AppError) as caught:
                await quote_router.delete_quote(1, _request(), actor, SyncSessionAsAsync(raw))
            assert caught.value.http_status == 422
            assert "历史版本" in caught.value.message
            assert raw.get(Quote, 1).deleted_at is None

            with pytest.raises(AppError) as caught:
                await quote_router.delete_quote(2, _request(), actor, SyncSessionAsAsync(raw))
            assert caught.value.http_status == 422
            assert "已拒绝" in caught.value.message
            assert raw.get(Quote, 2).deleted_at is None

            await quote_router.delete_quote(3, _request(), actor, SyncSessionAsAsync(raw))
            assert raw.get(Quote, 3).deleted_at is not None
    finally:
        engine.dispose()


@pytest.mark.anyio
async def test_quote_list_scope_splits_declined_into_history():
    """B02-04：报价中不含客户拒绝，历史报价只含客户拒绝；不传 scope 保持旧行为。"""
    from app.modules.quote import router as quote_router
    from app.modules.quote.model import Quote, QuoteVersion

    engine = _engine([User, Customer, Quote, QuoteVersion, SalesOrder, AuditLog])
    try:
        with Session(engine, expire_on_commit=False) as raw:
            raw.add_all([
                User(id=1, name="业务员", username="u1", password_hash="x", status="active"),
                Customer(id=1, name="客户", owner_id=1),
                Quote(id=1, quote_no="BJ-1", customer_id=1, owner_id=1, created_by=1,
                      status="draft", created_at=datetime.now(UTC)),
                Quote(id=2, quote_no="BJ-2", customer_id=1, owner_id=1,
                      status="declined", created_at=datetime.now(UTC)),
                Quote(id=3, quote_no="BJ-3", customer_id=1, owner_id=1,
                      status="sent", created_at=datetime.now(UTC)),
            ])
            raw.commit()
            actor = make_user(1, permissions=("quote:view",), data_scope="all")

            active = await quote_router.list_quotes(
                scope="active", page=1, page_size=20, user=actor, session=SyncSessionAsAsync(raw)
            )
            assert active["data"]["total"] == 2
            assert {item["status"] for item in active["data"]["items"]} == {"draft", "sent"}

            history = await quote_router.list_quotes(
                scope="history", page=1, page_size=20, user=actor, session=SyncSessionAsAsync(raw)
            )
            assert history["data"]["total"] == 1
            assert {item["status"] for item in history["data"]["items"]} == {"declined"}

            all_rows = await quote_router.list_quotes(
                page=1, page_size=20, user=actor, session=SyncSessionAsAsync(raw)
            )
            assert all_rows["data"]["total"] == 3
    finally:
        engine.dispose()
