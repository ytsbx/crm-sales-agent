"""客户标签与合并入参（03-API §7）。"""

from pydantic import BaseModel, ConfigDict


class TagCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    #: ⚠️ **不在这里写 `max_length`**（2026-10-09 修 P3 文案错）。
    #:
    #: 长度、去空格、非空三件事全部交给 `tags.normalize_tag_name` —— 它是标签名的
    #: 唯一校验入口，文案是「标签名最多 64 个字符，当前 N 个」。
    #: 从前这里也加了 `max_length=64`，于是 pydantic **抢先**拦下、走通用文案，
    #: 而通用文案只能按字段名猜中文（`loc` 里没有模型名），任何一个叫 `name` 的字段
    #: 都会显示成「产品名称」—— 实测提示写成「产品名称内容太长」，
    #: 而接口根本没有产品名称这个入参。
    name: str
    type: str = "custom"
    """标签分组，例如 行业 / 等级 / 渠道。"""
    sort_no: int = 0


class TagUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    #: 同上：长度校验交给 `tags.normalize_tag_name`，不在这里写 `max_length`
    name: str | None = None
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
