"""客户与联系人的入参结构。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CustomerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200, description="客户名称")
    short_name: str | None = None
    customer_type: str | None = "企业"
    country: str | None = "中国"
    region: str | None = None
    address: str | None = None
    domain: str | None = None
    tax_no: str | None = None
    source: str | None = None
    level: str | None = None
    owner_id: int | None = None
    remark: str | None = None


class CustomerUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    short_name: str | None = None
    customer_type: str | None = None
    country: str | None = None
    region: str | None = None
    address: str | None = None
    domain: str | None = None
    tax_no: str | None = None
    source: str | None = None
    level: str | None = None
    status: str | None = None
    remark: str | None = None
    next_followup_at: datetime | None = None


class CustomerTransfer(BaseModel):
    owner_id: int | None = None
    reason: str | None = None


class ContactCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    title: str | None = None
    department: str | None = None
    mobile: str | None = None
    phone: str | None = None
    email: str | None = None
    wechat: str | None = None
    is_primary: bool = False
    remark: str | None = None


class ContactUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    title: str | None = None
    department: str | None = None
    mobile: str | None = None
    phone: str | None = None
    email: str | None = None
    wechat: str | None = None
    is_primary: bool | None = None
    remark: str | None = None
