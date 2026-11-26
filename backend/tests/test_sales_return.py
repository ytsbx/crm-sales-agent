"""销售退货：登记金额、主管复核、财务改金额留痕、无自动抵扣、退款与作废。

2026-11-26 主人拍板后的口径：
- 登记时就填退款金额（含货款和运费），不再等主管通过后另填；
- 主管通过后直接进入待财务登记退款；
- 财务可以修改退款金额，但每次改动都要写审计；
- 新退货不再自动抵扣客户未付款，应收计划保持原样；
- 历史抵扣单仍保留作废还原逻辑，但不能再直接改金额。

用隔离 SQLite + 真实 service；通知与里程碑在本用例里不是断言对象，monkeypatch 掉。
"""

import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal as D
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from conftest import SyncSessionAsAsync, make_engine, make_user
from app.core.audit import AuditLog
from app.core.base import Base
from app.core.errors import AppError
from app.modules.customer.model import Customer
from app.modules.file.model import FileRecord
from app.modules.order.model import (
    OrderShipmentBatch,
    OrderShipmentBatchItem,
    SalesOrder,
    SalesOrderItem,
)
from app.modules.payment import service as payment_svc
from app.modules.payment.model import ReceivablePlan
from app.modules.returns import service as svc
from app.modules.returns.model import (
    SalesReturn,
    SalesReturnItem,
    SalesReturnPlanAdjustment,
)
from app.modules.returns.schema import ReturnCreate, ReturnItemInput
from app.modules.settings.model import NumberingRule, NumberSequence
from app.modules.user.model import User

NOW = datetime.now(UTC)


def run(coro):
    return asyncio.run(coro)


USER = make_user(1, data_scope="all", permissions=("order:manage", "payment:manage"))
REVIEWER = make_user(
    2,
    data_scope="all",
    permissions=("order:assign", "order:view", "payment:view"),
    roles=("sales_manager",),
)
FINANCE = make_user(
    3,
    data_scope="all",
    permissions=("order:manage", "payment:manage"),
    roles=("finance",),
)
SALESPERSON_WITHOUT_PAYMENT = make_user(
    4,
    data_scope="all",
    permissions=("order:manage", "order:view"),
    roles=("salesperson",),
)


class AsyncSession(SyncSessionAsAsync):
    async def delete(self, obj):
        self._session.delete(obj)

    def add_all(self, objs):
        self._session.add_all(objs)


@pytest.fixture
def db(monkeypatch):
    # 通知与里程碑不是本用例的断言对象：只守退货数量、钱和状态。
    monkeypatch.setattr(svc, "record_and_notify", AsyncMock())
    from app.modules.order import milestones as milestones_svc

    monkeypatch.setattr(milestones_svc, "sync_payment_milestones", AsyncMock())
    engine = make_engine()
    Base.metadata.create_all(
        engine,
        tables=[
            OrderShipmentBatch.__table__,
            OrderShipmentBatchItem.__table__,
            SalesOrderItem.__table__,
            SalesReturn.__table__,
            SalesReturnItem.__table__,
            SalesReturnPlanAdjustment.__table__,
            FileRecord.__table__,
            AuditLog.__table__,
            NumberingRule.__table__,
            NumberSequence.__table__,
        ],
    )
    with Session(engine, expire_on_commit=False) as raw:
        user = User(
            id=1,
            username="returner",
            password_hash="x",
            name="退货经办",
            status="active",
            created_at=NOW,
            updated_at=NOW,
        )
        customer = Customer(id=1, name="退货客户", owner_id=1)
        raw.add_all([user, customer])
        order = SalesOrder(
            id=1,
            order_no="SO-RET-1",
            customer_id=1,
            owner_id=1,
            total_amount=D("100.00"),
            currency="CNY",
            status="completed",
        )
        item = SalesOrderItem(
            id=1,
            order_id=1,
            sku_snapshot="SKU-R1",
            specification="红色",
            quantity=D("10"),
            unit_price=D("10.0000"),
            amount=D("100.00"),
        )
        batch = OrderShipmentBatch(
            id=1,
            order_id=1,
            batch_no=1,
            status="shipped",
            actual_ship_date=date(2026, 11, 20),
            created_at=NOW,
        )
        batch_item = OrderShipmentBatchItem(
            id=1,
            batch_id=1,
            order_item_id=1,
            sku_snapshot="SKU-R1",
            planned_qty=D("10"),
            shipped_qty=D("10"),
        )
        plans = [
            ReceivablePlan(
                id=1,
                order_id=1,
                plan_name="定金",
                due_date=date(2026, 12, 1),
                amount=D("30.00"),
                status="pending",
                created_at=NOW,
            ),
            ReceivablePlan(
                id=2,
                order_id=1,
                plan_name="尾款",
                due_date=date(2026, 12, 20),
                amount=D("70.00"),
                status="pending",
                created_at=NOW,
            ),
        ]
        raw.add_all([order, item, batch, batch_item, *plans])
        raw.flush()
        yield AsyncSession(raw), order, item, batch_item, plans


