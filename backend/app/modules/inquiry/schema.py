from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field
from app.core.patch_schema import PatchModel



class CustomInquiryCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    customer_id: int | None = None
    contact_id: int | None = None
    opportunity_id: int | None = None
    quantity: Decimal | None = None
    target_price: Decimal | None = None
    remark: str | None = None


class CustomInquiryUpdate(PatchModel):
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


class CustomInquiryQuoteRequest(BaseModel):
    """从定制需求直接发起报价（场景09）。

    只让填两个数：核价成本与报价。数量不填就取需求上的数量；其余（客户、
    商机、明细来源、快照）都由系统接。
    """

    model_config = ConfigDict(extra="ignore")

    #: 核价成本（元/件，不含运费）——必填，理由见 quote.service 的定制项分支
    unit_cost: Decimal
    quoted_price: Decimal
    quantity: Decimal | None = None
    item_name: str | None = None
    valid_until: date | None = None


class CustomInquiryRevise(BaseModel):
    """修订：新增一版并留修订说明，历史版本不覆盖（§3.3）。"""

    model_config = ConfigDict(extra="ignore")

    revision_note: str | None = Field(default=None, max_length=255)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    quantity: Decimal | None = None
    target_price: Decimal | None = None
    remark: str | None = None
