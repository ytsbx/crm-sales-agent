"""样品入参（03-API §26）。"""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class SampleItemInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sku_id: int
    quantity: Decimal = Decimal(1)
    remark: str | None = None


class SampleCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    opportunity_id: int | None = None
    customer_id: int | None = None
    contact_id: int | None = None
    owner_id: int | None = None
    """默认取商机负责人，再退回当前用户。"""
    remark: str | None = None
    items: list[SampleItemInput] = []


class SampleItemAdd(SampleItemInput):
    pass


class SampleUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    contact_id: int | None = None
    owner_id: int | None = None
    remark: str | None = None


class SampleApprove(BaseModel):
    model_config = ConfigDict(extra="ignore")

    approved: bool = True
    reject_reason: str | None = None


class SampleShip(BaseModel):
    model_config = ConfigDict(extra="ignore")

    carrier: str | None = None
    tracking_no: str | None = None
    shipping_fee: Decimal = Decimal(0)
    shipped_at: date | None = None


class SampleSign(BaseModel):
    model_config = ConfigDict(extra="ignore")

    signed_at: date | None = None


class SampleFeedback(BaseModel):
    model_config = ConfigDict(extra="ignore")

    feedback: str
