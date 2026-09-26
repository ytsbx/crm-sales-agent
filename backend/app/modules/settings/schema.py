from pydantic import BaseModel, ConfigDict


class SettingInput(BaseModel):
    key: str
    value: dict | None = None
    description: str | None = None


class PublicPoolRuleInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    level: str
    days: int
    enabled: bool = True
    remark: str | None = None


class TaskRuleInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    code: str
    name: str
    trigger_type: str
    trigger_config: dict | None = None
    action_config: dict | None = None
    status: str | None = None


class NumberingRuleCreate(BaseModel):
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


class NumberingRuleUpdate(BaseModel):
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

    用于产品分类、来源、行业这类"受控词表"：以前是自由文本，
    同一个分类会被录成好几种写法，统计口径就散了。
    """

    model_config = ConfigDict(extra="ignore")

    type: str
    code: str
    label: str
    sort_no: int = 0
    enabled: bool = True
    remark: str | None = None


class DictionaryItemUpdate(BaseModel):
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


class CustomerLevelUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    sort_no: int | None = None
    enabled: bool | None = None
    remark: str | None = None