def _approve(session, row):
    return run(
        svc.review_return(
            session, REVIEWER, row, decision="approve", note="数量与原因已核对"
        )
    )


def _payload(qty: str = "3", refund: str = "30", order_item_id: int = 1):
    return ReturnCreate(
        reason_code="customer_rejected",
        reason="客户拒收",
        refund_amount=D(refund),
        items=[ReturnItemInput(order_item_id=order_item_id, quantity=D(qty))],
    )


def test_create_records_refund_amount_and_no_offset(db):
    s, order, item, batch_item, plans = db
    row = run(svc.create_return(s, USER, order, _payload("3", "30")))

    assert row.status == "reviewing"
    assert row.goods_amount == D("30.00")
    # 登记时填的退款金额直接落库；主管通过后不用再填一次。
    assert row.settled_goods_amount == D("30.00")
    assert row.total_amount == D("30.00")
    assert row.refund_amount == D("30.00")
    # 新流程没有自动抵扣，也没有生成应收调整。
    assert row.offset_amount == D("0.00")
    assert row.settled_by == USER.id

    audit = s._session.execute(
        select(AuditLog).where(
            AuditLog.business_type == "sales_return",
            AuditLog.business_id == row.id,
            AuditLog.action == "create_return",
        )
    ).scalars().one()
    assert audit.after_data["refund_amount"] == "30.00"


def test_return_over_quantity_and_cancel_frees_it(db):
    s, order, item, batch_item, plans = db
    row = run(svc.create_return(s, USER, order, _payload("3", "30")))
    assert row.status == "reviewing"
    assert row.goods_amount == D("30.00")
    # 已发 10、已退 3，再退 8 必须拦（最多 7）
    with pytest.raises(AppError, match="最多还能退 7"):
        run(svc.create_return(s, USER, order, _payload("8", "80")))
    assert row.status == "reviewing"
    # 取消后释放占用，3 又能退
    run(svc.cancel_return(s, USER, row, "客户又不要退了"))
    assert row.status == "cancelled"
    again = run(svc.create_return(s, USER, order, _payload("3", "30")))
    assert again.status == "reviewing"
    assert again.goods_amount == D("30.00")


