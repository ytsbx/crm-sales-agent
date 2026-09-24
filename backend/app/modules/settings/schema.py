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
