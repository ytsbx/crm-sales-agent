from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class LeadCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    company_name: str | None = None
    contact_name: str | None = None
    mobile: str | None = None
    email: str | None = None
    source: str | None = None
    source_detail: str | None = None
    region: str | None = None
    remark: str | None = None
    owner_id: int | None = None


class LeadUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    company_name: str | None = None
    contact_name: str | None = None
    mobile: str | None = None
    email: str | None = None
    source: str | None = None
    source_detail: str | None = None
    region: str | None = None
    remark: str | None = None


class LeadAssign(BaseModel):
    owner_id: int | None = None
    reason: str | None = None


class LeadDiscard(BaseModel):
    reason: str = Field(min_length=1, max_length=255)


class LeadConvert(BaseModel):
    """线索转化：支持关联已有客户或新建客户，可选创建联系人与商机。"""

    customer_mode: str = Field(default="new", pattern="^(new|existing)$")
    customer_id: int | None = None
    create_contact: bool = True
    create_opportunity: bool = False
    opportunity_title: str | None = None
    expected_amount: float | None = None
    expected_close_date: datetime | None = None
