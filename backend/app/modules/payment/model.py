"""应收计划与实际回款。

设计要点（总设计文档 §5.13）：一个应收节点可以对应多笔实际回款，
所以两者必须分开建表，不能把"已收金额"直接写在应收计划上覆盖历史。
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, IdMixin

PLAN_STATUS_LABEL = {
    "pending": "待回款",
    "partial": "部分回款",
    "paid": "已回款",
    "overdue": "已逾期",
}

PAYMENT_STATUS_LABEL = {
    "pending": "待财务确认",
    "confirmed": "已确认",
    "rejected": "已驳回",
}


class ReceivablePlan(Base, IdMixin):
    __tablename__ = "receivable_plans"
    __table_args__ = (Index("ix_receivable_plans_order_status", "order_id", "status"),)

    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sales_orders.id"))
    plan_name: Mapped[str] = mapped_column(String(64))
    due_date: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    status: Mapped[str] = mapped_column(String(16), default="pending")
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PaymentRecord(Base, IdMixin):
    __tablename__ = "payment_records"
    __table_args__ = (
        Index("ix_payment_records_plan", "receivable_plan_id"),
        Index("ix_payment_records_order", "order_id"),
    )

    receivable_plan_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sales_orders.id"))
    received_date: Mapped[date] = mapped_column(Date)
    received_amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    payment_method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    voucher_file_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    voucher_note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    confirmed_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
