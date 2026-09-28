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


class CustomInquiryRevise(BaseModel):
    """修订：新增一版并留修订说明，历史版本不覆盖（§3.3）。"""

    model_config = ConfigDict(extra="ignore")

    revision_note: str | None = Field(default=None, max_length=255)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    quantity: Decimal | None = None
    target_price: Decimal | None = None
    remark: str | None = None
