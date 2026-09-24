"""任务：未来需要执行的动作。"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, IdMixin, TimestampMixin


class Task(Base, IdMixin, TimestampMixin):
    __tablename__ = "tasks"
    __table_args__ = (Index("ix_tasks_owner_status_due", "owner_id", "status", "due_at"),)

    title: Mapped[str] = mapped_column(String(200))
    task_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    customer_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    contact_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    lead_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    opportunity_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    quote_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    order_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    owner_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    priority: Mapped[str] = mapped_column(String(16), default="normal")  # high/normal/low
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/doing/done/cancelled
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="manual")  # manual/system/agent
    source_rule_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completion_note: Mapped[str | None] = mapped_column(Text, nullable=True)
