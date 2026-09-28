"""定制询价库（领导模块③：产品知识库 · 定制询价类）。

沉淀"客户问了但我们还没有标准产品"的询价——这是找开发方向的原料：
- 每一条 = 一个真实的定制需求信号（谁问的、要多少、能接受什么价）；
- 状态机：open（待评估）→ developing（已立项开发）→ converted（已转商机/报价），
  也可 archived（归档不做）；从不真删，deleted_at 软删；
- 与产品知识库的"已投产类"互不污染：没有 SKU 就进这里，定型后再建产品。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, Index, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, IdMixin, JSONType

STATUS_LABELS = {
    "open": "待评估",
    "developing": "开发中",
    "converted": "已转商机",
    "archived": "已归档",
}


class CustomInquiry(Base, IdMixin):
    __tablename__ = "custom_inquiries"
    __table_args__ = (
        Index("ix_custom_inquiries_customer", "customer_id"),
        Index("ix_custom_inquiries_status", "status"),
    )

    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    customer_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    contact_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    opportunity_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(16, 3), nullable=True)
    target_price: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    extra: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
