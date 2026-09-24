"""Agent 数据模型。

设计要点（05-TECH §2.2）：Agent 不允许直接操作数据库，
所有写动作必须经过 Action Gateway → 风险评估 → 权限校验 → 业务 Service。
因此这里只存"它想做什么"（AgentAction）和"实际做成了什么"（AgentExecution）。
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, IdMixin, JSONType, TimestampMixin

ACTION_STATUS_LABEL = {
    "proposed": "已提议",
    "awaiting_confirmation": "待用户确认",
    "approval_required": "需审批",
    "executed": "已执行",
    "rejected": "已拒绝",
    "failed": "执行失败",
}

RISK_LABEL = {"L1": "自动执行", "L2": "确认后执行", "L3": "审批后执行"}


class AgentSession(Base, IdMixin, TimestampMixin):
    __tablename__ = "agent_sessions"
    __table_args__ = (Index("ix_agent_sessions_user", "user_id"),)

    user_id: Mapped[int] = mapped_column(BigInteger)
    title: Mapped[str] = mapped_column(String(200), default="新会话")
    context_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    context_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class AgentMessage(Base, IdMixin):
    __tablename__ = "agent_messages"
    __table_args__ = (Index("ix_agent_messages_session", "session_id"),)

    session_id: Mapped[int] = mapped_column(BigInteger)
    role: Mapped[str] = mapped_column(String(16))  # user / assistant / tool
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(), server_default=func.now(),
        nullable=False,
    )


class AgentAction(Base, IdMixin):
    __tablename__ = "agent_actions"
    __table_args__ = (Index("ix_agent_actions_session", "session_id"),)

    session_id: Mapped[int] = mapped_column(BigInteger)
    action_type: Mapped[str] = mapped_column(String(64))
    tool_name: Mapped[str] = mapped_column(String(64))
    risk_level: Mapped[str] = mapped_column(String(4))
    business_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    business_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    title: Mapped[str] = mapped_column(String(200))
    proposed_payload: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="awaiting_confirmation")
    result: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    confirmed_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(), server_default=func.now(),
        nullable=False,
    )


class AgentExecution(Base, IdMixin):
    __tablename__ = "agent_executions"
    __table_args__ = (Index("ix_agent_executions_session", "session_id"),)

    session_id: Mapped[int] = mapped_column(BigInteger)
    action_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    tool_name: Mapped[str] = mapped_column(String(64))
    risk_level: Mapped[str] = mapped_column(String(4))
    input_payload: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    output_payload: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="success")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
