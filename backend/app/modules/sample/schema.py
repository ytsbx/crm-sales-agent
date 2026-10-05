"""样品入参（03-API §26）。"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    # 与「从来源创建」的 SampleSourceItem.quantity 保持**同一套约束**：
    # 旧入口此前不校验，0 / 负数都能落库，两个入口口径不一致。
    quantity: Decimal = Field(default=Decimal(1), gt=0, max_digits=16, decimal_places=3)
    # 车间依据：逐行不同，所以挂在明细上（不是单头）
    craft: str | None = Field(default=None, max_length=128)
    material: str | None = Field(default=None, max_length=128)
    drawing_version: str | None = Field(default=None, max_length=64)
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


class SampleItemPatch(BaseModel):
    """改一条明细的「车间依据」：材质 / 工艺 / 图纸版本。

    只放这三项，而且**故意用 `extra="forbid"`**（本项目其它入参都是 `ignore`）：
    这是个窄接口，调用方若顺手带了 `quantity` 之类的字段，`ignore` 会静默丢弃，
    前端以为改成功了、库里其实没动——那比报错更难查。宁可让它明确地报参数错误。

    改数量/规格/备注不在这里：它们不是"审批批的那一版资料"，与车间依据受不同的
    口径约束，混在一个接口里迟早会把两种规则搅在一起。
    """

    model_config = ConfigDict(extra="forbid")

    craft: str | None = Field(default=None, max_length=128)
    material: str | None = Field(default=None, max_length=128)
    drawing_version: str | None = Field(default=None, max_length=64)


class SampleSource(BaseModel):
    quote_version_id: int | None = Field(default=None, gt=0)
    inquiry_id: int | None = Field(default=None, gt=0)

    @model_validator(mode='after')
    def exactly_one_source(self):
        if (self.quote_version_id is None) == (self.inquiry_id is None):
            raise ValueError('必须指定且只能指定一种打样来源')
        return self


class SampleSourceItem(BaseModel):
    source_item_id: int = Field(gt=0)
    quantity: Decimal = Field(default=Decimal(1), gt=0, max_digits=16, decimal_places=3)
    specification: str | None = Field(default=None, max_length=2000)
    remark: str | None = Field(default=None, max_length=255)


class SampleFromSource(SampleSource):
    request_key: UUID
    items: list[SampleSourceItem] = Field(min_length=1, max_length=200)
    remark: str | None = Field(default=None, max_length=2000)

    @model_validator(mode='after')
    def unique_items(self):
        if len({item.source_item_id for item in self.items}) != len(self.items):
            raise ValueError('同一来源明细不能重复选择')
        return self


class SampleUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    contact_id: int | None = None
    owner_id: int | None = None
    remark: str | None = None
    # ---- 生产打样资料（文档 §3.5）----
    # 跟单/生产在这几个字段里补生产资料，打样需求单出图时把它们带给车间。
    # 材质 / 工艺 / 图纸版本**不在这里**：它们逐行不同，走明细（SampleItemPatch）。
    purpose: str | None = None
    target_completion_date: date | None = None
    acceptance_criteria: str | None = None
    # 打样费用：**空着 = 还没填**（不是 0 元）。所以
    # - 允许 None（列也已改成可空），界面显示"未填"；
    # - 一旦给值就必须 >= 0，负数是没有意义的费用。
    # 前端清空输入框时传 null，正好落回"未填"，不会再撞数据库的非空约束。
    sample_fee: Decimal | None = Field(default=None, ge=0, max_digits=16, decimal_places=2)
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
