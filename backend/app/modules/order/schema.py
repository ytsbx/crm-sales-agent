from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class OrderFromQuote(BaseModel):
    delivery_date: date | None = None
    remark: str | None = None


class MilestoneUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    planned_date: date | None = None
    actual_date: date | None = None
    remark: str | None = None


class OrderItemInput(BaseModel):
    sku_id: int
    quantity: Decimal = Field(gt=0)
    unit_price: Decimal = Field(ge=0)
    specification: str | None = None
    remark: str | None = None


class OrderCreate(BaseModel):
    """手工建订单（03-API §27）。

    PRD §20 说明订单正常来自"成交"环节（报价版本转订单），
    但线下签约、补录历史单等场景需要一个直接建单的入口。
    金额不由前端传 —— 由明细算出，避免两者不一致。
    """

    customer_id: int
    items: list[OrderItemInput] = Field(min_length=1)
    opportunity_id: int | None = None
    quote_id: int | None = None
    owner_id: int | None = None
    currency: str = "CNY"
    delivery_date: date | None = None
    payment_terms: str | None = None
    remark: str | None = None


class OrderUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    delivery_date: date | None = None
    remark: str | None = None
    owner_id: int | None = None
    payment_terms: str | None = None


class OrderStatusChange(BaseModel):
    status: str
    remark: str | None = None
