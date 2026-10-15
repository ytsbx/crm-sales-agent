"""样品入参（03-API §26）。"""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.core.patch_schema import CreateModel


# ---------------------------------------------------------------- 申请单选项
#
# 这三组选项**照抄钉钉模板**（2026-10-15 从实物文件与批注读出），不要自造：
#   · 样品制作单位的下拉里是「义乌打样部 / 山东生产部 / 外协厂」
#     （G3 批注补了选择规则：常规款有模具的优先山东生产部或外协厂，
#       完全新的设计产品走义乌打样部 —— 那是**业务判断**，不在这里硬编码）
#   · 紧急程度不只是个标记，批注写明它是**交期宽限**：
#       普通 = 可晚 3~5 天；急 = 可晚 1~3 天；紧急 = **不允许晚**
#   · 打样目的四项各有明确定义（C4 批注）
#
# 用 Literal 而不是自由字符串：钉钉那边是下拉，写错一个字的后果是
# **导出的 Excel 和钉钉单据对不上**，而人眼很难发现"外协厂打样"和"外协厂"的差别。
SAMPLE_TYPE_OPTIONS = ("设计研发内部打样", "销售客户打样")
PRODUCT_CATEGORY_OPTIONS = ("高频类产品", "车缝类产品")
MAKER_UNIT_OPTIONS = ("义乌打样部", "山东生产部", "外协厂")
SAMPLE_PURPOSE_OPTIONS = (
    "客审确认样",
    "报价核算样",
    "定制改款样",
    "送检上架样",
)
URGENCY_OPTIONS = ("普通", "急", "紧急")
SAMPLE_SITUATION_OPTIONS = (
    "首次设计新品",
    "设计更改",
    "客户首次要求",
    "客户更改",
    "其他",
)

#: 积分口径：师傅用时 1 小时 = 10 积分（钉钉表上原文）
POINTS_PER_HOUR = 10


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
    use_dingtalk: bool = False
    model_config = ConfigDict(extra="ignore")

    opportunity_id: int | None = None
    customer_id: int | None = None
    contact_id: int | None = None
    owner_id: int | None = None
    """默认取商机负责人，再退回当前用户。"""
    production_owner_id: int | None = None
    """制作负责人（跟单/车间）。**新建时也能指定**（审查 C3-02）。

    从前只有 `SampleUpdate` 有这个字段，于是新建时传它被 pydantic 的
    `extra="ignore"` 静默丢掉、模型上留着 NULL —— 调用方以为设好了。
    这和 C3-01 是同一类毛病：**入参收了但没往下走**。
    """
    remark: str | None = None
    # ---- 钉钉《打样申请单》上的格（2026-10-15）----
    # 见 model.py 同名列的说明；这里只强调**别用自由字符串**：钉钉那边是下拉，
    # 写错一个字的后果是导出的 Excel 与钉钉单据对不上，而人眼很难发现。
    sample_type: Literal[SAMPLE_TYPE_OPTIONS] | None = None
    product_category: Literal[PRODUCT_CATEGORY_OPTIONS] | None = None
    maker_unit: Literal[MAKER_UNIT_OPTIONS] | None = None
    sample_purpose: Literal[SAMPLE_PURPOSE_OPTIONS] | None = None
    urgency: Literal[URGENCY_OPTIONS] | None = None
    sample_situation: Literal[SAMPLE_SITUATION_OPTIONS] | None = None
    # 预计工时（小时）→ 积分由系统按 ×10 算，**不收积分入参**（不让手填，避免两人两个数）
    estimated_hours: Decimal | None = Field(
        default=None, ge=0, le=Decimal("9999"), max_digits=8, decimal_places=2
    )
    # 「打样具体要求」6 格：整段文字，落进 Excel 的 B8~B13
    material_spec: str | None = None
    product_size: str | None = None
    printing: str | None = None
    accessories: str | None = None
    packaging: str | None = None
    craft_requirement: str | None = None
    # 申请单上「产品名称」只有一格，而明细可以一行多样 —— 显式给，导出时不猜
    product_name: str | None = Field(default=None, max_length=200)

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
    use_dingtalk: bool = False
    request_key: UUID
    items: list[SampleSourceItem] = Field(min_length=1, max_length=200)
    remark: str | None = Field(default=None, max_length=2000)

    @model_validator(mode='after')
    def unique_items(self):
        if len({item.source_item_id for item in self.items}) != len(self.items):
            raise ValueError('同一来源明细不能重复选择')
        return self


