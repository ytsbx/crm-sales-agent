from pydantic import BaseModel


class WeComEvents(BaseModel):
    approval: bool | None = None
    task: bool | None = None
    payment: bool | None = None
    # 业务动作自动留痕推主管（报价提交/打样/下单，领导六阶段口径）
    followup: bool | None = None


class NotificationSettingUpdate(BaseModel):
    """通知渠道设置（03-API §33 PATCH /notification-settings）。

    只传要改的字段；没传的保持原值。
    """

    model_config = {"extra": "ignore"}

    inapp_enabled: bool | None = None
    wecom_enabled: bool | None = None
    wecom_events: WeComEvents | None = None
