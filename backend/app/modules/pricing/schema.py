from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class CostCreate(BaseModel):
    purchase_cost: Decimal = Decimal(0)
    production_cost: Decimal = Decimal(0)
    package_cost: Decimal = Decimal(0)
    processing_cost: Decimal = Decimal(0)
    effective_from: date
    effective_to: date | None = None
    remark: str | None = None


class CostUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    purchase_cost: Decimal | None = None
    production_cost: Decimal | None = None
    package_cost: Decimal | None = None
    processing_cost: Decimal | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    remark: str | None = None


class PriceRuleCreate(BaseModel):
    sku_id: int
    customer_level: str | None = None
    min_qty: Decimal = Decimal(0)
    max_qty: Decimal | None = None
    standard_price: Decimal | None = None
    guide_price: Decimal | None = None
    minimum_price: Decimal | None = None
    target_margin: Decimal | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    remark: str | None = None


class PriceRuleUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    customer_level: str | None = None
    min_qty: Decimal | None = None
    max_qty: Decimal | None = None
    standard_price: Decimal | None = None
    guide_price: Decimal | None = None
    minimum_price: Decimal | None = None
    target_margin: Decimal | None = None
    status: str | None = None
    remark: str | None = None


class CustomerPriceCreate(BaseModel):
    customer_id: int
    sku_id: int
    min_qty: Decimal = Decimal(0)
    max_qty: Decimal | None = None
    agreed_price: Decimal
    minimum_price: Decimal | None = None
    remark: str | None = None


class PricePermissionUpdate(BaseModel):
    minimum_margin: Decimal = Field(default=Decimal("0.15"), ge=0, le=1)
    discount_limit: Decimal | None = Field(default=None, ge=0, le=1)
    can_approve: bool = False
    remark: str | None = None


class LogisticsRateCreate(BaseModel):
    provider: str
    origin_region: str | None = None
    destination_region: str | None = None
    shipping_method: str = "陆运"
    unit_price_per_kg: Decimal = Decimal(1)
    """按重量计费的单价（元/kg）。"""
    unit_price_per_volume: Decimal | None = None
    """按体积计费的单价（元/m³）；为空表示这家不按体积计费。"""
    min_charge: Decimal = Decimal(0)
    eta_days: int | None = None
    eta_days_max: int | None = None
    remark: str | None = None


class PricingRequest(BaseModel):
    sku_id: int
    quantity: Decimal = Decimal(1)
    customer_id: int | None = None
    logistics_cost: Decimal | None = None
    target_margin: Decimal | None = None
    """利润要求（比率口径）。与 target_profit_amount 二选一，同时给时以比率优先。"""
    target_profit_amount: Decimal | None = None
    """利润要求（绝对金额口径，单件）：PRD §13「利润要求」允许按金额提要求。"""
    quoted_price: Decimal | None = None
    """quoted_price 有值时，接口会顺带判断这个报价是否需要审批。"""

    # PRD §13 核价输入：客户等级 / 国家 / 包装 / 物流方式 / 付款方式
    customer_level: str | None = None
    """显式指定客户等级；不给则按 customer.level 推。"""
    country: str | None = None
    """目的地国家/地区（内贸一般是省份或「全国」）。"""
    package_type: str | None = None
    """包装方式。"""
    shipping_method: str | None = None
    """物流方式（陆运/快递/空运…），用于筛运费费率。"""
    payment_terms: str | None = None
    """付款方式/账期，仅作记录与返回，不参与算价（资金成本口径待财务确认）。"""

    # 外贸口径（可选）：不填就是内贸，全人民币、无退税
    currency: str = "CNY"
    exchange_rate: Decimal | None = None
    tax_refund_rate: Decimal | None = None
