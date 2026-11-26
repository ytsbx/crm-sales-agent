"""销售售后：申请金额构成、主管审核、仓库确认、财务自动先冲欠款、退款与作废。

2026-11-26 按分销商售后 OA 表单重新拍板：
- 售后申请必须挂订单，业务员填货款/运费/改价调整和退款登记表附件；
- 主管审核通过后先等仓库确认收货；
- 仓库确认后财务才退款，系统自动先冲客户未付款、剩余才现金退；
- 财务不改金额、不手填抵扣；
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
        application_file = FileRecord(
            id=1,
            storage_provider="local",
            object_key="2026/11/return-application.xlsx",
            file_name="分销商退款登记表.xlsx",
            mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            size=1024,
            checksum="return-app",
            uploaded_by=1,
        )
        reviewer_file = FileRecord(
            id=2,
            storage_provider="local",
            object_key="2026/11/return-application-reviewer.xlsx",
            file_name="分销商退款登记表-主管.xlsx",
            mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            size=1024,
            checksum="return-app-reviewer",
            uploaded_by=2,
        )
        raw.add_all([user, customer, application_file, reviewer_file])
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


def _warehouse(session, row):
    return run(
        svc.confirm_warehouse(
            session, REVIEWER, row, note="仓库已点收，数量一致"
        )
    )


def _payload(
    qty: str = "3",
    refund: str = "30",
    order_item_id: int = 1,
    attachment_id: int = 1,
):
    return ReturnCreate(
        reason_code="customer_rejected",
        reason="客户拒收",
        goods_refund_amount=D(refund),
        freight_refund_amount=D("0"),
        adjustment_amount=D("0"),
        payment_method="线下转账",
        receiving_account="测试账户 6222 0000 0000 0000",
        application_attachment_file_id=attachment_id,
        items=[ReturnItemInput(order_item_id=order_item_id, quantity=D(qty))],
    )


def test_create_records_refund_amount_and_no_offset(db):
    s, order, item, batch_item, plans = db
    row = run(svc.create_return(s, USER, order, _payload("3", "30")))

    assert row.status == "reviewing"
    assert row.goods_amount == D("30.00")
    # 业务员填的是构成，服务端合计总额；主管只审核，不再改金额。
    assert row.goods_refund_amount == D("30.00")
    assert row.freight_refund_amount == D("0.00")
    assert row.adjustment_amount == D("0.00")
    assert row.settled_goods_amount == D("30.00")
    assert row.total_amount == D("30.00")
    assert row.refund_amount == D("30.00")
    assert row.payment_method == "线下转账"
    assert row.receiving_account == "测试账户 6222 0000 0000 0000"
    assert row.application_attachment_file_id == 1
    # 审核前不自动抵扣，也没有生成应收调整。
    assert row.offset_amount == D("0.00")
    assert row.settled_by is None

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
            goods_refund_amount=D("100.00"),
            payment_method="线下转账",
            receiving_account="测试账户 6222 0000 0000 0000",
            application_attachment_file_id=1,
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


def test_review_approve_goes_to_warehouse_pending_and_keeps_amount(db):
    s, order, item, batch_item, plans = db
    row = run(svc.create_return(s, USER, order, _payload("10", "100")))
    approved = _approve(s, row)

    assert approved.status == "warehouse_pending"
    assert approved.settled_goods_amount == D("100.00")
    assert approved.offset_amount == D("0.00")
    assert approved.refund_amount == D("100.00")
    assert approved.warehouse_confirmed_by is None
    # 审核本身不冲应收，原计划金额保持不变。
    assert s._session.get(ReceivablePlan, 1).amount == D("30.00")
    assert s._session.get(ReceivablePlan, 2).amount == D("70.00")


def test_warehouse_confirmation_moves_to_finance_refund_pending(db):
    s, order, item, batch_item, plans = db
    row = run(svc.create_return(s, USER, order, _payload("10", "100")))
    _approve(s, row)

    # 仓库没确认之前，财务不能直接退款。
    with pytest.raises(AppError, match="待仓库确认收货"):
        run(svc.settle_return(s, FINANCE, row, refund_reference="银行流水 X"))

    confirmed = _warehouse(s, row)
    assert confirmed.status == "completed"
    assert confirmed.warehouse_confirmed_by == REVIEWER.id
    # 旧 refund_return 接口不能绕过自动抵扣直接全现金退。
    with pytest.raises(AppError, match="财务确认退款"):
        run(svc.refund_return(s, FINANCE, row, "银行流水 绕过测试"))
    assert confirmed.warehouse_note == "仓库已点收，数量一致"
    assert confirmed.total_amount == D("100.00")

    audit = s._session.execute(
        select(AuditLog).where(
            AuditLog.business_type == "sales_return",
            AuditLog.business_id == row.id,
            AuditLog.action == "confirm_return_warehouse",
        )
    ).scalars().one()
    assert audit.after_data["status"] == "completed"
    assert audit.after_data["warehouse_note"] == "仓库已点收，数量一致"


def test_finance_cannot_change_new_return_amount(db):
    s, order, item, batch_item, plans = db
    row = run(svc.create_return(s, USER, order, _payload("5", "50")))
    with pytest.raises(AppError, match="不能改金额"):
        run(svc.update_return_amount(s, FINANCE, row, refund_amount=D("60.00")))

    _approve(s, row)
    _warehouse(s, row)
    with pytest.raises(AppError, match="不能改金额"):
        run(svc.update_return_amount(s, FINANCE, row, refund_amount=D("60.00")))


def test_salesperson_without_payment_manage_cannot_change_amount(db):
    s, order, item, batch_item, plans = db
    row = run(svc.create_return(s, USER, order, _payload("3", "30")))
    _approve(s, row)
    _warehouse(s, row)
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
    _warehouse(s, row)
    run(svc.settle_return(s, FINANCE, row, refund_reference="银行流水 20261125001"))
    assert row.status == "refunded"
    assert row.offset_amount == D("0.00")
    assert row.refund_amount == D("20.00")
    with pytest.raises(AppError, match="已经实际退款"):
        run(svc.void_return(s, FINANCE, row, "想撤销"))
    with pytest.raises(AppError, match="不能改金额"):
        run(svc.update_return_amount(
            s, FINANCE, row, refund_amount=D("10.00")
        ))


def test_void_new_return_keeps_plans_unchanged(db):
    s, order, item, batch_item, plans = db
    row = run(svc.create_return(s, USER, order, _payload("4", "40")))
    _approve(s, row)
    assert row.status == "warehouse_pending"
    assert s._session.get(ReceivablePlan, 2).amount == D("70.00")

    run(svc.void_return(s, FINANCE, row, "退错了"))
    assert row.status == "voided"
    # 新售后本来就没冲应收，作废后计划还是 30/70。
    assert s._session.get(ReceivablePlan, 1).amount == D("30.00")
    assert s._session.get(ReceivablePlan, 2).amount == D("70.00")
    # 作废后释放可退数量，能重新登记。
    overview = run(svc.returnable_overview(s, order.id))
    assert overview["items"][0]["remaining_quantity_exact"] == "10.000"


def test_legacy_pending_return_can_fill_amount_and_goes_completed(db):
    """兼容历史旧单：没有金额的 pending 单，财务补填后进入待财务退款。"""
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
    assert updated.goods_refund_amount == D("25.00")
    assert updated.settled_goods_amount == D("25.00")
    assert updated.refund_amount == D("25.00")


def test_historical_offset_return_cannot_change_amount(db):
    s, order, item, batch_item, plans = db
    row = run(svc.create_return(s, USER, order, _payload("3", "30")))
    _approve(s, row)
    # 模拟历史抵扣单：当时冲过客户未付款。
    row.status = "completed"
    row.offset_amount = D("20.00")
    row.refund_amount = D("10.00")
    s._session.flush()
    with pytest.raises(AppError, match="不能改金额"):
        run(svc.update_return_amount(
            s, FINANCE, row, refund_amount=D("30.00")
        ))


def test_historical_offset_void_restores_plan_exactly_and_blocks_manual_edit(db):
    """历史抵扣单的作废仍按原快照恢复，不能被新流程改坏。"""
    s, order, item, batch_item, plans = db
    row = run(svc.create_return(s, USER, order, _payload("4", "40")))
    _approve(s, row)
    row.status = "completed"
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
    # 主管自己登记的售后，主管自己审核：允许，但审计要标 self_reviewed。
    row = run(svc.create_return(s, REVIEWER, order, _payload("3", "30", attachment_id=2)))
    assert row.status == "reviewing"
    assert row.created_by == REVIEWER.id

    # 业务员没有 order:assign，不能审核（即使这张不是他登记的）。
    with pytest.raises(AppError, match="只有主管"):
        run(svc.review_return(s, USER, row, decision="approve", note=None))

    approved = run(
        svc.review_return(
            s, REVIEWER, row, decision="approve", note="本人审核：已核对退货照片"
        )
    )
    assert approved.status == "warehouse_pending"
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
    row = run(svc.create_return(s, REVIEWER, order, _payload("3", "30", attachment_id=2)))
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


def test_finance_cannot_change_amount_even_if_less_or_full(db):
    s, order, item, batch_item, plans = db
    # 退款金额由业务员登记、主管审核；财务不能改成另一笔账。
    row = run(svc.create_return(s, USER, order, _payload("5", "50")))
    _approve(s, row)
    _warehouse(s, row)
    with pytest.raises(AppError, match="不能改金额"):
        run(svc.update_return_amount(
            s, FINANCE, row, refund_amount=D("30.00"),
            note="客户承担 20 元折损",
        ))
    with pytest.raises(AppError, match="不能改金额"):
        run(svc.update_return_amount(
            s, FINANCE, row, refund_amount=D("100.00"),
            note="客户投诉，整单全退",
        ))


def test_refund_attachment_metadata_serialized(db):
    """退款附件只存 file_id，序列化时回查文件中心的名字、大小和类型。"""
    s, order, item, batch_item, plans = db
    row = run(svc.create_return(s, USER, order, _payload("3", "30")))
    _approve(s, row)
    _warehouse(s, row)
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
    """整单售后全部自动抵扣：未付款归 0，状态已抵扣结清。"""
    s, order, item, batch_item, plans = db
    row = run(svc.create_return(s, USER, order, _payload("10", "100")))
    _approve(s, row)
    _warehouse(s, row)
    settled = run(
        svc.settle_return(
            s,
            FINANCE,
            row,
            note="客户还没付款，整单退款直接抵未付款",
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

    # 订单详情/列表也必须减掉售后抵扣；只改汇总接口会出现“汇总 0、详情还挂旧数”。
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

    # 已抵扣结清，不能再改金额、不能再作废。
    with pytest.raises(AppError, match="不能改金额"):
        run(svc.update_return_amount(s, FINANCE, row, refund_amount=D("90.00")))
    with pytest.raises(AppError, match="已经用抵扣"):
        run(svc.void_return(s, FINANCE, row, "想撤销"))


def test_settle_auto_offset_then_cash_requires_reference(db):
    """客户已付 80、欠 20，退 30：系统先抵 20，再现金退 10。"""
    s, order, item, batch_item, plans = db
    _add_payment(s, "80.00", [(1, "30.00"), (2, "50.00")])
    row = run(svc.create_return(s, USER, order, _payload("3", "30")))
    _approve(s, row)
    _warehouse(s, row)

    with pytest.raises(AppError, match="必须填写退款流水"):
        run(svc.settle_return(s, FINANCE, row))

    settled = run(
        svc.settle_return(
            s,
            FINANCE,
            row,
            refund_reference="银行流水 20261126001",
            note="系统先抵欠款，再退剩余现金",
        )
    )
    assert settled.status == "refunded"
    assert settled.total_amount == D("30.00")
    assert settled.offset_amount == D("20.00")
    assert settled.refund_amount == D("10.00")
    assert settled.refund_reference == "银行流水 20261126001"

    # 定金已收满，尾款 70 里自动抵 20，剩 50。
    assert plans[0].amount == D("30.00")
    assert plans[1].amount == D("50.00")

    summary = run(payment_svc.order_finance_summary(s, 1))
    assert summary["returned_amount_exact"] == "30.00"
    assert summary["return_offset_amount_exact"] == "20.00"
    assert summary["return_refunded_amount_exact"] == "10.00"
    assert summary["refund_pending_amount_exact"] == "0.00"
    assert summary["unreceived_amount_exact"] == "0.00"
    assert summary["net_received_amount_exact"] == "70.00"


def test_settle_auto_offset_caps_at_customer_unpaid(db):
    """客户只欠 10，退 30：只能抵 10，剩余 20 必须现金退。"""
    s, order, item, batch_item, plans = db
    _add_payment(s, "90.00", [(1, "30.00"), (2, "60.00")])
    row = run(svc.create_return(s, USER, order, _payload("3", "30")))
    _approve(s, row)
    _warehouse(s, row)

    settled = run(
        svc.settle_return(
            s,
            FINANCE,
            row,
            refund_reference="银行流水 20261126002",
        )
    )
    assert settled.status == "refunded"
    assert settled.offset_amount == D("10.00")
    assert settled.refund_amount == D("20.00")
    assert plans[0].amount == D("30.00")
    assert plans[1].amount == D("60.00")
    assert run(payment_svc.order_finance_summary(s, 1))["unreceived_amount_exact"] == "0.00"


def test_create_amount_breakdown_and_requires_application_attachment(db):
    s, order, item, batch_item, plans = db
    row = run(svc.create_return(
        s, USER, order,
        ReturnCreate(
            reason_code="quality_issue",
            reason="外箱破损，客户要求退货款并补运费",
            goods_refund_amount=D("30.00"),
            freight_refund_amount=D("8.00"),
            adjustment_amount=D("-3.00"),
            adjustment_note="改价：售价减包装费",
            payment_method="银行转账",
            receiving_account="测试账户 6222 0000 0000 0000",
            application_attachment_file_id=1,
            items=[ReturnItemInput(order_item_id=1, quantity=D("3"))],
        ),
    ))
    assert row.goods_refund_amount == D("30.00")
    assert row.freight_refund_amount == D("8.00")
    assert row.adjustment_amount == D("-3.00")
    assert row.adjustment_note == "改价：售价减包装费"
    assert row.total_amount == D("35.00")
    assert row.refund_amount == D("35.00")

    with pytest.raises(AppError, match="附件不存在"):
        run(svc.create_return(
            s, USER, order,
            ReturnCreate(
                reason_code="other",
                reason="附件不存在测试",
                goods_refund_amount=D("30.00"),
                payment_method="线下转账",
                receiving_account="测试账户 6222 0000 0000 0000",
                application_attachment_file_id=999,
                items=[ReturnItemInput(order_item_id=1, quantity=D("3"))],
            ),
        ))