def test_same_product_across_batches_does_not_double_count_reference(db):
    """同一产品分两批发 5+5，一次退 10：参考金额应是 100，不是 200。"""
    s, order, item, batch_item, plans = db
    batch_item.shipped_qty = D("5")
    second_batch = OrderShipmentBatch(
        id=2,
        order_id=1,
        batch_no=2,
        status="shipped",
        actual_ship_date=date(2026, 11, 22),
        created_at=NOW,
    )
    second_item = OrderShipmentBatchItem(
        id=2,
        batch_id=2,
        order_item_id=1,
        sku_snapshot="SKU-R1",
        planned_qty=D("5"),
        shipped_qty=D("5"),
    )
    s._session.add_all([second_batch, second_item])
    s._session.flush()
    row = run(svc.create_return(
        s, USER, order,
        ReturnCreate(
            reason_code="customer_rejected",
            reason="客户整单退回",
            refund_amount=D("100.00"),
            items=[ReturnItemInput(order_item_id=1, quantity=D("10"))],
        ),
    ))
    assert row.goods_amount == D("100.00")
    # 新退货明细只挂订单明细，不再写批次字段；历史批次记录仍保留在原表里。
    item_row = s._session.execute(
        select(SalesReturnItem).where(SalesReturnItem.return_id == row.id)
    ).scalars().one()
    assert item_row.order_item_id == 1
    assert item_row.shipment_batch_item_id is None
    assert item_row.batch_id is None
    # 可退概览按订单明细汇总两批 5+5，不再返回 batches。
    overview = run(svc.returnable_overview(s, order.id))
    assert len(overview["items"]) == 1
    assert overview["items"][0]["shipped_quantity_exact"] == "10.000"
    assert overview["items"][0]["remaining_quantity_exact"] == "0.000"
    assert "batches" not in overview


def test_review_approve_goes_directly_to_completed_and_keeps_amount(db):
    s, order, item, batch_item, plans = db
    row = run(svc.create_return(s, USER, order, _payload("10", "100")))
    approved = _approve(s, row)

    assert approved.status == "completed"
    assert approved.settled_goods_amount == D("100.00")
    assert approved.offset_amount == D("0.00")
    assert approved.refund_amount == D("100.00")
    # 不再自动冲应收，原计划金额保持不变。
    assert s._session.get(ReceivablePlan, 1).amount == D("30.00")
    assert s._session.get(ReceivablePlan, 2).amount == D("70.00")


def test_finance_can_update_amount_after_review_and_writes_audit(db):
    s, order, item, batch_item, plans = db
    row = run(svc.create_return(s, USER, order, _payload("5", "50")))
    _approve(s, row)
    assert row.status == "completed"

    updated = run(svc.update_return_amount(
        s, FINANCE, row,
        refund_amount=D("60.00"),
        note="财务核对后按客户确认金额调整",
    ))
    assert updated.status == "completed"
    assert updated.settled_goods_amount == D("60.00")
    assert updated.total_amount == D("60.00")
    assert updated.refund_amount == D("60.00")
    assert updated.offset_amount == D("0.00")
    # 财务改金额同样不碰应收计划。
    assert s._session.get(ReceivablePlan, 1).amount == D("30.00")
    assert s._session.get(ReceivablePlan, 2).amount == D("70.00")

    audit = s._session.execute(
        select(AuditLog).where(
            AuditLog.business_type == "sales_return",
            AuditLog.business_id == row.id,
            AuditLog.action == "update_return_amount",
        )
    ).scalars().one()
    assert audit.before_data["settled_goods_amount"] == "50.00"
    assert audit.after_data["settled_goods_amount"] == "60.00"
    assert audit.after_data["note"] == "财务核对后按客户确认金额调整"

    summary = run(payment_svc.order_finance_summary(s, order.id))
    assert summary["returned_amount_exact"] == "60.00"
    assert summary["return_offset_amount_exact"] == "0.00"
    assert summary["refund_pending_amount_exact"] == "60.00"
    # 没有自动抵扣，客户未付款仍是原订单额减去已确认回款（本用例没有回款）。
    assert summary["unreceived_amount_exact"] == "100.00"


