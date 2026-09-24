"""产品与 SKU。

约定（对齐总设计文档 §11）：
- Product 是销售侧的产品资料，SKU 是可报价、可报价的最小单位；
- 完整 BOM 由 ERP/MES 维护，CRM 不重复建设，只存销售需要的规格与包装信息。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, IdMixin, TimestampMixin


class Product(Base, IdMixin, TimestampMixin):
    __tablename__ = "products"
    __table_args__ = (Index("ix_products_name", "name"),)

    name: Mapped[str] = mapped_column(String(200))
    product_line: Mapped[str | None] = mapped_column(String(64), nullable=True)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    brand: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    knowledge: Mapped[str | None] = mapped_column(Text, nullable=True)  # 产品知识，供 Agent 检索
    status: Mapped[str] = mapped_column(String(32), default="active")
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Sku(Base, IdMixin, TimestampMixin):
    __tablename__ = "skus"
    __table_args__ = (
        Index("ix_skus_product", "product_id"),
        Index("ix_skus_code", "sku_code", unique=True),
    )

    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("products.id"))
    sku_code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    specification: Mapped[str | None] = mapped_column(String(200), nullable=True)
    color: Mapped[str | None] = mapped_column(String(64), nullable=True)
    material: Mapped[str | None] = mapped_column(String(64), nullable=True)
    length: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    width: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    height: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    weight: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    carton_qty: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    carton_volume: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    moq: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    package_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(16), default="件")
    status: Mapped[str] = mapped_column(String(32), default="active")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
