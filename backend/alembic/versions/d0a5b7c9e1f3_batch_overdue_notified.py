"""发货批次补"逾期提醒过"的凭证（方案 :105 / 场景13）

批次逾期提醒走的是**旁路**（不动节点口径，见 15 号清单 §2-1 的取舍）：
批次不是跟单节点，现有的每日扫描只认 order_milestones，所以"第 2 批过了计划日
还没发"此前不会有任何提醒。

这个列就是那条旁路的去重凭证，与里程碑的 overdue_notified_at 同一套做法：
推过就不再推，避免每天一封骚扰；批次后来真的发了也不会重推旧状态。
"""

import sqlalchemy as sa
from alembic import op

revision = "d0a5b7c9e1f3"
down_revision = "c9f4a6b8d0e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "order_shipment_batches",
        sa.Column("overdue_notified_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("order_shipment_batches", "overdue_notified_at")
