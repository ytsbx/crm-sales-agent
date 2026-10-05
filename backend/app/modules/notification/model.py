"""站内通知与企业微信通知渠道。

投递状态为什么要落库（PRD §25 要求"站内 + 企微"两个渠道）：
- 只看站内通知，无法回答"这条到底有没有发到企微"；
- 企微投递可能因为对方没配 userid、应用没发消息权限而失败，
  失败原因必须留在同一行上，否则运营只能靠猜。
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, IdMixin, JSONType

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

#: 通知分级（文档 §11.4 验收 24）。
#:
#: 主管一天会收到大量业务事件，"每条都即时推"等于把人训练成不看通知。
#: 分级的作用是把"要不要立刻打断人"从业务代码里抽出来，变成可配置的策略：
#:
#: - `urgent`：**即时**推送，且不进日报——紧急项不能被日报延误；
#: - `normal`：即时推送（与分级功能上线前的行为一致）；
#: - `digest`：不即时推，攒到日报里合成一条发。
#:
#: 无论哪一级，站内那一行**都在**：事件本身始终可查，分级只影响"什么时候、
#: 以几条消息的形式"推给企微。分级策略由业务批准后配置，见 service.level_policy。
LEVEL_URGENT = "urgent"
LEVEL_NORMAL = "normal"
LEVEL_DIGEST = "digest"

LEVEL_LABEL = {
    LEVEL_URGENT: "紧急（即时推送）",
    LEVEL_NORMAL: "普通（即时推送）",
    LEVEL_DIGEST: "日报（攒批投递）",
}


class BusinessEvent(Base, IdMixin):
    """业务事件（文档 §四/验收场景04）：一次真实业务动作一行。

    event_key 由调用点按「动作:对象」确定性生成（如 order:create:42），
    唯一约束封死重复——重放/重试命中同一 key 时整体跳过：
    客户时间线只一条、主管只收一次。notifications 表就是逐接收人的投递记录。
    """

    __tablename__ = "business_events"
    __table_args__ = (Index(
        "ix_business_events_pending_notification", "id",
        postgresql_where=text("notification_payload IS NOT NULL AND notification_processed_at IS NULL"),
    ),)

    event_key: Mapped[str] = mapped_column(String(128), unique=True)
    business_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    business_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    customer_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # 主管通知待办随业务事实提交；站内通知生成失败后可重试，不重做业务动作。
    notification_payload: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    notification_processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notification_error: Mapped[str | None] = mapped_column(String(255), nullable=True)


class Notification(Base, IdMixin):
    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("business_event_id", "user_id", name="uq_notification_event_recipient"),
        Index("ix_notifications_user_read", "user_id", "read_at"),
        # 待投递的企微通知要能被扫出来
        Index("ix_notifications_wecom_status", "wecom_status"),
        # 日报任务按「级别 + 投递状态」捞待发行（验收 24）
        Index("ix_notifications_level_status", "level", "wecom_status"),
    )

    business_event_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    type: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    business_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    business_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # 分级（文档 §11.4 验收 24）：决定"即时推"还是"攒进日报"。
    # 默认 normal = 与分级上线前的行为完全一致，不擅自改变投递策略。
    level: Mapped[str] = mapped_column(
        String(16), default=LEVEL_NORMAL, server_default=LEVEL_NORMAL, nullable=False
    )
    # 该条是随哪次日报发出去的（NULL = 不是日报发的）。
    # 有它才能回答"主管说没看到某条"到底是没推、还是夹在日报里推的。
    digest_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
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
