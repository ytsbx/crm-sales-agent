from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class CustomInquiryCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    customer_id: int | None = None
    contact_id: int | None = None
    quantity: Decimal | None = None
    target_price: Decimal | None = None
    remark: str | None = None


class CustomInquiryUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    customer_id: int | None = None
    contact_id: int | None = None
    quantity: Decimal | None = None
    target_price: Decimal | None = None
    status: str | None = None
    remark: str | None = None
    opportunity_id: int | None = None


class CustomInquiryStatusUpdate(BaseModel):
    status: str
