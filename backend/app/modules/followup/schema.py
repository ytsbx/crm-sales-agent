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


class FollowUpNextTask(BaseModel):
    """从一条跟进记录生成后续任务（03-API §24）。

    与「记跟进时顺手建任务」（FollowUpCreate.create_task）的区别：
    那是记录当下就决定下次动作；这是在**已经发生过的**跟进上补一个后续任务，
    典型场景是回头看历史跟进时发现"当时说好要回访但没人建任务"。
    """

    model_config = ConfigDict(extra="ignore")

    title: str | None = None
    due_at: datetime
    owner_id: int | None = None
    task_type: str = "followup"
    priority: str = "normal"
