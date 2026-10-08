from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from app.core.patch_schema import PatchModel


#: 任务状态**唯一的一份枚举**（第九批 §9.3）。
#:
#: 此前 `TaskUpdate.status` 是裸 `str`、库里也是裸 `String(16)`，于是
#: `status="whatever"` 能存进去；而"完成/取消/指派"三个专门接口各有各的判定，
#: 普通编辑却能直接写 status 与 owner_id —— 同一个动作换个入口就放行了。
TASK_STATUSES = ("pending", "doing", "done", "cancelled")


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    task_type: str | None = None
    customer_id: int | None = None
    contact_id: int | None = None
    lead_id: int | None = None
    opportunity_id: int | None = None
    quote_id: int | None = None
    order_id: int | None = None
    owner_id: int | None = None
    priority: str = "normal"
    due_at: datetime | None = None


class TaskUpdate(PatchModel):
    model_config = ConfigDict(extra="ignore")

    title: str | None = None
    priority: str | None = None
    # 明确枚举：未知值由 pydantic 直接拒（422），不再写进库里
    status: Literal["pending", "doing", "done", "cancelled"] | None = None
    due_at: datetime | None = None
    owner_id: int | None = None


class TaskComplete(BaseModel):
    completion_note: str | None = None


class TaskAssign(BaseModel):
    """指派 / 改派任务负责人（03-API §25）。"""

    model_config = ConfigDict(extra="ignore")

    owner_id: int
    reason: str | None = None


class TaskBatchComplete(BaseModel):
    """批量完成任务（03-API §25）。

    单条失败不影响其余：返回成功/跳过清单与原因，
    让操作的人知道哪几条没成、为什么（与线索批量分配同一口径）。
    """

    task_ids: list[int]
    completion_note: str | None = None
