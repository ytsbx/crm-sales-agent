from pydantic import BaseModel


class BindCustomerRequest(BaseModel):
    """把待归一联系人关联到已有客户。"""

    customer_id: int
    is_primary: bool | None = None


class CreateCustomerRequest(BaseModel):
    """给待归一联系人建新客户。名称必填，其余走客户模块同一套字段。"""

    name: str
    short_name: str | None = None
    region: str | None = None
    address: str | None = None
    level: str | None = None
    source: str | None = None
    remark: str | None = None


class IgnoreContactRequest(BaseModel):
    reason: str | None = None


class TransferRequest(BaseModel):
    """离职继承。"""

    handover_user_id: int
    takeover_user_id: int
    # 企微侧客户关系是否一并交接；没配外部联系人 secret 时置 false 只转 CRM 侧
    transfer_wecom: bool = True
    item_assignees: dict[str, int] | None = None
    """**逐项接手人**（业务方 2026-10-06 定："交接清单可以逐项调整"）。

    键 = `"{kind}:{business_id}"`（与交接清单里那一项一一对应，
    例如 `"customer:12"`、`"sample:5"`），值 = 该项实际交给谁。
    没列到的项全部交给 `takeover_user_id`。
    这样"某个客户本来就该归另一位同事"不用再单独操作一次。
    """
