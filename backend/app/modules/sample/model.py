"""样品（PRD §19、02-ER §13）。

三张表对应 ER §13：申请单 / 明细 / 寄样记录。
状态机按 03-API §26 的接口语义推（文档没写明状态名，不自行发明）：
    pending → approved → shipped → signed
另有 rejected（approve 接口可拒）。

为什么要独立模块而不是塞进商机：PRD §9.2 的商机阶段里本来就有「样品」阶段，
但只有阶段没有实体，业务走到那里没有任何东西可录入。这个模块补的就是它。
"""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, IdMixin

SAMPLE_STATUS_LABEL: dict[str, str] = {
    "pending": "待审批",
    "approved": "已批准",
    "rejected": "已拒绝",
    "shipped": "已寄样",
    "signed": "已签收",
}

# 允许的状态流转。写死成一张表，避免各处 if 判断漂移。
SAMPLE_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"approved", "rejected"},
    "approved": {"shipped"},
    "rejected": set(),
    "shipped": {"signed"},
    "signed": set(),
}


class SampleRequest(Base, IdMixin):
    __tablename__ = "sample_requests"
    __table_args__ = (
        Index("ix_sample_requests_opportunity", "opportunity_id"),
        Index("ix_sample_requests_customer", "customer_id"),
        Index("ix_sample_requests_owner_status", "owner_id", "status"),
    )

    opportunity_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("opportunities.id"), nullable=True
    )
    customer_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("customers.id"), nullable=True
    )
    contact_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    owner_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class SampleItem(Base, IdMixin):
    __tablename__ = "sample_items"
    __table_args__ = (Index("ix_sample_items_request", "sample_request_id"),)

    sample_request_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sample_requests.id")
    )
    # 定制项（文档场景09）：尚无正式 SKU 时也能打样——sku_id 为空，
    # 由需求编号说明"打的是哪条需求"。定制件本来就要先打样再定 SKU，
    # 强制先建档等于把这个顺序反过来。
    sku_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("skus.id"), nullable=True
    )
    inquiry_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    inquiry_no_snapshot: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # 定制项的展示名（没有 SKU 名称可用）
    item_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(16, 3), default=1)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)


class SampleShipment(Base, IdMixin):
    __tablename__ = "sample_shipments"
    __table_args__ = (Index("ix_sample_shipments_request", "sample_request_id"),)

    sample_request_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sample_requests.id")
    )
    carrier: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tracking_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    shipping_fee: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
