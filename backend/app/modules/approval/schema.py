"""审批相关入参（03-API §23）。

这个模块原先没有 schema 文件：approve/reject 把 comment 当 query 参数收，
简单但和其余模块的写法不一致。新增的三个接口改成标准的 body 入参，
新老并存，不动既有接口的契约。
"""

from pydantic import BaseModel, ConfigDict, Field
from app.core.patch_schema import PatchModel



class ApprovalTransfer(BaseModel):
    """转交给别人审批。

    主管临时出差时把待办交出去，而不是让别人用自己的账号批 ——
    审批记录里必须留下"谁转给了谁"，责任才清楚。
    """

    to_user_id: int
    comment: str | None = None


class ApprovalWithdraw(BaseModel):
    """申请人撤回自己提交的审批。"""

    comment: str | None = None


class ApprovalDefinitionCreate(BaseModel):
    """新建审批定义。"""

    model_config = ConfigDict(extra="ignore")

    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    business_type: str = Field(min_length=1, max_length=64)
    status: str = "active"
    config_json: dict | None = None


class ApprovalDefinitionUpdate(PatchModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    business_type: str | None = None
    status: str | None = None
    config_json: dict | None = None