def test_finance_can_update_amount_while_reviewing_then_supervisor_approves(db):
    s, order, item, batch_item, plans = db
    row = run(svc.create_return(s, USER, order, _payload("3", "30")))
    assert row.status == "reviewing"

    updated = run(svc.update_return_amount(
        s, FINANCE, row, refund_amount=D("25.00"), note="财务先核对金额"
    ))
    assert updated.status == "reviewing"
    assert updated.settled_goods_amount == D("25.00")

    approved = _approve(s, row)
    assert approved.status == "completed"
    assert approved.settled_goods_amount == D("25.00")


def test_salesperson_without_payment_manage_cannot_change_amount(db):
    s, order, item, batch_item, plans = db
    row = run(svc.create_return(s, USER, order, _payload("3", "30")))
    _approve(s, row)
    with pytest.raises(AppError, match="只有财务"):
        run(svc.update_return_amount(
            s, SALESPERSON_WITHOUT_PAYMENT, row, refund_amount=D("20.00")
        ))


def test_refund_must_be_registered_then_cannot_void_or_change_amount(db):
    s, order, item, batch_item, plans = db
    from app.modules.payment.model import PaymentAllocation, PaymentRecord

    # 全部收清、退 20 -> 全部退现。
    payment = PaymentRecord(
        id=1,
        order_id=1,
        received_date=date(2026, 11, 21),
        received_amount=D("100.00"),
        currency="CNY",
        status="confirmed",
        created_at=NOW,
    )
    s.add(payment)
    s._session.flush()
    s.add_all(
        [
            PaymentAllocation(payment_id=1, plan_id=1, amount=D("30.00"), created_at=NOW, updated_at=NOW),
            PaymentAllocation(payment_id=1, plan_id=2, amount=D("70.00"), created_at=NOW, updated_at=NOW),
        ]
    )
    s._session.flush()
    row = run(svc.create_return(s, USER, order, _payload("2", "20")))
    _approve(s, row)
    run(svc.refund_return(s, FINANCE, row, "银行流水 20261125001"))
    assert row.status == "refunded"
    with pytest.raises(AppError, match="已经实际退款"):
        run(svc.void_return(s, FINANCE, row, "想撤销"))
    with pytest.raises(AppError, match="已经实际退款"):
        run(svc.update_return_amount(
            s, FINANCE, row, refund_amount=D("10.00")
        ))


def test_void_new_return_keeps_plans_unchanged(db):
    s, order, item, batch_item, plans = db
    row = run(svc.create_return(s, USER, order, _payload("4", "40")))
    _approve(s, row)
    assert row.status == "completed"
    assert s._session.get(ReceivablePlan, 2).amount == D("70.00")

    run(svc.void_return(s, FINANCE, row, "退错了"))
    assert row.status == "voided"
    # 新退货本来就没冲应收，作废后计划还是 30/70。
    assert s._session.get(ReceivablePlan, 1).amount == D("30.00")
    assert s._session.get(ReceivablePlan, 2).amount == D("70.00")
    # 作废后释放可退数量，能重新登记。
    overview = run(svc.returnable_overview(s, order.id))
    assert overview["items"][0]["remaining_quantity_exact"] == "10.000"


def test_legacy_pending_return_can_fill_amount_and_goes_completed(db):
    """兼容历史旧单：没有金额的 pending 单，财务补填后进入待财务结算。"""
    s, order, item, batch_item, plans = db
    row = run(svc.create_return(s, USER, order, _payload("3", "30")))
    row.status = "pending"
    row.settled_goods_amount = None
    row.total_amount = D("0.00")
    row.refund_amount = D("0.00")
    s._session.flush()

    updated = run(svc.update_return_amount(
        s, FINANCE, row, refund_amount=D("25.00"), note="历史单补金额"
    ))
    assert updated.status == "completed"
    assert updated.settled_goods_amount == D("25.00")
    assert updated.refund_amount == D("25.00")


