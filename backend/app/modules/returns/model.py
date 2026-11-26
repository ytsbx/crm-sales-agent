"""销售退货：发货之后的退货登记、主管审核、仓库确认与财务退款。

业务口径（2026-11-26 主人按分销商售后 OA 表单重新拍板）：
- 售后申请必须挂订单，业务员登记时填退款金额构成（货款 / 运费 / 改价或其他调整），
  系统只负责合计，不再让业务员只填一个说不清来源的总数；
- 申请单必须附「分销商退款登记表」，主管审核通过后先进入待仓库确认收货；
- 仓库确认货已退回后，财务才处理退款；
- 财务不手填抵扣、不改金额：系统按订单当前客户未付款自动先冲，剩余才现金退；
- 审核前不冲欠款，只有财务确认退款时才写应收冲减快照。

三张表：
- `sales_returns`：售后申请单头，状态与金额；
- `sales_return_items`：按订单明细（产品/规格）记录退多少；新单批次字段为空，历史单保留原值；
- `sales_return_plan_adjustments`：记录每一期应收被退货自动抵扣多少，作废时精确还原。
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
    "reviewing": "待主管审核",
    #: 兼容历史旧单：金额还没填。新售后在登记时已填金额，不会落到这个状态。
    "pending": "待填退款总额（历史单）",
    "warehouse_pending": "待仓库确认收货",
    "completed": "待财务退款",
    "refunded": "已退款",
    "settled": "已抵扣结清",
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
    #: 售后状态：reviewing / pending（仅历史旧单）/ warehouse_pending / completed /
    #: refunded / settled / cancelled / voided。
    #: reviewing = 已提交申请，等主管审核；
    #: warehouse_pending = 主管已通过，等仓库确认收到退货；
    #: completed = 仓库已收货，等财务退款；
    #: refunded = 已退款（可能含自动抵扣 + 现金退还）；
    #: settled = 退款金额全部自动抵扣客户未付款，没有现金退给客户。
    status: Mapped[str] = mapped_column(String(32), default="reviewing")
    reason_code: Mapped[str] = mapped_column(String(32), default="other")
    reason: Mapped[str] = mapped_column(String(255))
    return_date: Mapped[date] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")

    #: 登记时的参考货款（按订单明细单价折算），仅供业务员/主管参考，不是最终账。
    goods_amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    #: 货款退款：业务员在申请单里填，默认带出参考货款，可按实际协商金额改。
    goods_refund_amount: Mapped[Decimal] = mapped_column(
        Numeric(16, 2), default=0
    )
    #: 本次退款总额（兼容历史字段，仍等于 goods_refund + freight_refund + adjustment）。
    settled_goods_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(16, 2), nullable=True
    )
    #: 运费退款：业务员填，0 表示不退运费。
    freight_refund_amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    #: 改价/其他调整：可正可负，用来解释“为什么和按单价算出来的不一样”。
    adjustment_amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    adjustment_note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: 本次退款总额 = goods_refund_amount + freight_refund_amount + adjustment_amount。
    total_amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)

    #: 打款方式（线下转账/银行转账等）。财务只按这个渠道退款，不在系统里选抵扣。
    payment_method: Mapped[str] = mapped_column(String(32), default="线下转账")
    #: 收款账户，业务员填户名/开户行/账号；财务按它打款。
    receiving_account: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: 申请时上传的《分销商退款登记表》附件，文件中心 file_id。
    application_attachment_file_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    #: 客户退回货物的物流单号，便于仓库核对。
    return_tracking_no: Mapped[str | None] = mapped_column(String(64), nullable=True)

    #: 财务确认退款时，系统自动抵扣客户未付款的金额；不是人工填的。
    offset_amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    #: 现金退还给客户的金额 = total_amount - offset_amount。
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
    #: 仓库确认收到退货：谁确认、什么时候、备注。主管/管理员可代仓库确认。
    warehouse_confirmed_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    warehouse_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    warehouse_note: Mapped[str | None] = mapped_column(String(255), nullable=True)
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
