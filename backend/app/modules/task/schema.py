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

    #: 客户端**读到的版本**（任务响应里的 `updated_at`）。
    #:
    #: 为什么需要（审查 B2-03）：两个请求真正同时发起时，服务器只知道"你要完成"，
    #: 不知道你点按钮时屏幕上是什么样。实测会两个都返回 200，最终落成
    #: "已完成 + 负责人已换人" —— 两个人各自看到"成功"，但"完成的到底是谁那单"
    #: 说不清。带上这个字段后，服务器可以判断"你看到的版本还是当前版本吗"：
    #: 对不上就拒绝并让人刷新重看，而不是替他把两个矛盾的动作都执行掉。
    #:
    #: **兼容性口径**（与主人确认）：带了这个字段就严格校验；没带就退回
    #: 原有的原子守卫（`_guard_task_field` 的终态 + 行锁），老前端不会坏，
    #: 但也享受不到"严格只允许一个成功"的保护。
    expected_updated_at: datetime | None = None

    title: str | None = None
    priority: str | None = None
    # 明确枚举：未知值由 pydantic 直接拒（422），不再写进库里
    status: Literal["pending", "doing", "done", "cancelled"] | None = None
    due_at: datetime | None = None
    owner_id: int | None = None


class TaskVersioned(BaseModel):
    """只需要"我读到的版本"的动作（取消）：本来没有 body，补一个可选的。

    客户端不带这个字段时按兼容口径放行（见 `expected_updated_at` 的说明）。
    """

    model_config = ConfigDict(extra="ignore")

    expected_updated_at: datetime | None = None


class TaskComplete(BaseModel):
    model_config = ConfigDict(extra="ignore")

    #: 客户端**读到的版本**（任务响应里的 `updated_at`）。
    #:
    #: 为什么需要（审查 B2-03）：两个请求真正同时发起时，服务器只知道"你要完成"，
    #: 不知道你点按钮时屏幕上是什么样。实测会两个都返回 200，最终落成
    #: "已完成 + 负责人已换人" —— 两个人各自看到"成功"，但"完成的到底是谁那单"
    #: 说不清。带上这个字段后，服务器可以判断"你看到的版本还是当前版本吗"：
    #: 对不上就拒绝并让人刷新重看，而不是替他把两个矛盾的动作都执行掉。
    #:
    #: **兼容性口径**（与主人确认）：带了这个字段就严格校验；没带就退回
    #: 原有的原子守卫（`_guard_task_field` 的终态 + 行锁），老前端不会坏，
    #: 但也享受不到"严格只允许一个成功"的保护。
    expected_updated_at: datetime | None = None

    completion_note: str | None = None


class TaskAssign(BaseModel):
    """指派 / 改派任务负责人（03-API §25）。"""

    model_config = ConfigDict(extra="ignore")

    owner_id: int
    reason: str | None = None
    #: 客户端**读到的版本**（任务响应里的 `updated_at`）。
    #:
    #: 为什么需要（审查 B2-03）：两个请求真正同时发起时，服务器只知道"你要完成"，
    #: 不知道你点按钮时屏幕上是什么样。实测会两个都返回 200，最终落成
    #: "已完成 + 负责人已换人" —— 两个人各自看到"成功"，但"完成的到底是谁那单"
    #: 说不清。带上这个字段后，服务器可以判断"你看到的版本还是当前版本吗"：
    #: 对不上就拒绝并让人刷新重看，而不是替他把两个矛盾的动作都执行掉。
    #:
    #: **兼容性口径**（与主人确认）：带了这个字段就严格校验；没带就退回
    #: 原有的原子守卫（`_guard_task_field` 的终态 + 行锁），老前端不会坏，
    #: 但也享受不到"严格只允许一个成功"的保护。
    expected_updated_at: datetime | None = None


class TaskBatchComplete(BaseModel):
    """批量完成任务（03-API §25）。

    单条失败不影响其余：返回成功/跳过清单与原因，
    让操作的人知道哪几条没成、为什么（与线索批量分配同一口径）。
    """

    task_ids: list[int]
    completion_note: str | None = None
