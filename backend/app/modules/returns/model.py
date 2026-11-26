"""销售退货：发货之后的退货登记与财务核销。

业务口径（2026-11-25 主人拍板 A 方案，2026-11-26 两次调整）：
- 只针对**已实发**的订单明细登记，发货批次仅用于汇总已发数量，原发货事实不动，不碰库存；
- 登记时必须填本次退款金额（含货款和运费）；主管复核后直接进入待财务登记退款；
- 财务可以核对/修改退款金额，但每次改动都要写审计；
- 系统**不再自动抵扣客户未付款**：退款金额单独记录，应收计划不因新退货被冲减；
- 财务确认后未退款可作废；退款已实际支付的不能作废（钱已经出去了，
  系统不能假装没发生）。

三张表：
- `sales_returns`：退货单头，状态与金额；
- `sales_return_items`：按订单明细（产品/规格）记录退多少；新单批次字段为空，历史单保留原值；
- `sales_return_plan_adjustments`：**仅历史抵扣单使用**，记录当时每一期应收被冲减多少，
  用于作废时**精确还原**。2026-11-26 起新退货不再生成抵扣，也不再写这张表。
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
    #: 兼容历史旧单：金额还没填。新退货在登记时已填金额，不会落到这个状态。
    "pending": "待填退款金额（历史单）",
    "completed": "待登记退款",
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
    #: 退货单状态：reviewing / pending（仅历史旧单）/ completed / refunded / cancelled / voided。
    #: reviewing = 已登记退款金额，等主管复核；
    #: completed = 主管已通过，等财务登记实际退款；财务可在这时核对/修改金额。
    #: refunded = 退款已支付（refund_amount=0 时不会走到这里）。
    status: Mapped[str] = mapped_column(String(16), default="reviewing")
    reason_code: Mapped[str] = mapped_column(String(32), default="other")
    reason: Mapped[str] = mapped_column(String(255))
    return_date: Mapped[date] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")

    #: 登记时的参考货款（按订单明细单价折算），仅供主管/财务参考，不是最终账。
    goods_amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    #: 本次退款金额（含货款和运费）。登记时必填；财务有权修改，改动写审计。
    settled_goods_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(16, 2), nullable=True
    )
    #: 历史兼容字段：新流程固定 0，退款金额不再拆运费。
    freight_refund_amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    #: 本单应退总额 = settled_goods_amount + freight_refund_amount（后者新流程为 0）。
    total_amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)

    #: 历史抵扣字段：2026-11-26 起新退货固定 0，不再自动冲减客户未付款。
    #: 历史单保留原值，作废时按 `sales_return_plan_adjustments` 精确还原。
    offset_amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    #: 应退给客户的金额：新流程 = total_amount；历史抵扣单 = total_amount - offset_amount。
    refund_amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    #: 退款实际支付后的银行流水/备注，财务填。
    refund_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: 财务登记实际退款时上传的凭证附件（银行回单/转账截图/客户签收单等）。
    #: 只存文件中心的 file_id，文件元信息和磁盘位置由 `files` 表管理。
    refund_attachment_file_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
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
    #: 历史字段：旧版按发货批次登记退货时，记录具体批次明细。
    #: 2026-11-26 起退货只按订单明细（产品/规格）汇总，不再关联批次；
    #: 新退货单这两个字段为 NULL，历史单保留原值供追溯。
    shipment_batch_item_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("order_shipment_batch_items.id"), nullable=True
    )
    batch_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("order_shipment_batches.id"), nullable=True
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
