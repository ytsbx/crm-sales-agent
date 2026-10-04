"""报价单与报价版本。

状态机（收敛了 06-需求澄清清单第 1-5 条指出的重复枚举）：
  draft 草稿 → pending_approval 待审批 → approved 已通过 → sent 已发送
                                        ↘ approval_rejected 审批未通过
  sent → accepted 已接受 / declined 客户拒绝 / expired 已失效

版本内的 approval_status：not_submitted / pending / approved / rejected
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, IdMixin, TimestampMixin

QUOTE_STATUS_LABEL = {
    "draft": "草稿",
    "pending_approval": "待审批",
    "approved": "已通过",
    "approval_rejected": "审批未通过",
    "sent": "已发送",
    "accepted": "已接受",
    "declined": "客户拒绝",
    "expired": "已失效",
}


class Quote(Base, IdMixin, TimestampMixin):
    __tablename__ = "quotes"
    __table_args__ = (
        Index("ix_quotes_opportunity", "opportunity_id"),
        Index("ix_quotes_customer", "customer_id"),
    )

    quote_no: Mapped[str] = mapped_column(String(32), unique=True)
    opportunity_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    customer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("customers.id"))
    contact_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    owner_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="draft")
    current_version_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class QuoteVersion(Base, IdMixin):
    __tablename__ = "quote_versions"
    __table_args__ = (Index("ix_quote_versions_quote", "quote_id"),)

    quote_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("quotes.id"))
    version_no: Mapped[int] = mapped_column(BigInteger, default=1)
    subtotal_amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    charge_amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    # 外贸报价才用：报价时点的汇率快照（内贸留空）
    exchange_rate_snapshot: Mapped[Decimal | None] = mapped_column(Numeric(16, 6), nullable=True)
    exchange_rate_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    exchange_rate_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 贸易条款：内贸「含运费 / 不含运费」；外贸 FOB / CIF / EXW
    trade_terms: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payment_terms: Mapped[str | None] = mapped_column(String(128), nullable=True)
    delivery_terms: Mapped[str | None] = mapped_column(String(128), nullable=True)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    approval_status: Mapped[str] = mapped_column(String(24), default="not_submitted")
    approval_required: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    declined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class QuoteItem(Base, IdMixin):
    __tablename__ = "quote_items"
    __table_args__ = (Index("ix_quote_items_version", "quote_version_id"),)

    quote_version_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("quote_versions.id"))
    # 与商机需求明细的追溯关系（06-需求澄清清单第 2-4 条的缺口）
    opportunity_item_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # 定制项（文档场景09）：尚无正式 SKU 时也能报价——sku_id 为空，
    # 由 inquiry_id 指向定制需求，成本与价格靠人工核价填。
    # 非空约束在这里去掉是刻意的：定制件在打样投产前本来就没有 SKU 编码，
    # 强制先建 SKU 等于把"先报价接单、后建档"的真实流程堵死。
    sku_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("skus.id"), nullable=True
    )
    inquiry_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # 编号快照：需求被改名/归档后，这张报价仍要能说明"当时对着哪条需求报的价"
    inquiry_no_snapshot: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sku_code_snapshot: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sku_name_snapshot: Mapped[str | None] = mapped_column(String(200), nullable=True)
    spec_snapshot: Mapped[str | None] = mapped_column(String(200), nullable=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(16, 3), default=1)
    cost_snapshot: Mapped[Decimal] = mapped_column(Numeric(16, 4), default=0)
    package_cost_snapshot: Mapped[Decimal] = mapped_column(Numeric(16, 4), default=0)
    logistics_cost_snapshot: Mapped[Decimal] = mapped_column(Numeric(16, 4), default=0)
    standard_price_snapshot: Mapped[Decimal | None] = mapped_column(Numeric(16, 4), nullable=True)
    recommended_price_snapshot: Mapped[Decimal | None] = mapped_column(Numeric(16, 4), nullable=True)
    minimum_price_snapshot: Mapped[Decimal | None] = mapped_column(Numeric(16, 4), nullable=True)
    # A09：拟报价来源快照（customer_specific/level/general）与当时客户等级——
    # 事后要能回答"这一版当初按哪条规则带的价"
    # 不能只有 16：取价来源写成 `customer_specific`（17 字符）时会
    # "value too long for character varying(16)" —— 有专属价的客户一建明细就 500。
    price_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    customer_level_snapshot: Mapped[str | None] = mapped_column(String(8), nullable=True)
    quoted_price: Mapped[Decimal] = mapped_column(Numeric(16, 4), default=0)
    profit_snapshot: Mapped[Decimal] = mapped_column(Numeric(16, 4), default=0)
    profit_rate_snapshot: Mapped[Decimal] = mapped_column(Numeric(10, 6), default=0)
    # 出口退税额（单件）与退税后利润：内贸恒为 0
    tax_refund_snapshot: Mapped[Decimal] = mapped_column(
        Numeric(16, 4), default=0, server_default="0"
    )
    profit_with_refund_snapshot: Mapped[Decimal] = mapped_column(
        Numeric(16, 4), default=0, server_default="0"
    )
    approval_required: Mapped[bool] = mapped_column(Boolean, default=False)
    approval_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)


class QuoteCharge(Base, IdMixin):
    __tablename__ = "quote_charges"

    quote_version_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("quote_versions.id"))
    charge_type: Mapped[str] = mapped_column(String(32), default="other")
    description: Mapped[str | None] = mapped_column(String(128), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    is_discount: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_no: Mapped[int] = mapped_column(BigInteger, default=0)


class QuoteSendLog(Base, IdMixin):
    __tablename__ = "quote_send_logs"

    quote_version_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("quote_versions.id"))
    channel: Mapped[str] = mapped_column(String(32), default="手动标记")
    receiver: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sent_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="success")
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    error_message: Mapped[str | None] = mapped_column(String(255), nullable=True)
