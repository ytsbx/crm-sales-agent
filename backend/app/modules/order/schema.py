from datetime import date

from pydantic import BaseModel, ConfigDict


class OrderFromQuote(BaseModel):
    delivery_date: date | None = None
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
