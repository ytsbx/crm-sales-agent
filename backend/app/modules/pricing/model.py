"""成本、价格规则、客户特殊价、价格权限、运费费率。

说明：本文件里的「价格」全部是人民币（客户在国内，不做多币种）。
"""

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Index, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, IdMixin


class ProductCost(Base, IdMixin):
    __tablename__ = "product_costs"
    __table_args__ = (Index("ix_product_costs_sku", "sku_id", "effective_from"),)

    sku_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("skus.id"))
    purchase_cost: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=0)
    production_cost: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=0)
    package_cost: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=0)
    processing_cost: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=0)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    @property
    def total_cost(self) -> Decimal:
        return (
            (self.purchase_cost or Decimal(0))
            + (self.production_cost or Decimal(0))
            + (self.package_cost or Decimal(0))
            + (self.processing_cost or Decimal(0))
        )


class PriceRule(Base, IdMixin):
    """标准价 / 指导价 / 最低保护价，可按客户等级与数量区间细分。"""

    __tablename__ = "price_rules"
    __table_args__ = (Index("ix_price_rules_sku", "sku_id", "status"),)

    sku_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("skus.id"))
    customer_level: Mapped[str | None] = mapped_column(String(8), nullable=True)  # 空 = 适用所有等级
    min_qty: Mapped[Decimal] = mapped_column(Numeric(16, 3), default=0)
    max_qty: Mapped[Decimal | None] = mapped_column(Numeric(16, 3), nullable=True)
    standard_price: Mapped[Decimal | None] = mapped_column(Numeric(16, 4), nullable=True)
    guide_price: Mapped[Decimal | None] = mapped_column(Numeric(16, 4), nullable=True)
    minimum_price: Mapped[Decimal | None] = mapped_column(Numeric(16, 4), nullable=True)
    target_margin: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)


class CustomerPriceRule(Base, IdMixin):
    """客户特殊价：一客一价，优先级高于通用价格规则。"""

    __tablename__ = "customer_price_rules"
    __table_args__ = (Index("ix_customer_price_rules_customer", "customer_id", "sku_id"),)

    customer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("customers.id"))
    sku_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("skus.id"))
    min_qty: Mapped[Decimal] = mapped_column(Numeric(16, 3), default=0)
    max_qty: Mapped[Decimal | None] = mapped_column(Numeric(16, 3), nullable=True)
    agreed_price: Mapped[Decimal] = mapped_column(Numeric(16, 4))
    minimum_price: Mapped[Decimal | None] = mapped_column(Numeric(16, 4), nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)


class PricePermission(Base, IdMixin):
    """价格权限：按角色控制最低利润率与最大折扣。

    这是「价格权限与业务权限分离」的落点（05-TECH §23）。
    """

    __tablename__ = "price_permissions"
    __table_args__ = (Index("ix_price_permissions_role", "role_id"),)

    role_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("roles.id"))
    minimum_margin: Mapped[Decimal] = mapped_column(Numeric(6, 4), default=Decimal("0.15"))
    discount_limit: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    can_approve: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(16), default="active")
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)


class LogisticsRate(Base, IdMixin):
    """国内运费费率表。

    文档要求「物流试算」，但接哪家物流商 API 属于待确认事项（澄清清单 3-11），
    所以先落一张本地费率表：按公斤单价 + 最低收费，能算、能改、能替换成 API。
    """

    __tablename__ = "logistics_rates"

    provider: Mapped[str] = mapped_column(String(64))
    destination_region: Mapped[str | None] = mapped_column(String(64), nullable=True)
    shipping_method: Mapped[str] = mapped_column(String(32), default="陆运")
    unit_price_per_kg: Mapped[Decimal] = mapped_column(Numeric(10, 4), default=1)
    min_charge: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)
    eta_days: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")


class ExchangeRate(Base, IdMixin):
    """汇率。

    内贸场景用不到它，也不会看到它；只有外贸报价才需要。
    一条记录 = 一个币种对本币的汇率 + 来源 + 生效时间。
    报价版本会把当时用的汇率快照下来，事后汇率怎么变都不影响历史报价。
    """

    __tablename__ = "exchange_rates"
    __table_args__ = (Index("ix_exchange_rates_pair", "base_currency", "quote_currency"),)

    base_currency: Mapped[str] = mapped_column(String(8), default="CNY")
    quote_currency: Mapped[str] = mapped_column(String(8), default="USD")
    rate: Mapped[Decimal] = mapped_column(Numeric(16, 6))
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    effective_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
