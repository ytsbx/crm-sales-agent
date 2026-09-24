from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


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
    next_action: str | None = None
    # 记跟进时顺手建一条后续任务（UI §16 的「是否创建后续任务」）
    create_task: bool = False
    task_title: str | None = None
    task_due_at: datetime | None = None


class FollowUpUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    content: str | None = None
    followup_type: str | None = None
    customer_feedback: str | None = None
    next_action: str | None = None
