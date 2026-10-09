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
    # 数值约束（审查 C3-05）：**与库列精度对齐**
    #   quantity     numeric(16,3) → 必须 > 0、最多 3 位小数
    #   target_price numeric(16,2) → 必须 >= 0、最多 2 位小数
    #
    # 改之前这一组毫无约束，实测：数量 -2 / 目标价 -10 都 200 并落库；
    # 数量 **0.0001 会被静默存成 0.000**（正数变成零，比接受负数更阴 ——
    # 报价/下单拿到 0 数量会算出 0 金额）；1.23456 被静默改成 1.235。
    # 留空仍是"尚未确定"，语义不变（None 放行）。
    quantity: Decimal | None = Field(default=None, gt=0, max_digits=16, decimal_places=3)
    target_price: Decimal | None = Field(default=None, ge=0, max_digits=16, decimal_places=2)
    remark: str | None = None


class CustomInquiryUpdate(PatchModel):
    model_config = ConfigDict(extra="ignore")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    customer_id: int | None = None
    contact_id: int | None = None
    # 数值约束（审查 C3-05）：**与库列精度对齐**
    #   quantity     numeric(16,3) → 必须 > 0、最多 3 位小数
    #   target_price numeric(16,2) → 必须 >= 0、最多 2 位小数
    #
    # 改之前这一组毫无约束，实测：数量 -2 / 目标价 -10 都 200 并落库；
    # 数量 **0.0001 会被静默存成 0.000**（正数变成零，比接受负数更阴 ——
    # 报价/下单拿到 0 数量会算出 0 金额）；1.23456 被静默改成 1.235。
    # 留空仍是"尚未确定"，语义不变（None 放行）。
    quantity: Decimal | None = Field(default=None, gt=0, max_digits=16, decimal_places=3)
    target_price: Decimal | None = Field(default=None, ge=0, max_digits=16, decimal_places=2)
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
    # 数值约束（审查 C3-05）：**与库列精度对齐**
    #   quantity     numeric(16,3) → 必须 > 0、最多 3 位小数
    #   target_price numeric(16,2) → 必须 >= 0、最多 2 位小数
    #
    # 改之前这一组毫无约束，实测：数量 -2 / 目标价 -10 都 200 并落库；
    # 数量 **0.0001 会被静默存成 0.000**（正数变成零，比接受负数更阴 ——
    # 报价/下单拿到 0 数量会算出 0 金额）；1.23456 被静默改成 1.235。
    # 留空仍是"尚未确定"，语义不变（None 放行）。
    quantity: Decimal | None = Field(default=None, gt=0, max_digits=16, decimal_places=3)
    target_price: Decimal | None = Field(default=None, ge=0, max_digits=16, decimal_places=2)
    remark: str | None = None
