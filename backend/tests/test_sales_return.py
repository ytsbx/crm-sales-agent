"""销售退货：数量闸门、抵扣/退款、作废还原、状态流转。

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
from app.modules.file.model import FileRecord
from app.modules.customer.model import Customer
from app.modules.order.model import (
    OrderShipmentBatch,
    OrderShipmentBatchItem,
    SalesOrder,
    SalesOrderItem,
)
from app.modules.payment import service as payment_svc
from app.modules.payment.model import (
    PaymentAllocation,
    PaymentRecord,
    ReceivablePlan,
)
from app.modules.returns import service as svc
from app.modules.returns.model import SalesReturn, SalesReturnItem, SalesReturnPlanAdjustment
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


def _payload(qty: str = "3", order_item_id: int = 1):
    return ReturnCreate(
        reason_code="customer_rejected",
        reason="客户拒收",
        items=[ReturnItemInput(order_item_id=order_item_id, quantity=D(qty))],
    )


def test_return_over_quantity_and_cancel_frees_it(db):
    s, order, item, batch_item, plans = db
    row = run(svc.create_return(s, USER, order, _payload("3")))
    assert row.status == "reviewing"
    assert row.goods_amount == D("30.00")
    # 已发 10、已退 3，再退 8 必须拦（最多 7）
    with pytest.raises(AppError, match="最多还能退 7"):
        run(svc.create_return(s, USER, order, _payload("8")))
    assert row.status == "reviewing"
    # 取消后释放占用，3 又能退
    run(svc.cancel_return(s, USER, row, "客户又不要退了"))
    assert row.status == "cancelled"
    again = run(svc.create_return(s, USER, order, _payload("3")))
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


def test_settle_offsets_unpaid_then_refunds_paid_part(db):
    s, order, item, batch_item, plans = db
    # 收 40 元（分配到第一期 30、第二期 10），退 100：未收 60 先冲，剩下 40 退现。
    payment = PaymentRecord(
        id=1,
        order_id=1,
        received_date=date(2026, 11, 21),
        received_amount=D("40.00"),
        currency="CNY",
        status="confirmed",
        created_at=NOW,
    )
    s.add(payment)
    s._session.flush()
    s.add_all(
        [
            PaymentAllocation(payment_id=1, plan_id=1, amount=D("30.00"), created_at=NOW, updated_at=NOW),
            PaymentAllocation(payment_id=1, plan_id=2, amount=D("10.00"), created_at=NOW, updated_at=NOW),
        ]
    )
    s._session.flush()
    for plan in plans:
        run(svc.payment_svc.recalc_plan(s, plan))
    row = run(svc.create_return(s, USER, order, _payload("10")))
    _approve(s, row)
    result = run(svc.settle_return(
        s, USER, row, settled_goods_amount=D("100.00"),
        freight_refund_amount=D("0"), settlement_note=None,
    ))
    assert result.status == "completed"
    assert result.offset_amount == D("60.00")
    assert result.refund_amount == D("40.00")
    # 未收 60 全部冲掉：第一期本来已收满 30，第二期 10 已收 + 60 冲减 = 70
    p1 = s._session.get(ReceivablePlan, 1)
    p2 = s._session.get(ReceivablePlan, 2)
    assert (p1.amount, p1.status) == (D("30.00"), "paid")
    assert (p2.amount, p2.status) == (D("10.00"), "paid")
    adjustments = s._session.execute(
        select(SalesReturnPlanAdjustment).where(SalesReturnPlanAdjustment.return_id == row.id)
    ).scalars().all()
    assert len(adjustments) == 1
    assert adjustments[0].plan_id == 2 and adjustments[0].applied_amount == D("60.00")


def test_finance_cannot_set_refund_amount(db):
    """退多少钱必须由业务员/主管谈；财务有 order:manage 也不能借这个口子改金额。"""
    s, order, item, batch_item, plans = db
    row = run(svc.create_return(s, USER, order, _payload("3")))
    _approve(s, row)
    with pytest.raises(AppError, match="只能由业务员或主管填写"):
        run(svc.settle_return(
            s, FINANCE, row, settled_goods_amount=D("30.00"),
            freight_refund_amount=D("0"), settlement_note="财务想直接填",
        ))


def test_refund_must_be_registered_then_cannot_void(db):
    s, order, item, batch_item, plans = db
    # 全部收清，退 20 -> 全部退现
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
    s.add(PaymentAllocation(payment_id=1, plan_id=1, amount=D("30.00"), created_at=NOW, updated_at=NOW))
    s.add(PaymentAllocation(payment_id=1, plan_id=2, amount=D("70.00"), created_at=NOW, updated_at=NOW))
    s._session.flush()
    row = run(svc.create_return(s, USER, order, _payload("2")))
    _approve(s, row)
    result = run(svc.settle_return(
        s, USER, row, settled_goods_amount=D("20.00"),
        freight_refund_amount=D("0"), settlement_note="质量补偿"
    ))
    assert result.offset_amount == D("0.00")
    assert result.refund_amount == D("20.00")
    run(svc.refund_return(s, USER, row, "银行流水 20261125001"))
    assert row.status == "refunded"
    with pytest.raises(AppError, match="已经实际退款"):
        run(svc.void_return(s, USER, row, "想撤销"))


def test_void_restores_plan_exactly_and_blocks_after_manual_edit(db):
    s, order, item, batch_item, plans = db
    row = run(svc.create_return(s, USER, order, _payload("4")))
    _approve(s, row)
    run(svc.settle_return(
        s, USER, row, settled_goods_amount=D("40.00"),
        freight_refund_amount=D("0"), settlement_note=None,
    ))
    assert row.status == "completed"
    assert row.offset_amount == D("40.00")
    p2 = s._session.get(ReceivablePlan, 2)
    assert p2.amount == D("30.00")
    # 财务在退货后手工改了计划：作废必须拦住，不能把改动覆盖掉
    p2.amount = D("35.00")
    s._session.flush()
    with pytest.raises(AppError, match="手工改过"):
        run(svc.void_return(s, USER, row, "退错了"))
    # 还原后可以正常作废
    p2.amount = D("30.00")
    s._session.flush()
    run(svc.void_return(s, USER, row, "退错了"))
    assert row.status == "voided"
    assert s._session.get(ReceivablePlan, 2).amount == D("70.00")


def test_freight_refund_is_separate_and_counts_toward_refund(db):
    s, order, item, batch_item, plans = db
    row = run(svc.create_return(s, USER, order, _payload("2")))  # 货款 20
    _approve(s, row)
    result = run(svc.settle_return(
        s, USER, row, settled_goods_amount=D("20.00"),
        freight_refund_amount=D("10.00"),
        settlement_note="客户承担一半运费",
    ))
    assert result.goods_amount == D("20.00")
    assert result.freight_refund_amount == D("10.00")
    assert result.total_amount == D("30.00")
    # 运费不冲应收：货款 20 全冲应收，运费 10 单独进退款。
    assert result.offset_amount == D("20.00")
    assert result.refund_amount == D("10.00")
    summary = run(payment_svc.order_finance_summary(s, 1))
    assert summary["returned_amount_exact"] == "20.00"


def test_void_older_return_keeps_later_return(db):
    """两张退货先后冲同一期：作废前一张，后一张的冲减必须保留。"""
    s, order, item, batch_item, plans = db
    first = run(svc.create_return(s, USER, order, _payload("2")))  # 20
    _approve(s, first)
    run(svc.settle_return(
        s, USER, first, settled_goods_amount=D("20.00"),
        freight_refund_amount=D("0"), settlement_note=None,
    ))
    second = run(svc.create_return(s, USER, order, _payload("3")))  # 30
    _approve(s, second)
    run(svc.settle_return(
        s, USER, second, settled_goods_amount=D("30.00"),
        freight_refund_amount=D("0"), settlement_note=None,
    ))
    p2 = s._session.get(ReceivablePlan, 2)
    assert p2.amount == D("20.00")  # 70 - 20 - 30

    # 先作废第一张：只剩第二张的 30，应收回到 40（不是 50）。
    run(svc.void_return(s, USER, first, "第一张退错了"))
    assert s._session.get(ReceivablePlan, 2).amount == D("40.00")
    assert first.status == "voided"

    # 再作废第二张：全部恢复 70。
    run(svc.void_return(s, USER, second, "第二张也退错了"))
    assert s._session.get(ReceivablePlan, 2).amount == D("70.00")
    assert second.status == "voided"


def test_supervisor_self_review_allowed_but_salesperson_blocked(db):
    s, order, item, batch_item, plans = db
    # 主管自己登记的退货，主管自己复核：允许，但审计要标 self_reviewed。
    row = run(svc.create_return(s, REVIEWER, order, _payload("3")))
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
    assert approved.status == "pending"
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
    row = run(svc.create_return(s, REVIEWER, order, _payload("3")))
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
    again = run(svc.create_return(s, USER, order, _payload("10")))
    assert again.status == "reviewing"
    assert again.goods_amount == D("100.00")


def test_finance_manual_amount_can_be_less_or_full(db):
    s, order, item, batch_item, plans = db
    # 退 5 件，登记参考 50；财务按协商只确认 30
    less = run(svc.create_return(s, USER, order, _payload("5")))
    _approve(s, less)
    result = run(svc.settle_return(
        s, USER, less, settled_goods_amount=D("30.00"),
        freight_refund_amount=D("0"), settlement_note="客户承担 20 元折损",
    ))
    assert result.goods_amount == D("50.00")  # 登记参考价保留
    assert result.settled_goods_amount == D("30.00")
    assert result.offset_amount == D("30.00")
    assert result.refund_amount == D("0.00")
    assert result.total_amount == D("30.00")


def test_finance_manual_full_amount_can_exceed_reference(db):
    s, order, item, batch_item, plans = db
    # 只退 5 件、登记参考 50，但公司同意按整单 100 全退；系统照财务填的记。
    row = run(svc.create_return(s, USER, order, _payload("5")))
    _approve(s, row)
    result = run(svc.settle_return(
        s, USER, row, settled_goods_amount=D("100.00"),
        freight_refund_amount=D("0"), settlement_note="客户投诉，整单全退",
    ))
    assert result.goods_amount == D("50.00")
    assert result.settled_goods_amount == D("100.00")
    assert result.offset_amount == D("100.00")
    assert result.refund_amount == D("0.00")
    assert result.total_amount == D("100.00")


def test_refund_attachment_metadata_serialized(db):
    """退款附件只存 file_id，序列化时回查文件中心的名字、大小和类型。"""
    s, order, item, batch_item, plans = db
    row = run(svc.create_return(s, USER, order, _payload("3")))
    row.status = "completed"
    row.settled_goods_amount = D("30.00")
    row.total_amount = D("30.00")
    row.offset_amount = D("30.00")
    row.refund_amount = D("0.00")

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
