"""应收计划与实际回款。

设计要点（总设计文档 §5.13）：一个应收节点可以对应多笔实际回款，
所以两者必须分开建表，不能把"已收金额"直接写在应收计划上覆盖历史。
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Index, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, IdMixin

PLAN_STATUS_LABEL = {
    "pending": "待回款",
    "partial": "部分回款",
    "paid": "已回款",
    "overdue": "已逾期",
    "cancelled": "已取消（随订单）",
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


class PaymentAllocation(Base, IdMixin):
    """一笔记账**分配到哪几期、各多少**（第十批主人 2026-10-13 拍板）。

    ## 为什么需要单独一张表

    原来 `payment_records.receivable_plan_id` 是**单个**外键：一笔回款只能挂一期。
    实测（两期各 100/200，给第一期登记并确认 200）：超出的 100 留在第一期，
    第二期仍是 `overdue` —— 既不能自动抵后续期，也没有"改挂期"的入口。

    主人定的口径：
      · **一笔银行流水仍然是一行** `payment_records`（审核好解释，
        不把一个 200 拆成两条回款）；
      · 超出该期应收的部分**按到期日从早到晚**自动抵后续期；
      · 金额必须**实际分配到某一期**才算该期收齐 —— 不能因为"订单总额够了"
        就把所有期都标完成（那是第十批审查 ② 的毛病）；
      · 分配可以人工调整，但**必须留审计**（原期/新期/金额/操作人/时间），
        不允许系统静默改写（主人：不能擅自调整已登记的收款归属）。

    ## 与 `payment_records.receivable_plan_id` 的关系

    保留那个列不动：它记的是**登记人当初选的那一期**（用户意图），
    是"把 200 打到第一期"这个人做的动作。分配表记的是**这笔钱实际覆盖了哪些期**。
    两者在常见情形下一致；只有超收时才会不一致 —— 那正是需要能分辨的地方。

    ## 历史数据

    迁移会给每条**已确认**的老回款按其 `receivable_plan_id` 回填一条**全额**分配，
    因此各期"已收"金额与迁移前完全一致（语义不变、对账口径不变）。
    `receivable_plan_id` 为空的老回款不分配（本来就挂不到期）。
    """

    __tablename__ = "payment_allocations"
    __table_args__ = (
        Index("ix_payment_allocations_payment", "payment_id"),
        Index("ix_payment_allocations_plan", "plan_id"),
        # 同一笔回款对同一期只允许一条分配：人工调整是**改金额**（或删掉再建），
        # 不是给同一对再插一条 —— 否则"这一期到底分到多少"要读多条再求和，
        # 对账页面容易漏。
        UniqueConstraint("payment_id", "plan_id", name="uq_payment_allocations_pair"),
    )

    # ⚠️ `ondelete="CASCADE"`：回款被删时它的分配一起走（第十批实测踩到）。
    # 没有它，删回款会撞 `payment_allocations_payment_id_fkey` —— 清理/作废路径
    # 直接 IntegrityError。分配脱离回款没有任何意义（它记的就是"这笔钱分到哪"）。
    payment_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("payment_records.id", ondelete="CASCADE"), nullable=False
    )
    plan_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("receivable_plans.id"), nullable=False
    )
    #: 这一笔回款分给这一期的金额（正数）。`Numeric(16,2)` 与回款金额同精度。
    amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)
    #: `auto` = 确认回款时按到期日自动分配；`manual` = 人工调整过。
    #: 留这个字段是为了让"系统自动分的"与"人改过的"在页面上能分辨，
    #: 也让审计能回答"这条分配是谁定的"。
    source: Mapped[str] = mapped_column(String(16), default="auto")
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PaymentRecord(Base, IdMixin):
    __tablename__ = "payment_records"
    __table_args__ = (
        Index("ix_payment_records_plan", "receivable_plan_id"),
        Index("ix_payment_records_order", "order_id"),
    )

    receivable_plan_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sales_orders.id"))
    # legacy：历史隐含归属；auto：系统分配；manual：完整人工决定（包括空分配）。
    # 必须独立于分配行保存，否则“人工清空”和“从未分配”无法区分。
    allocation_mode: Mapped[str] = mapped_column(String(16), default="legacy", server_default="legacy")
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
