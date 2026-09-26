"""公海池入参（03-API §9）。"""

from pydantic import BaseModel, ConfigDict


class PoolAssignRequest(BaseModel):
    """把公海里的客户/线索指派给某个负责人。

    `owner_id` 允许为空：清空负责人等于放回公海，这与客户模块
    `transfer_customer` 的语义一致（一处实现两处生效）。
    """

    model_config = ConfigDict(extra="ignore")

    owner_id: int | None = None
    reason: str | None = None
