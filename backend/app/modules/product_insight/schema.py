from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class InsightCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    source: str | None = Field(default=None, max_length=64)
    target_customer: str | None = Field(default=None, max_length=128)
    direction: str | None = None
    selling_points: str | None = None
    price_assumption: Decimal | None = None
    conclusion: str | None = None
    owner_id: int | None = None


class InsightUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    source: str | None = None
    target_customer: str | None = None
    direction: str | None = None
    selling_points: str | None = None
    price_assumption: Decimal | None = None
    conclusion: str | None = None
    owner_id: int | None = None


class InsightReview(BaseModel):
    approve: bool
    note: str | None = Field(default=None, max_length=255)
