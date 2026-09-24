"""线索。

线索必须独立存在：它是进入 CRM 的原始销售线索，不能直接跳到客户。
状态：pending 待分配 / assigned 已分配 / following 跟进中 / converted 已转客户 / invalid 无效。
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, IdMixin, TimestampMixin


class Lead(Base, IdMixin, TimestampMixin):
    __tablename__ = "leads"
    __table_args__ = (Index("ix_leads_owner_status", "owner_id", "status"),)

    name: Mapped[str] = mapped_column(String(200))
    company_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    contact_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mobile: Mapped[str | None] = mapped_column(String(32), nullable=True)
    email: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_detail: Mapped[str | None] = mapped_column(String(255), nullable=True)
    country: Mapped[str | None] = mapped_column(String(64), default="中国")
    region: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    owner_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    converted_customer_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    converted_contact_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    converted_opportunity_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    invalid_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_followup_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LeadAssignment(Base, IdMixin):
    __tablename__ = "lead_assignments"

    lead_id: Mapped[int] = mapped_column(BigInteger)
    from_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    to_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    operator_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
