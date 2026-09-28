"""通知投递失败可重试（CRM 完整实现方案 §六/场景04 通知可靠性）

notifications 加两列：
- wecom_attempts：企微投递尝试次数（成功也算一次），用于"试过几次"与上限判定；
- wecom_next_retry_at：下次重试时间，退避策略由 notification_retry 配置给；
  达到上限后置空——停在被人工看见的 failed，不再无限重试。

背景：此前失败行写成 failed 后就再也不会被选中（dispatch_pending 只捞
pending），而业务事件去重键已经占上，人工重跑业务动作也不会补发——
通知静默丢失。此迁移为"按退避自动重试 + 人工补投"提供字段。

向后兼容：既有 failed 行 attempts=0、next_retry_at 为空，迁移后按
"未超上限且到期"直接进入第一轮重试，不需要人工翻数据。
"""

import sqlalchemy as sa
from alembic import op

revision = "c6a1e8d4f2b7"
down_revision = "b5d7f9a1c3e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "notifications",
        sa.Column("wecom_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "notifications",
        sa.Column("wecom_next_retry_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("notifications", "wecom_next_retry_at")
    op.drop_column("notifications", "wecom_attempts")