def test_historical_offset_return_cannot_change_amount(db):
    s, order, item, batch_item, plans = db
    row = run(svc.create_return(s, USER, order, _payload("3", "30")))
    _approve(s, row)
    # 模拟历史抵扣单：当时冲过客户未付款。
    row.offset_amount = D("20.00")
    row.refund_amount = D("10.00")
    s._session.flush()
    with pytest.raises(AppError, match="历史.*抵扣"):
        run(svc.update_return_amount(
            s, FINANCE, row, refund_amount=D("30.00")
        ))


def test_historical_offset_void_restores_plan_exactly_and_blocks_manual_edit(db):
    """历史抵扣单的作废仍按原快照恢复，不能被新流程改坏。"""
    s, order, item, batch_item, plans = db
    row = run(svc.create_return(s, USER, order, _payload("4", "40")))
    _approve(s, row)
    row.offset_amount = D("40.00")
    row.refund_amount = D("0.00")
    row.total_amount = D("40.00")
    adjustment = SalesReturnPlanAdjustment(
        return_id=row.id,
        plan_id=2,
        amount_before=D("70.00"),
        applied_amount=D("40.00"),
        amount_after=D("30.00"),
        status_before="pending",
    )
    s.add(adjustment)
    s._session.get(ReceivablePlan, 2).amount = D("30.00")
    s._session.flush()

    # 财务在退货后手工改了计划：作废必须拦住，不能把改动覆盖掉。
    s._session.get(ReceivablePlan, 2).amount = D("35.00")
    s._session.flush()
    with pytest.raises(AppError, match="手工改过"):
        run(svc.void_return(s, FINANCE, row, "退错了"))
    s._session.get(ReceivablePlan, 2).amount = D("30.00")
    s._session.flush()
    run(svc.void_return(s, FINANCE, row, "退错了"))
    assert row.status == "voided"
    assert s._session.get(ReceivablePlan, 2).amount == D("70.00")


def test_supervisor_self_review_allowed_but_salesperson_blocked(db):
    s, order, item, batch_item, plans = db
    # 主管自己登记的退货，主管自己复核：允许，但审计要标 self_reviewed。
    row = run(svc.create_return(s, REVIEWER, order, _payload("3", "30")))
    assert row.status == "reviewing"
    assert row.created_by == REVIEWER.id

    # 业务员没有 order:assign，不能复核（即使这张不是他登记的）。
    with pytest.raises(AppError, match="只有主管"):
        run(svc.review_return(s, USER, row, decision="approve", note=None))

    approved = run(
        svc.review_return(
            s, REVIEWER, row, decision="approve", note="本人复核：已核对退货照片"
        )
    )
    assert approved.status == "completed"
    assert approved.reviewed_by == REVIEWER.id
    assert approved.created_by == REVIEWER.id

    audit = s._session.execute(
        select(AuditLog).where(
            AuditLog.business_type == "sales_return",
            AuditLog.business_id == row.id,
            AuditLog.action == "review_return",
        )
    ).scalars().first()
    assert audit is not None
    assert audit.after_data["self_reviewed"] is True


def test_review_reject_releases_quantity(db):
    s, order, item, batch_item, plans = db
    row = run(svc.create_return(s, REVIEWER, order, _payload("3", "30")))
    assert row.status == "reviewing"
    rejected = run(
        svc.review_return(
            s, REVIEWER, row, decision="reject", note="客户没把货寄回，先驳回"
        )
    )
    assert rejected.status == "rejected"
    assert rejected.reviewed_by == REVIEWER.id
    assert rejected.review_note == "客户没把货寄回，先驳回"
    # 驳回后不占可退数量：同一批还能重新登记 10 件
    again = run(svc.create_return(s, USER, order, _payload("10", "100")))
    assert again.status == "reviewing"
    assert again.goods_amount == D("100.00")


