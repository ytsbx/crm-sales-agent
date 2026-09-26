from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class QuoteCreate(BaseModel):
    opportunity_id: int | None = None
    customer_id: int | None = None
    contact_id: int | None = None
    valid_until: date | None = None
    payment_terms: str | None = None
    delivery_terms: str | None = None
    remark: str | None = None
    # 外贸口径（可选）：不填就是内贸，全人民币
    currency: str = "CNY"
    exchange_rate: Decimal | None = None
    """不填则自动取汇率表里该币种的当前汇率并落快照（02-ER §11）。"""


class QuoteVersionUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    payment_terms: str | None = None
    delivery_terms: str | None = None
    remark: str | None = None
    valid_until: date | None = None


class QuoteItemInput(BaseModel):
    """报价明细入参：quoted_price 留空则用核价建议价。"""

    sku_id: int
    quantity: Decimal = Decimal(1)
    quoted_price: Decimal | None = None
    opportunity_item_id: int | None = None
    spec_snapshot: str | None = None
    logistics_cost: Decimal | None = None
    remark: str | None = None


class QuoteItemUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    quantity: Decimal | None = None
    quoted_price: Decimal | None = None
    logistics_cost: Decimal | None = None
    remark: str | None = None


class QuoteChargeInput(BaseModel):
    charge_type: str = "other"
    description: str | None = None
    amount: Decimal = Decimal(0)
    is_discount: bool = False


class ApprovalAction(BaseModel):
    comment: str | None = None


class SendRequest(BaseModel):
    channel: str = "邮件"
    receiver: str | None = None


class SubmitApprovalRequest(BaseModel):
    reason: str | None = None


class DeclinedRequest(BaseModel):
    reason: str | None = None


__all__ = ["datetime", "Field"]
