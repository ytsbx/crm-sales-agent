"""系统配置与业务规则。

06-需求澄清清单指出：文档里有 `customer-levels`、`numbering-rules`、`dictionaries`、
`settings`、`public-pool/rules` 这些接口，但 ER 里没有对应表。
这里用「一张通用配置表 + 两张规则表」把它补上，避免每加一个配置就加一张表。
"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, IdMixin, JSONType


class SystemSetting(Base, IdMixin):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(64), unique=True)
    value: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(), server_default=func.now(),
        nullable=False,
    )


class PublicPoolRule(Base, IdMixin):
    """公海回收规则：某等级客户多少天没跟进就回收。"""

    __tablename__ = "public_pool_rules"

    level: Mapped[str] = mapped_column(String(8))
    days: Mapped[int] = mapped_column(BigInteger, default=30)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)


class TaskRule(Base, IdMixin):
    """自动任务规则。

    trigger_type: quote_no_followup / customer_silent / receivable_due
    """

    __tablename__ = "task_rules"
    __table_args__ = (Index("ix_task_rules_code", "code", unique=True),)

    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))
    trigger_type: Mapped[str] = mapped_column(String(32))
    trigger_config: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    action_config: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")