def test_finance_manual_amount_can_be_less_or_full(db):
    s, order, item, batch_item, plans = db
    # 退 5 件，登记参考 50；财务按协商改成 30，再改成整单 100。
    row = run(svc.create_return(s, USER, order, _payload("5", "50")))
    _approve(s, row)
    less = run(svc.update_return_amount(
        s, FINANCE, row, refund_amount=D("30.00"),
        note="客户承担 20 元折损",
    ))
    assert less.goods_amount == D("50.00")  # 登记参考价保留
    assert less.settled_goods_amount == D("30.00")
    assert less.refund_amount == D("30.00")
    assert less.offset_amount == D("0.00")

    full = run(svc.update_return_amount(
        s, FINANCE, row, refund_amount=D("100.00"),
        note="客户投诉，整单全退",
    ))
    assert full.goods_amount == D("50.00")
    assert full.settled_goods_amount == D("100.00")
    assert full.refund_amount == D("100.00")
    assert full.total_amount == D("100.00")


def test_refund_attachment_metadata_serialized(db):
    """退款附件只存 file_id，序列化时回查文件中心的名字、大小和类型。"""
    s, order, item, batch_item, plans = db
    row = run(svc.create_return(s, USER, order, _payload("3", "30")))
    _approve(s, row)
    assert row.status == "completed"
    assert row.offset_amount == D("0.00")
    assert row.refund_amount == D("30.00")

    attachment = FileRecord(
        storage_provider="local",
        object_key="2026/11/refund-proof.pdf",
        file_name="银行回单.pdf",
        mime_type="application/pdf",
        size=1234,
        checksum="abc",
        uploaded_by=1,
    )
    s.add(attachment)
    run(s.flush())
    row.refund_attachment_file_id = attachment.id
    run(s.flush())

    data = run(svc.serialize_return(s, row))
    assert data["refund_attachment_file_id"] == attachment.id
    assert data["refund_attachment_file_name"] == "银行回单.pdf"
    assert data["refund_attachment_file_size"] == 1234
    assert data["refund_attachment_mime_type"] == "application/pdf"


def _add_payment(s, amount: str, allocations: list[tuple[int, str]]):
    from app.modules.payment.model import PaymentAllocation, PaymentRecord

    payment = PaymentRecord(
        id=1,
        order_id=1,
        received_date=date(2026, 11, 21),
        received_amount=D(amount),
        currency="CNY",
        status="confirmed",
        created_at=NOW,
    )
    s.add(payment)
    s._session.flush()
    s.add_all(
        [
            PaymentAllocation(
                payment_id=1,
                plan_id=plan_id,
                amount=D(allocated),
                created_at=NOW,
                updated_at=NOW,
            )
            for plan_id, allocated in allocations
        ]
    )
    s._session.flush()
    return payment


