from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ExemptionReason = Literal["customer_declined", "business_closed", "waiting_external"]
EXEMPTION_LABELS = {"customer_declined": "客户明确拒绝", "business_closed": "业务已关闭", "waiting_external": "等待外部固定节点"}


def validate_plan(next_action, planned_at, exemption_reason):
    if exemption_reason:
        if next_action or planned_at:
            raise ValueError("免填原因与下一动作、下次跟进时间不能同时填写")
    elif not next_action or not planned_at:
        raise ValueError("请填写下一动作和下次跟进时间，或选择免填原因")


class FollowUpCreate(BaseModel):
    content: str = Field(min_length=1)
    followup_type: str = "电话"
    customer_id: int | None = None
    contact_id: int | None = None
    lead_id: int | None = None
    opportunity_id: int | None = None
    quote_id: int | None = None
    order_id: int | None = None
    customer_feedback: str | None = None
    next_action: str | None = Field(default=None, max_length=200)
    exemption_reason: ExemptionReason | None = None
    request_key: str | None = Field(default=None, min_length=1, max_length=96)
    # 兼容旧客户端字段；有有效计划时自动建任务，免填时不建。
    create_task: bool = False
    task_title: str | None = Field(default=None, max_length=200)
    task_due_at: datetime | None = None

    @field_validator("content", "next_action", "task_title", "request_key", mode="before")
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("task_due_at")
    @classmethod
    def aware_due(cls, value):
        if value is not None:
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("下次跟进时间须包含时区")
            return value.astimezone(UTC)
        return value

    @model_validator(mode="after")
    def plan_required(self):
        validate_plan(self.next_action, self.task_due_at, self.exemption_reason)
        if self.exemption_reason and self.create_task:
            raise ValueError("选择免填原因时不创建后续任务")
        return self


class FollowUpUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    content: str | None = Field(default=None, min_length=1)
    followup_type: str | None = None
    customer_feedback: str | None = None
    next_action: str | None = Field(default=None, max_length=200)
    task_due_at: datetime | None = None
    exemption_reason: ExemptionReason | None = None

    _strip = field_validator("content", "next_action", mode="before")(FollowUpCreate.strip_text.__func__)
    _due = field_validator("task_due_at")(FollowUpCreate.aware_due.__func__)

    @field_validator("content")
    @classmethod
    def content_not_null(cls, value):
        if value is None:
            raise ValueError("跟进内容不能为空")
        return value


class FollowUpNextTask(BaseModel):
    """从一条跟进记录生成后续任务（03-API §24）。

    与「记跟进时顺手建任务」（FollowUpCreate.create_task）的区别：
    那是记录当下就决定下次动作；这是在**已经发生过的**跟进上补一个后续任务，
    典型场景是回头看历史跟进时发现"当时说好要回访但没人建任务"。
    """

    model_config = ConfigDict(extra="ignore")

    title: str | None = None
    due_at: datetime

    _due = field_validator("due_at")(FollowUpCreate.aware_due.__func__)
    owner_id: int | None = None
    task_type: str = "followup"
    priority: str = "normal"
