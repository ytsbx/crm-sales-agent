"""客户标签与合并入参（03-API §7）。"""

from pydantic import BaseModel, ConfigDict, Field


class TagCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    #: `tags.name` 是 `String(64)`：入参侧先拦，避免撞数据库约束报 500
    #: （空/纯空格仍由 `tags.normalize_tag_name` 统一判，好给一致的中文提示）
    name: str = Field(max_length=64)
    type: str = "custom"
    """标签分组，例如 行业 / 等级 / 渠道。"""
    sort_no: int = 0


class TagUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    #: 同上：长度上限在入参侧拦一道（审查 B2-05）
    name: str | None = Field(default=None, max_length=64)
    type: str | None = None
    status: str | None = None
    sort_no: int | None = None


class CustomerTagAttach(BaseModel):
    model_config = ConfigDict(extra="ignore")

    tag_ids: list[int]


class CustomerBatchTag(BaseModel):
    model_config = ConfigDict(extra="ignore")

    customer_ids: list[int]
    tag_ids: list[int]
    mode: str = "add"
    """add = 追加；replace = 先清空再打；remove = 摘除。"""


class CustomerMergeRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source_customer_id: int
    """被合并（合并后消失）的客户。"""
    target_customer_id: int
    """保留的客户。"""
    reason: str | None = None
    resolutions: dict[str, str] | None = None
    """冲突处理口径，键见「合并影响」返回的 `blocking`。

    例：`{"customer_price": "keep_target"}` = 两边同一个 SKU 定了不同价时
    保留目标客户的价格（来源那几条转为历史资料）。**不给口径就拒绝合并**，
    不替业务默认选一个。
    """


class CustomerBatchTransfer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    customer_ids: list[int]
    owner_id: int | None = None
    """为空表示放入公海。"""
    reason: str | None = None
