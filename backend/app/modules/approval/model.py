"""审批定义、审批实例与审批记录。"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, IdMixin, JSONType


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
