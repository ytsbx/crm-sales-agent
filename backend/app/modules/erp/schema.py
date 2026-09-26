from pydantic import BaseModel


class OrderPushRequest(BaseModel):
    """按 CRM 订单号推送。"""

    order_no: str | None = None


class StatusWebhookRequest(BaseModel):
    """对方系统推送的状态变更。

    用 CRM 订单号或外部单号定位订单，两者给一个即可；
    status 是**对方系统的口径**，由 Adapter 的映射表翻译成 CRM 状态。
    """

    order_no: str | None = None
    erp_order_id: str | None = None
    status: str
    remark: str | None = None
