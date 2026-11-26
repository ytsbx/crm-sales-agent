"""销售退货：发货之后的退货登记与财务核销。

业务口径（2026-11-25 主人拍板 A 方案）：
- 只针对**已实发**的批次明细登记，原发货事实不动，不碰库存；
- 登记时只记数量与原因，不直接改应收/回款；
- 业务员/主管确认本次退货退款总额后，先冲抵客户未付款，剩余部分转为应退还客户；
- 财务确认后可作废并冲回，但退款已实际支付的不能作废（钱已经出去了，
  系统不能假装没发生）。

三张表：
- `sales_returns`：退货单头，状态与金额；
- `sales_return_items`：按发货批次明细记录退多少；
- `sales_return_plan_adjustments`：确认退款总额时每一期应收被冲减了多少，
  用于作废时**精确还原**，不靠“再算一遍”猜。
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, IdMixin, TimestampMixin

RETURN_STATUS_LABEL = {
    "reviewing": "待主管复核",
    "pending": "待填退款金额",
    "completed": "已处理",
    "refunded": "已退款",
    "rejected": "主管驳回",
    "cancelled": "已取消",
    "voided": "已作废",
}

RETURN_REASON_LABEL = {
    "customer_rejected": "客户拒收",
    "quality_issue": "质量问题",
    "wrong_item": "发错货",
    "other": "其他",
}


class SalesReturn(Base, IdMixin, TimestampMixin):
    __tablename__ = "sales_returns"
    __table_args__ = (
        Index("ix_sales_returns_order", "order_id"),
        Index("ix_sales_returns_status", "status"),
    )

    return_no: Mapped[str] = mapped_column(String(32), unique=True)
    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sales_orders.id"))
    #: 退货单状态：pending / completed / refunded / cancelled / voided。
    #: completed = 财务已冲账，若 refund_amount > 0 还等着实际退款；
    #: refunded = 退款已支付（refund_amount=0 时不会走到这里）。
    status: Mapped[str] = mapped_column(String(16), default="pending")
    reason_code: Mapped[str] = mapped_column(String(32), default="other")
    reason: Mapped[str] = mapped_column(String(255))
    return_date: Mapped[date] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")

    #: 登记时的参考货款（按订单明细单价折算），仅供主管/财务参考，不是最终账。
    goods_amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    #: 人工填写的最终退款总额（可含货款和运费）；空 = 还没确认。
    settled_goods_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(16, 2), nullable=True
    )
    #: 历史兼容字段：新流程由前端传 0，运费并入 `settled_goods_amount`，不再单列。
    freight_refund_amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    #: 本单应退总额 = settled_goods_amount + freight_refund_amount（后者新流程为 0）。
    total_amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)

    #: 确认退款总额时实际冲减的未收应收。冲减后各期 plan.amount 会同步变小。
    offset_amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    #: 应退给客户的现金 = total_amount - offset_amount。
    refund_amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    #: 退款实际支付后的银行流水/备注，财务填。
    refund_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    settlement_note: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reviewed_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    settled_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    refunded_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    voided_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    void_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)


class SalesReturnItem(Base, IdMixin):
    __tablename__ = "sales_return_items"
    __table_args__ = (
        Index("ix_sales_return_items_return", "return_id"),
        UniqueConstraint(
            "return_id", "shipment_batch_item_id", name="uq_return_batch_item"
        ),
    )

    return_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sales_returns.id", ondelete="CASCADE")
    )
    order_item_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sales_order_items.id")
    )
    #: 退的是哪一条实发明细。同一订单明细分批发货时，必须精确到批次，
    #: 否则“已发 1000、退 3”会被算成整单退 3，批次台账对不上。
    shipment_batch_item_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("order_shipment_batch_items.id")
    )
    batch_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("order_shipment_batches.id")
    )
    sku_snapshot: Mapped[str | None] = mapped_column(String(200), nullable=True)
    specification_snapshot: Mapped[str | None] = mapped_column(String(200), nullable=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(16, 3), default=0)
    #: 退货单价（按订单明细金额/数量折算，保留 4 位）。
    unit_price: Mapped[Decimal] = mapped_column(Numeric(16, 4), default=0)
    amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)


class SalesReturnPlanAdjustment(Base, IdMixin):
    """退货冲减应收的逐期快照。

    `amount_before/after` 是**处理那一刻**的值；作废时用这一期的原始金额
    减去所有仍生效退货的冲减额，核对 plan 是否仍是这个值，确认没人手工改过，
    再把本单的 `applied_amount` 加回去。这样同一期被多张退货先后冲减时，
    作废前一张也不会把后一张的冲减抹掉。
    """

    __tablename__ = "sales_return_plan_adjustments"
    __table_args__ = (
        Index("ix_sales_return_adjustments_return", "return_id"),
        UniqueConstraint("return_id", "plan_id", name="uq_return_plan_adjustment"),
    )

    return_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sales_returns.id", ondelete="CASCADE")
    )
    plan_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("receivable_plans.id")
    )
    amount_before: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    applied_amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    amount_after: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    status_before: Mapped[str] = mapped_column(String(16), default="pending")
