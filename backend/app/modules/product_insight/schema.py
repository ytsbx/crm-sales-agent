"""新品洞察入参校验（第五批 §6.2：字段校验与 null 清空语义）。

这个文件针对三个已确认的缺陷：

1. **清空不了**：更新里写的是 `if value is not None: setattr(...)`，于是传 `null`
   想清空价格假设时被直接跳过、旧值原样留着，**界面上看着清空了、库里还有**。
   正确做法是按"这个字段这次有没有传"判断，而不是按"值是不是空"——
   传 `null` 是"我要清空"，没传才是"别动它"。路由层用
   `model_dump(exclude_unset=True)` 区分这两件事，这里只负责把字段定义齐。

2. **空白标题能过**：`min_length=1` 只数长度，`"   "`（三个空格）长度是 3，
   照样通过。改成 strip 后非空。

3. **长度与库里不一致**：更新的 `source` / `target_customer` 没有长度上限，
   而库里是 64 / 128 —— 超长一直要到写库那一刻才炸，报的是数据库错误。
   另外价格假设没有非负校验，负数能存进去。

`extra="forbid"`：这是**窄接口**（字段就是洞察自己的那几个），前端多传字段
一律报错，而不是静默丢掉——静默丢掉会让前端以为改成功了、其实库没动，
跟第 1 条是同一类事故。
"""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from app.core.patch_schema import PatchModel



def _strip_title(value: str | None) -> str | None:
    """标题必须 strip 后非空。None 表示"这次没传"，原样放行。"""
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("标题不能是空白")
    return cleaned


class InsightCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(max_length=200)
    source: str | None = Field(default=None, max_length=64)
    target_customer: str | None = Field(default=None, max_length=128)
    direction: str | None = None
    selling_points: str | None = None
    # 价格假设：负数没有意义，而且后续换算会把它当真实数据用
    price_assumption: Decimal | None = Field(default=None, ge=0)
    conclusion: str | None = None
    # 参考图 URL 列表（第五批 §6.1(6)：这个字段模型里一直有，但四个环节都没打通）
    images: list[str] | None = None
    owner_id: int | None = None

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, value: str) -> str:
        cleaned = _strip_title(value)
        assert cleaned is not None  # title 是必填，不会是 None
        return cleaned


class InsightUpdate(PatchModel):
    """更新：**传了就改（含传 null = 清空），没传就不动**。

    路由层必须用 `model_dump(exclude_unset=True)`，否则分不出"没传"和"传 null"。
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=200)
    source: str | None = Field(default=None, max_length=64)
    target_customer: str | None = Field(default=None, max_length=128)
    direction: str | None = None
    selling_points: str | None = None
    price_assumption: Decimal | None = Field(default=None, ge=0)
    conclusion: str | None = None
    images: list[str] | None = None
    owner_id: int | None = None

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, value: str | None) -> str | None:
        return _strip_title(value)


class InsightSubmit(BaseModel):
    """提交评审。`request_key` 只用于弱网重试的幂等（不带键时行为不变）。"""

    model_config = ConfigDict(extra="forbid")

    request_key: str | None = Field(default=None, max_length=64)


class InsightReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approve: bool
    note: str | None = Field(default=None, max_length=255)
    request_key: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _reject_needs_note(self) -> "InsightReview":
        # 否决必须说明理由：否则提出者只知道"没过"，不知道该改什么，
        # 而且事后没人说得清为什么否掉——这类结论是有人要担责的。
        if not self.approve and not (self.note or "").strip():
            raise ValueError("否决时必须写明评审意见")
        return self


class InsightConvert(BaseModel):
    """转换入参（第五批 §6.1(3)，口径已确认）。

    - **填了客户** → 走正常的客户询价（校验客户/商机/联系人是否同一笔业务、
      是否在操作者范围内，与 `/custom-inquiries` 同一套）；
    - **没填客户** → 转成**内部开发需求**：明确标识来源，保留独立权限，
      不冒充"客户已经提出采购需求"。
    """

    model_config = ConfigDict(extra="forbid")

    customer_id: int | None = None
    opportunity_id: int | None = None
    contact_id: int | None = None
    quantity: Decimal | None = Field(default=None, gt=0)
    target_price: Decimal | None = Field(default=None, ge=0)
    remark: str | None = Field(default=None, max_length=500)
    request_key: str | None = Field(default=None, max_length=64)
