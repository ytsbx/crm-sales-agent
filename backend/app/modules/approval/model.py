"""审批定义、审批实例与审批记录。"""

from datetime import UTC, datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, IdMixin, JSONType, TimestampMixin


class ApprovalDefinition(Base, IdMixin):
    __tablename__ = "approval_definitions"

    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    business_type: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="active")
    config_json: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ApprovalInstance(Base, IdMixin):
    __tablename__ = "approval_instances"
    __table_args__ = (Index("ix_approval_instances_business", "business_type", "business_id"),)

    definition_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("approval_definitions.id"))
    business_type: Mapped[str] = mapped_column(String(64))
    business_id: Mapped[int] = mapped_column(BigInteger)
    applicant_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/approved/rejected/withdrawn
    current_node: Mapped[str | None] = mapped_column(String(32), nullable=True)
    summary: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ApprovalRecord(Base, IdMixin):
    __tablename__ = "approval_records"
    __table_args__ = (Index("ix_approval_records_instance", "approval_instance_id"),)

    approval_instance_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("approval_instances.id"))
    node_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    approver_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    action: Mapped[str] = mapped_column(String(16))  # approve/reject/transfer/withdraw/submit
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ApprovalRule(Base, IdMixin, TimestampMixin):
    """审批流转规则（设计稿 _6 的国内业务版）。

    kind 三类：
      auto_pass       免审——条件全部命中，提交即通过；
      express         极速通道——条件全部命中，跳过金额分档的高层级，一律由第一级审批；
      exception_route 异常加签——条件全部命中，正常分档之外追加一个会签节点（一票否决）。

    `conditions` / `action` 等是**草稿**，改完要「发布」生成版本快照才生效；
    引擎永远按最新已发布版本求值。`enabled` 是运维开关，立即生效。
    """

    __tablename__ = "approval_rules"

    name: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(24))  # auto_pass/express/exception_route
    priority: Mapped[int] = mapped_column(BigInteger, default=100)  # 小者先求值
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    conditions: Mapped[list] = mapped_column(JSONType, default=list)  # [{field,op,value}]
    action: Mapped[dict] = mapped_column(JSONType, default=dict)  # kind 相关的动作参数
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    published_version_no: Mapped[int] = mapped_column(BigInteger, default=0)  # 0=从未发布
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ApprovalRuleVersion(Base, IdMixin):
    """规则发布快照：审批留痕要能回答"这单当时按哪版规则走的"。"""

    __tablename__ = "approval_rule_versions"
    __table_args__ = (
        Index("ix_approval_rule_versions_rule", "rule_id"),
    )

    rule_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("approval_rules.id"))
    version_no: Mapped[int] = mapped_column(BigInteger)
    payload: Mapped[dict] = mapped_column(JSONType)
    published_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # 同时给 Python 侧与数据库侧默认值：只用 server_default 会在异步会话里触发
    # flush 后回查（MissingGreenlet），见 core/base.py TimestampMixin 的说明。
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )
