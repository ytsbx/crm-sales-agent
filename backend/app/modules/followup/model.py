"""跟进记录。

分工（总设计文档 §5.11）：
- FollowUp = 已经发生的销售行为；
- Task = 未来需要执行的动作。
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, IdMixin


class FollowUp(Base, IdMixin):
    __tablename__ = "followups"
    __table_args__ = (
        UniqueConstraint("owner_id", "request_key", name="uq_followup_owner_request"),
        Index("ix_followups_customer_created", "customer_id", "created_at"),
        Index("ix_followups_opportunity", "opportunity_id"),
    )

    customer_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    contact_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    lead_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    opportunity_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    quote_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    order_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sample_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    owner_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    followup_type: Mapped[str] = mapped_column(String(32), default="电话")
    content: Mapped[str] = mapped_column(Text)
    customer_feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_action: Mapped[str | None] = mapped_column(String(255), nullable=True)
    planned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exemption_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    next_task_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    request_key: Mapped[str | None] = mapped_column(String(96), nullable=True)
    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
