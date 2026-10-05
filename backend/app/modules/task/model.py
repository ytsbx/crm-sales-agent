"""任务：未来需要执行的动作。"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, IdMixin, TimestampMixin


class Task(Base, IdMixin, TimestampMixin):
    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_tasks_owner_status_due", "owner_id", "status", "due_at"),
        # 自动待办的**去重身份键**（人工建的任务不写它，所以用部分索引）。
        # 「同一业务来源 + 同一到期周期只能有一张」——**不论它当前什么状态**：
        # 已完成/已取消也算占位。不这么做的话，任务一被完成，下次扫描找不到它，
        # 又会新建一条同样的待办，天天冒出来（这正是外部审查第 7 条指出的问题）。
        Index(
            "uq_tasks_source_key",
            "source_key",
            unique=True,
            postgresql_where=text("source_key IS NOT NULL"),
        ),
    )

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
    # 自动待办指向的**业务对象**：让"这条待办是从哪来的"可跳转。
    # 此前系统待办只有 source='system' 和一段标题文字，点进去不知道要处理哪一份协议。
    source_business_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_business_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # 去重身份键，形如 `contract:monthly:{文档id}:{到期日}`。
    # **去重靠它、不靠标题**：标题是写给人看的，改一个字就会重来一条待办；
    # 到期日也在键里，所以续签换了到期日就是另一个周期，可以另建一张。
    source_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completion_note: Mapped[str | None] = mapped_column(Text, nullable=True)
