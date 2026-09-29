"""样品入参（03-API §26）。"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class SampleItemInput(BaseModel):
    """打样明细入参。两条路径（文档场景09）：

    - 现货：给 `sku_id`；
    - 定制：尚无正式 SKU 时给 `inquiry_id`（需求编号）+ 可选 `item_name`。
      两者都不给会被拒——明细得说得清打的是什么。
    """

    model_config = ConfigDict(extra="ignore")

    sku_id: int | None = None
    inquiry_id: int | None = None
    item_name: str | None = None
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
    # ---- 生产打样资料（文档 §3.5）----
    # 跟单/生产在这几个字段里补生产资料，打样需求单出图时把它们带给车间
    purpose: str | None = None
    craft: str | None = None
    material: str | None = None
    drawing_version: str | None = None
    target_completion_date: date | None = None
    acceptance_criteria: str | None = None
    sample_fee: Decimal | None = None
    production_owner_id: int | None = None


class SampleMade(BaseModel):
    """登记制作完成（CRM 管不到车间，这里只记录事实，不当流程闸门）。"""

    model_config = ConfigDict(extra="ignore")

    made_at: datetime | None = None
    remark: str | None = None


class SampleConfirm(BaseModel):
    """客户确认。与签收分开：客户收到样品不等于样品被接受。"""

    model_config = ConfigDict(extra="ignore")

    accepted: bool
    remark: str | None = None
    confirmed_at: datetime | None = None


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
