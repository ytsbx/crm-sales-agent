from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from app.core.patch_schema import CreateModel, PatchModel



class SettingInput(BaseModel):
    key: str
    value: dict | None = None
    description: str | None = None


class PublicPoolRuleInput(CreateModel):
    model_config = ConfigDict(extra="ignore")

    level: str
    days: int = Field(gt=0)
    enabled: bool = True
    remark: str | None = None


class PublicPoolRuleUpdate(PatchModel):
    model_config = ConfigDict(extra="ignore")

    level: str | None = Field(default=None, min_length=1, max_length=8)
    days: int | None = Field(default=None, gt=0)
    enabled: bool | None = None
    remark: str | None = None

    @field_validator("level", "days", "enabled")
    @classmethod
    def non_null(cls, value):
        if value is None:
            raise ValueError("该规则字段不能为空")
        return value


class TaskRuleInput(CreateModel):
    model_config = ConfigDict(extra="ignore")

    code: str
    name: str
    trigger_type: str
    trigger_config: dict | None = None
    action_config: dict | None = None
    status: str | None = None


class TaskRuleUpdate(PatchModel):
    model_config = ConfigDict(extra="ignore")

    code: str | None = Field(default=None, min_length=1, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    trigger_type: str | None = Field(default=None, min_length=1)
    trigger_config: dict | None = None
    action_config: dict | None = None
    status: str | None = Field(default=None, min_length=1)

    @field_validator("code", "name", "trigger_type", "status")
    @classmethod
    def non_null(cls, value):
        if value is None:
            raise ValueError("该规则字段不能为空")
        return value


class NumberingRuleCreate(CreateModel):
    """新建编号规则（03-API §36）。"""

    model_config = ConfigDict(extra="ignore")

    code: str
    name: str
    prefix: str = ""
    date_format: str = "%Y%m%d"
    seq_length: int = 4
    reset_period: str = "daily"
    enabled: bool = True
    remark: str | None = None


class NumberingRuleUpdate(PatchModel):
    """改编号规则。只传要改的字段。"""

    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    prefix: str | None = None
    date_format: str | None = None
    seq_length: int | None = None
    reset_period: str | None = None
    enabled: bool | None = None
    remark: str | None = None


class DictionaryItemCreate(BaseModel):
    """字典项（03-API §36）。

    受控词表（目前只有客户等级）：同一份名单集中维护、业务表单只允许选启用的项 ——
    否则同一个值会被录成好几种写法，统计口径就散了。

    产品分类**曾经**也走这里，2026-10-15 已退役（改成产品页直接填写）；见
    `model.DictionaryItem.type` 上的说明。
    """

    model_config = ConfigDict(extra="ignore")

    type: str
    code: str
    label: str
    sort_no: int = 0
    enabled: bool = True
    remark: str | None = None


class DictionaryItemUpdate(PatchModel):
    model_config = ConfigDict(extra="ignore")

    label: str | None = None
    sort_no: int | None = None
    enabled: bool | None = None
    remark: str | None = None


class CustomerLevelCreate(BaseModel):
    """客户等级定义（03-API §36）。

    PRD 里等级是 A/B/C/D 人工评定，但"每一级意味着什么"没定义；
    这里把等级做成可维护的数据（名称、说明、是否参与公海回收默认值）。
    """

    model_config = ConfigDict(extra="ignore")

    code: str
    name: str
    sort_no: int = 0
    enabled: bool = True
    remark: str | None = None


class CustomerLevelUpdate(PatchModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    sort_no: int | None = None
    enabled: bool | None = None
    remark: str | None = None


class UnratedRulesInput(BaseModel):
    reminder_days: int | None = Field(default=None, gt=0, le=36500)
    reminder_enabled: bool = False
    recycle_days: int | None = Field(default=None, gt=0, le=36500)
    recycle_enabled: bool = False

    @model_validator(mode="after")
    def enabled_needs_days(self):
        if self.reminder_enabled and self.reminder_days is None:
            raise ValueError("开启未分级跟进提醒前请填写天数")
        if self.recycle_enabled and self.recycle_days is None:
            raise ValueError("开启未分级公海回收前请填写天数")
        return self
