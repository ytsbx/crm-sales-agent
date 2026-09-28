"""站内通知与企业微信通知渠道。

投递状态为什么要落库（PRD §25 要求"站内 + 企微"两个渠道）：
- 只看站内通知，无法回答"这条到底有没有发到企微"；
- 企微投递可能因为对方没配 userid、应用没发消息权限而失败，
  失败原因必须留在同一行上，否则运营只能靠猜。
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, IdMixin

#: 通知渠道
CHANNEL_INAPP = "inapp"
CHANNEL_BOTH = "both"

CHANNEL_LABEL = {
    CHANNEL_INAPP: "仅站内",
    CHANNEL_BOTH: "站内 + 企业微信",
}

#: 企微投递状态。NULL 表示"没走企微渠道"（而不是"发失败了"），
#: 这样一看字段就知道是没发还是发失败。
WECOM_STATUS_LABEL = {
    "pending": "待投递",
    "sent": "已投递",
    "skipped": "未投递（未配置或未绑定企微）",
    "failed": "投递失败",
}


class BusinessEvent(Base, IdMixin):
    """业务事件（文档 §四/验收场景04）：一次真实业务动作一行。

    event_key 由调用点按「动作:对象」确定性生成（如 order:create:42），
    唯一约束封死重复——重放/重试命中同一 key 时整体跳过：
    客户时间线只一条、主管只收一次。notifications 表就是逐接收人的投递记录。
    """

    __tablename__ = "business_events"

    event_key: Mapped[str] = mapped_column(String(128), unique=True)
    business_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    business_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    customer_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class Notification(Base, IdMixin):
    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_user_read", "user_id", "read_at"),
        # 待投递的企微通知要能被扫出来
        Index("ix_notifications_wecom_status", "wecom_status"),
    )

    user_id: Mapped[int] = mapped_column(BigInteger)
    type: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    business_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    business_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # 本次通知实际走哪些渠道
    channel: Mapped[str] = mapped_column(String(16), default=CHANNEL_INAPP)
    # 企微投递状态与失败原因（没走企微渠道时为 NULL）
    wecom_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    wecom_error: Mapped[str | None] = mapped_column(String(255), nullable=True)
    wecom_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 投递重试（文档 §六/API §32：「发送失败保留业务记录并重试通知」）：
    # 失败不是终点。试过几次、下次什么时候再试都落库——界面上能解释
    # "这条为什么没发出去、还会不会自己再试"，而不是只有一行 failed 猜原因。
    # attempts 达到上限后 next_retry_at 置空，停在 failed 等人工补投。
    wecom_attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    wecom_next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(), server_default=func.now(),
        nullable=False,
    )
