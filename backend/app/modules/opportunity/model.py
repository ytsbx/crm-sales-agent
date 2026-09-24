"""商机、商机需求明细、阶段与失单原因。

原则（总设计文档 §5.7）：商机需求必须落在 OpportunityItem，禁止把多个 SKU
用逗号字符串或 JSON 塞进商机本身——否则后面的核价与报价无法逐条对应。
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, IdMixin, TimestampMixin


class OpportunityStage(Base, IdMixin):
    __tablename__ = "opportunity_stages"

    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(64))
    sequence: Mapped[int] = mapped_column(BigInteger, default=0)
    is_win: Mapped[bool] = mapped_column(default=False)
    is_loss: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(32), default="active")


class Opportunity(Base, IdMixin, TimestampMixin):
    __tablename__ = "opportunities"
    __table_args__ = (
        Index("ix_opportunities_customer_status", "customer_id", "status"),
        Index("ix_opportunities_owner_stage", "owner_id", "stage_id"),
    )

    customer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("customers.id"))
    primary_contact_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    title: Mapped[str] = mapped_column(String(200))
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    stage_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("opportunity_stages.id"))
    expected_amount: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    expected_close_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    owner_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    competitor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    risk_level: Mapped[str | None] = mapped_column(String(16), nullable=True)  # high/medium/low
    next_action: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open/win/loss
    win_quote_version_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    loss_reason_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    loss_remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    reopen_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OpportunityStageHistory(Base, IdMixin):
    __tablename__ = "opportunity_stage_history"
    __table_args__ = (Index("ix_stage_history_opportunity", "opportunity_id"),)

    opportunity_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("opportunities.id"))
    from_stage_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    to_stage_id: Mapped[int] = mapped_column(BigInteger)
    operator_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class OpportunityItem(Base, IdMixin, TimestampMixin):
    __tablename__ = "opportunity_items"
    __table_args__ = (Index("ix_opportunity_items_opportunity", "opportunity_id"),)

    opportunity_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("opportunities.id"))
    sku_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("skus.id"))
    quantity: Mapped[Decimal] = mapped_column(Numeric(16, 3), default=1)
    target_price: Mapped[Decimal | None] = mapped_column(Numeric(16, 4), nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    specification: Mapped[str | None] = mapped_column(String(200), nullable=True)
    color: Mapped[str | None] = mapped_column(String(64), nullable=True)
    package_requirement: Mapped[str | None] = mapped_column(String(128), nullable=True)
    delivery_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    destination: Mapped[str | None] = mapped_column(String(128), nullable=True)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)


class LossReason(Base, IdMixin):
    __tablename__ = "loss_reasons"

    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(64))
    category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active")
