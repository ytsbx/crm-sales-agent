from datetime import date

from pydantic import BaseModel, ConfigDict, Field


class OpportunityCreate(BaseModel):
    customer_id: int
    title: str = Field(min_length=1, max_length=200)
    primary_contact_id: int | None = None
    source: str | None = None
    stage_id: int | None = None
    expected_amount: float | None = None
    expected_close_date: date | None = None
    owner_id: int | None = None
    competitor: str | None = None
    risk_level: str | None = None
    next_action: str | None = None


class OpportunityUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str | None = None
    primary_contact_id: int | None = None
    source: str | None = None
    expected_amount: float | None = None
    expected_close_date: date | None = None
    competitor: str | None = None
    risk_level: str | None = None
    next_action: str | None = None


class StageChange(BaseModel):
    stage_id: int | None = None
    stage_code: str | None = None
    remark: str | None = None


class OpportunityLose(BaseModel):
    loss_reason_id: int
    remark: str | None = None
    reopen_at: date | None = None


class OpportunityWin(BaseModel):
    win_quote_version_id: int | None = None
    remark: str | None = None


class OpportunityItemCreate(BaseModel):
    sku_id: int
    quantity: float = 1
    target_price: float | None = None
    specification: str | None = None
    color: str | None = None
    package_requirement: str | None = None
    delivery_date: date | None = None
    destination: str | None = None
    remark: str | None = None


class OpportunityItemUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    quantity: float | None = None
    target_price: float | None = None
    specification: str | None = None
    color: str | None = None
    package_requirement: str | None = None
    delivery_date: date | None = None
    destination: str | None = None
    remark: str | None = None
