"""客户与联系人（对齐 02-ER §5）。

相对文档的补充：
- `customers.pool_status`：区分"私海 / 公海"，避免只靠 owner_id IS NULL 判断
  （06-需求澄清清单第 1-8 条的缺口）；
- `customers.level` 用 A/B/C/D，等级定义待业务确认。
"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, IdMixin, TimestampMixin


class Customer(Base, IdMixin, TimestampMixin):
    __tablename__ = "customers"
    __table_args__ = (
        Index("ix_customers_owner_status", "owner_id", "status"),
        Index("ix_customers_name", "name"),
    )

    name: Mapped[str] = mapped_column(String(200))
    short_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    customer_type: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 企业 / 个人
    country: Mapped[str | None] = mapped_column(String(64), default="中国")
    region: Mapped[str | None] = mapped_column(String(64), nullable=True)
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    domain: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tax_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    level: Mapped[str | None] = mapped_column(String(8), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active")  # active / lost / disabled
    pool_status: Mapped[str] = mapped_column(String(16), default="private")  # private / public
    owner_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=True
    )
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_followup_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_followup_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Contact(Base, IdMixin, TimestampMixin):
    __tablename__ = "contacts"
    __table_args__ = (Index("ix_contacts_customer", "customer_id"),)

    customer_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("customers.id"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(64))
    title: Mapped[str | None] = mapped_column(String(64), nullable=True)
    department: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mobile: Mapped[str | None] = mapped_column(String(32), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    email: Mapped[str | None] = mapped_column(String(128), nullable=True)
    wechat: Mapped[str | None] = mapped_column(String(64), nullable=True)
    external_userid: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    owner_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CustomerOwnerHistory(Base, IdMixin):
    __tablename__ = "customer_owner_history"

    customer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("customers.id"))
    old_owner_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    new_owner_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    operator_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