class SampleUpdate(CreateModel):
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
    # ---- 钉钉《打样申请单》上的格（2026-10-15）----
    # 见 model.py 同名列的说明；这里只强调**别用自由字符串**：钉钉那边是下拉，
    # 写错一个字的后果是导出的 Excel 与钉钉单据对不上，而人眼很难发现。
    sample_type: Literal[SAMPLE_TYPE_OPTIONS] | None = None
    product_category: Literal[PRODUCT_CATEGORY_OPTIONS] | None = None
    maker_unit: Literal[MAKER_UNIT_OPTIONS] | None = None
    sample_purpose: Literal[SAMPLE_PURPOSE_OPTIONS] | None = None
    urgency: Literal[URGENCY_OPTIONS] | None = None
    sample_situation: Literal[SAMPLE_SITUATION_OPTIONS] | None = None
    # 预计工时（小时）→ 积分由系统按 ×10 算，**不收积分入参**（不让手填，避免两人两个数）
    estimated_hours: Decimal | None = Field(
        default=None, ge=0, le=Decimal("9999"), max_digits=8, decimal_places=2
    )
    # 「打样具体要求」6 格：整段文字，落进 Excel 的 B8~B13
    material_spec: str | None = None
    product_size: str | None = None
    printing: str | None = None
    accessories: str | None = None
    packaging: str | None = None
    craft_requirement: str | None = None
    # 申请单上「产品名称」只有一格，而明细可以一行多样 —— 显式给，导出时不猜
    product_name: str | None = Field(default=None, max_length=200)


class SampleMade(BaseModel):
    """登记制作完成（CRM 管不到车间，这里只记录事实，不当流程闸门）。"""

    model_config = ConfigDict(extra="ignore")

    made_at: datetime | None = None
    remark: str | None = None
    #: 幂等键（第一批返修 §3.4）：客户端重发同一个请求时带同一个值，
    #: 后端据此认成"这次已经登记过"；不带则按 (完成时间, 说明) 精确算一个。
    #: 说明只要不同（哪怕恰好是上一条的子串）就是一次**新事件**，照常追加与通知。
    request_key: str | None = Field(default=None, max_length=64)
    #: **制作依据**（2026-10-06）：本次实际采用的附件 id 列表（`files.id`）。
    #:
    #: 为什么必须有：车间依据里的"图纸版本"只是一串自由文字，和文件之间没有
    #: 任何关联——出了质量问题，系统答不出"当时按哪份图纸做的"。
    #: 这里显式指定后，后端会把文件 id / 文件名 / sha256 / 大小一起快照下来。
    #: 登记即固化：要换依据只能开修订版。
    basis_file_ids: list[int] | None = None



class SampleResubmit(BaseModel):
    """原样重提（第一批返修 §3.2）。

    `request_key` 只为**弱网重试**服务：同一次提交重发时带同一个键，后端认成幂等、
    不再加一轮也不重复通知。不带键时行为不变——"待审批的单子调重提"仍按原口径被拦，
    两者靠请求键区分，不是靠状态。
    """

    model_config = ConfigDict(extra="ignore")

    request_key: str | None = Field(default=None, max_length=64)


class SampleRevise(BaseModel):
    """开新修订版（第一批返修 §3.3，口径已确认 A：原单出 V2、旧版冻结只读）。"""

    model_config = ConfigDict(extra="ignore")

    #: 为什么要开这一版（给人看）。版本链本身由 version/parent_id 承担，
    #: **不靠备注假装**——备注只是说明文字。
    remark: str | None = Field(default=None, max_length=1000)


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
    # 运费：**非负 + 2 位小数**，与 `sample_fee` 同一套约束（审查 C3-03）。
    #
    # 改之前这一项毫无约束，实测提交 `-99` 会 200 并落库 `-99.00`；
    # 提交 `12.345` 会被静默四舍五入成 `12.35`（用户填的数被悄悄改了）。
    # 同一个费用类字段，`sample_fee` 是拦的（400「不能小于 0」「小数位太多」），
    # 这里却放行 —— 两条路两个口径。现在对齐。
    shipping_fee: Decimal = Field(default=Decimal(0), ge=0, max_digits=16, decimal_places=2)
    shipped_at: date | None = None


class SampleSign(BaseModel):
    model_config = ConfigDict(extra="ignore")

    signed_at: date | None = None


class SampleFeedback(BaseModel):
    model_config = ConfigDict(extra="ignore")

    feedback: str
