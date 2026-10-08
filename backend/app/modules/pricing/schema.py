from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from app.core.patch_schema import PatchModel



class CostCreate(BaseModel):
    """新增成本版本（页面维护入口）。

    第七批 7.4：四项成本可为空，但**不允许全部为空** —— 空表示"未提供"，
    全空落库就是一条"信息为零的成本"，而核价按"有没有成本行"判断成本已知，
    会据此算出假毛利。校验放在路由里做（要给出人话原因），这里只保证类型。
    """

    model_config = ConfigDict(extra="ignore")

    purchase_cost: Decimal | None = Field(default=None, ge=0)
    production_cost: Decimal | None = Field(default=None, ge=0)
    package_cost: Decimal | None = Field(default=None, ge=0)
    processing_cost: Decimal | None = Field(default=None, ge=0)
    effective_from: date
    effective_to: date | None = None
    remark: str | None = Field(default=None, max_length=255)


class CostUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    purchase_cost: Decimal | None = Field(default=None, ge=0)
    production_cost: Decimal | None = Field(default=None, ge=0)
    package_cost: Decimal | None = Field(default=None, ge=0)
    processing_cost: Decimal | None = Field(default=None, ge=0)
    effective_from: date | None = None
    effective_to: date | None = None
    remark: str | None = Field(default=None, max_length=255)


class ExchangeRateCreate(BaseModel):
    """维护汇率（03-API §16）。

    外贸报价必须先有汇率：`resolve_exchange_rate` 找不到就报错，
    不静默按 1:1 处理（否则外币报价会悄悄算错一个数量级）。
    此前汇率表只有模型没有接口，只能改库 —— 这条链路是断的。
    """

    model_config = ConfigDict(extra="ignore")

    base_currency: str = "CNY"
    quote_currency: str
    rate: Decimal = Field(gt=0)
    source: str | None = None
    effective_at: datetime | None = None


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


class PriceRuleUpdate(PatchModel):
    model_config = ConfigDict(extra="ignore")

    customer_level: str | None = None
    min_qty: Decimal | None = None
    max_qty: Decimal | None = None
    standard_price: Decimal | None = None
    guide_price: Decimal | None = None
    minimum_price: Decimal | None = None
    target_margin: Decimal | None = None
    status: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    remark: str | None = None


class CustomerPriceCreate(BaseModel):
    customer_id: int
    sku_id: int
    min_qty: Decimal = Decimal(0)
    max_qty: Decimal | None = None
    agreed_price: Decimal
    minimum_price: Decimal | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    remark: str | None = None


class CustomerPriceUpdate(BaseModel):
    """改客户特殊价（03-API §15 PATCH /customer-price-rules/{id}）。

    不允许改 `customer_id` / `sku_id`：那等于换一条规则，
    删除重建比原地改更清楚（历史审计也读得懂）。
    """

    model_config = ConfigDict(extra="ignore")

    min_qty: Decimal | None = None
    max_qty: Decimal | None = None
    agreed_price: Decimal | None = None
    minimum_price: Decimal | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    remark: str | None = None


class PricePermissionUpdate(BaseModel):
    minimum_margin: Decimal = Field(default=Decimal("0.15"), ge=0, le=1)
    discount_limit: Decimal | None = Field(default=None, ge=0, le=1)
    can_approve: bool = False
    remark: str | None = None


class PricePermissionCreate(PricePermissionUpdate):
    """新增价格权限（03-API §16 的 POST 写法）。

    比 Update 多一个 role_id —— 一个角色只有一条价格权限，
    所以"新增"遇到已存在的会被拒，让调用方改用 PUT。
    """

    role_id: int


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


class LogisticsRateUpdate(PatchModel):
    """运费费率的**部分更新**（价格中心「运费费率」的「改」）。

    为什么是"部分更新"而不是让调用方传整行：这一页最常见的就是"只改一个单价"或
    "把目的地收窄"，要求整行传全，改错的风险更大（把没打算动的字段一起写回去）。

    **哪些不许被清空**（`provider` / `shipping_method` / `unit_price_per_kg` /
    `min_charge` / `status`）登记在 `core/patch_schema.NOT_NULLABLE` —— 库里那几列
    非空，显式传 `null` 从前会一路走到数据库才报 500。

    **哪些允许清空**：两个地区字段、`unit_price_per_volume`、两个时效、`remark`。
    尤其地区字段：**留空 = 不限**（匹配时视作通配），而写「全国」是一个**具体取值**——
    两者在核价匹配里行为不同，别混。
    """

    provider: str | None = None
    origin_region: str | None = None
    destination_region: str | None = None
    shipping_method: str | None = None
    unit_price_per_kg: Decimal | None = Field(default=None, ge=0)
    unit_price_per_volume: Decimal | None = Field(default=None, ge=0)
    min_charge: Decimal | None = Field(default=None, ge=0)
    eta_days: int | None = Field(default=None, ge=0)
    eta_days_max: int | None = Field(default=None, ge=0)
    status: Literal["active", "inactive"] | None = None
    remark: str | None = None


class PricingRequest(BaseModel):
    sku_id: int
    # 数量必须为正：此前没有约束，quantity=0 会一路算出 0 元的"建议价"，
    # 界面上看起来像正常的核价结果，实际是无意义数据。
    quantity: Decimal = Field(default=Decimal(1), gt=0)
    customer_id: int | None = None
    logistics_cost: Decimal | None = Field(default=None, ge=0)
    target_margin: Decimal | None = Field(default=None, gt=0, le=1)
    """利润要求（比率口径）。与 target_profit_amount 二选一，同时给时以比率优先。"""
    target_profit_amount: Decimal | None = None
    """利润要求（绝对金额口径，单件）：PRD §13「利润要求」允许按金额提要求。"""
    quoted_price: Decimal | None = Field(default=None, gt=0)
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


class PricePermissionCheck(PricingRequest):
    """询价权限校验（03-API §18 POST /pricing/check-permission）。

    比 calculate 多一个硬要求：必须给出 `quoted_price` ——
    不问"这个价能不能报"，光核算价没意义。
    """

    quoted_price: Decimal = Field(gt=0)


class PricingSimulation(BaseModel):
    """报价模拟（03-API §18 POST /pricing/simulate）。

    给定一个基准询价条件，再给一组候选报价，一次算出每个候选的利润、
    利润率、是否需要审批 —— 谈判前要能一眼看出"降到这个价还赚不赚、
    会不会触发审批"。

    `candidates`（候选成交价）与 `margins`（候选利润率）二选一，
    同时给时以 `candidates` 为准。
    """

    base: PricingRequest
    candidates: list[Decimal] = Field(default_factory=list)
    margins: list[Decimal] = Field(default_factory=list)