def test_settle_full_offset_clears_unreceived_without_cash(db):
    """整单退货全部抵扣：未付款归 0，状态已结算，不再一直悬浮。"""
    s, order, item, batch_item, plans = db
    row = run(svc.create_return(s, USER, order, _payload("10", "100")))
    _approve(s, row)
    settled = run(
        svc.settle_return(
            s,
            FINANCE,
            row,
            total_amount=D("100.00"),
            offset_amount=D("100.00"),
            note="客户还没付款，整单退货直接抵未付款",
        )
    )
    assert settled.status == "settled"
    assert settled.total_amount == D("100.00")
    assert settled.offset_amount == D("100.00")
    assert settled.refund_amount == D("0.00")
    assert settled.refund_reference is None
    assert settled.settled_by == FINANCE.id

    summary = run(payment_svc.order_finance_summary(s, 1))
    assert summary["returned_amount_exact"] == "100.00"
    assert summary["return_offset_amount_exact"] == "100.00"
    assert summary["return_refund_amount_exact"] == "0.00"
    assert summary["return_refunded_amount_exact"] == "0.00"
    assert summary["unreceived_amount_exact"] == "0.00"
    assert summary["net_order_amount_exact"] == "0.00"

    # 订单详情/列表也必须减掉退货抵扣；只改汇总接口会出现“汇总 0、详情还挂 800”。
    from app.modules.order import service as order_svc

    ctx = run(order_svc.order_context(s, [order]))
    serialized = order_svc.serialize_order(
        order,
        received_amount=ctx["received"].get(order.id, 0),
        return_offset_amount=ctx["return_offsets"].get(order.id, 0),
    )
    assert serialized["unreceived_amount_exact"] == "0.00"

    audit = s._session.execute(
        select(AuditLog).where(
            AuditLog.business_type == "sales_return",
            AuditLog.business_id == row.id,
            AuditLog.action == "settle_return",
        )
    ).scalars().one()
    assert audit.after_data["status"] == "settled"
    assert audit.after_data["offset_amount"] == "100.00"
    assert audit.after_data["refund_amount"] == "0.00"
    assert len(audit.after_data["plans"]) == 2
    adjustments = s._session.execute(
        select(SalesReturnPlanAdjustment).where(
            SalesReturnPlanAdjustment.return_id == row.id
        )
    ).scalars().all()
    assert len(adjustments) == 2
    assert sum((item.applied_amount for item in adjustments), D("0.00")) == D("100.00")
    assert all(item.amount_after == D("0.00") for item in adjustments)

    # 已用抵扣结清，不能再改金额、不能再作废。
    with pytest.raises(AppError, match="已经实际退款或结算"):
        run(svc.update_return_amount(s, FINANCE, row, refund_amount=D("90.00")))
    with pytest.raises(AppError, match="已经用抵扣"):
        run(svc.void_return(s, FINANCE, row, "想撤销"))


def test_settle_partial_offset_and_cash_refund_requires_reference(db):
    s, order, item, batch_item, plans = db
    _add_payment(s, "20.00", [(1, "20.00")])
    row = run(svc.create_return(s, USER, order, _payload("3", "30")))
    _approve(s, row)

    with pytest.raises(AppError, match="必须填写退款流水"):
        run(
            svc.settle_return(
                s,
                FINANCE,
                row,
                total_amount=D("30.00"),
                offset_amount=D("10.00"),
            )
        )

    settled = run(
        svc.settle_return(
            s,
            FINANCE,
            row,
            total_amount=D("30.00"),
            offset_amount=D("10.00"),
            refund_reference="银行流水 20261126001",
            note="先抵 10，再退 20",
        )
    )
    assert settled.status == "refunded"
    assert settled.total_amount == D("30.00")
    assert settled.offset_amount == D("10.00")
    assert settled.refund_amount == D("20.00")
    assert settled.refund_reference == "银行流水 20261126001"

    # 先到期的定金余额 10 被抵完，计划金额从 30 降到 20；尾款不动。
    assert plans[0].amount == D("20.00")
    assert plans[1].amount == D("70.00")

    summary = run(payment_svc.order_finance_summary(s, 1))
    assert summary["returned_amount_exact"] == "30.00"
    assert summary["return_offset_amount_exact"] == "10.00"
    assert summary["return_refunded_amount_exact"] == "20.00"
    assert summary["refund_pending_amount_exact"] == "0.00"
    assert summary["unreceived_amount_exact"] == "70.00"
    assert summary["net_received_amount_exact"] == "0.00"


def test_settle_offset_cannot_exceed_unreceived(db):
    s, order, item, batch_item, plans = db
    _add_payment(s, "90.00", [(1, "30.00"), (2, "60.00")])
    row = run(svc.create_return(s, USER, order, _payload("3", "30")))
    _approve(s, row)

    with pytest.raises(AppError, match="可抵扣上限 10.00"):
        run(
            svc.settle_return(
                s,
                FINANCE,
                row,
                total_amount=D("30.00"),
                offset_amount=D("20.00"),
                refund_reference="银行流水 20261126002",
            )
        )
    # 失败不落库、不改计划、不改状态。
    assert row.status == "completed"
    assert row.offset_amount == D("0.00")
    assert plans[0].amount == D("30.00")
    assert plans[1].amount == D("70.00")
