"""成本、价格规则、客户特殊价、价格权限、运费费率。

说明：本文件里的「价格」全部是人民币（客户在国内，不做多币种）。
"""

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Index, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, IdMixin, JSONType


class ProductCost(Base, IdMixin):
    """SKU 成本（按生效区间留版本）。

    ## 为什么四项成本可以为 NULL（第七批 7.4 返修）

    原来这四列是 `NOT NULL DEFAULT 0`，于是"没填"和"填了 0"在库里长得一模一样。
    导入时把空白当 0，就造出了一条"四项全零"的成本 —— 而核价是靠
    "有没有生效成本行"判断成本已知的，结果是：**只有 SKU 和生效日、
    成本一个字没填的模板行，进系统后就变成了已知的零成本，毛利率 100%**。

    改成可空之后：
    - `NULL` = 未提供（核价要提示成本不完整，不能装作零成本）；
    - `0` = 明确为零（真实业务里存在，例如客户供料）；
    - 四项全空的新建行一律不落库（见 pricing/io_router.py 的导入校验）。

    历史数据里那些"四项全零"的行无法自动分辨是哪一种，
    所以只**列核对清单**（`scripts/list_zero_cost_rows.py`），不擅自删改。
    """

    __tablename__ = "product_costs"
    __table_args__ = (Index("ix_product_costs_sku", "sku_id", "effective_from"),)

    sku_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("skus.id"))
    purchase_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)
    production_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)
    package_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)
    processing_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    #: 四项成本的中文名，报"缺哪几项"时直接用，避免前端再维护一份映射
    COST_LABELS = {
        "purchase_cost": "采购成本",
        "production_cost": "生产成本",
        "package_cost": "包装成本",
        "processing_cost": "加工成本",
    }

    @property
    def total_cost(self) -> Decimal:
        """合计。未提供的列按 0 参与合计 —— 但调用方必须自己看 `is_complete`，
        不能拿这个数当"完整成本"用。"""
        return (
            (self.purchase_cost or Decimal(0))
            + (self.production_cost or Decimal(0))
            + (self.package_cost or Decimal(0))
            + (self.processing_cost or Decimal(0))
        )

    @property
    def missing_components(self) -> list[str]:
        """未提供的成本项中文名（空列表 = 四项都填了）。"""
        return [
            label
            for column, label in self.COST_LABELS.items()
            if getattr(self, column) is None
        ]

    @property
    def is_complete(self) -> bool:
        return not self.missing_components


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
    # A14：active=当前售价（参与匹配）；historical=历史资料（不参与匹配与冲突检查）
    status: Mapped[str] = mapped_column(String(16), default="active")
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
    所以先落一张本地费率表：按公斤单价 / 体积单价 + 最低收费，能算、能改、能替换成 API。

    体积单价与时效区间是为 PRD §14「计费重 / 预计时效 / 方案列表」补的：
    只按重量算，抛货（体积大重量轻）会被严重低估。
    """

    __tablename__ = "logistics_rates"

    provider: Mapped[str] = mapped_column(String(64))
    destination_region: Mapped[str | None] = mapped_column(String(64), nullable=True)
    origin_region: Mapped[str | None] = mapped_column(String(64), nullable=True)
    shipping_method: Mapped[str] = mapped_column(String(32), default="陆运")
    unit_price_per_kg: Mapped[Decimal] = mapped_column(Numeric(10, 4), default=1)
    # 按体积计费时的单价（元/立方米）；为空表示这家不按体积计费
    unit_price_per_volume: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    min_charge: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)
    eta_days: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    eta_days_max: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)


class LogisticsQuote(Base, IdMixin):
    """物流试算结果留痕（02-ER §10 logistics_quotes）。

    为什么要落库而不是纯计算：报价一旦发给客户，事后要能回答
    「当时这个运费是按哪家、什么费率、多重体积算出来的」。
    """

    __tablename__ = "logistics_quotes"
    __table_args__ = (
        Index("ix_logistics_quotes_customer", "customer_id"),
        Index("ix_logistics_quotes_opportunity", "opportunity_id"),
    )

    customer_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("customers.id"), nullable=True
    )
    opportunity_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sku_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("skus.id"), nullable=True)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(16, 3), nullable=True)
    origin: Mapped[str | None] = mapped_column(String(64), nullable=True)
    destination: Mapped[str | None] = mapped_column(String(64), nullable=True)
    shipping_method: Mapped[str] = mapped_column(String(32), default="陆运")
    # 计费重：取「实际重量」与「体积重」的较大者（PRD §14 要求输出）
    chargeable_weight: Mapped[Decimal] = mapped_column(Numeric(16, 4), default=0)
    actual_weight: Mapped[Decimal] = mapped_column(Numeric(16, 4), default=0)
    volume: Mapped[Decimal] = mapped_column(Numeric(16, 4), default=0)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(16, 4), nullable=True)
    eta_days: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_data: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


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
