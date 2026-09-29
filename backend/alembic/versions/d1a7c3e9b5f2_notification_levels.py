"""通知分级与日报投递（文档 §11.4 验收 24）

主管一天收到大量业务事件时，"每条都即时推"等于把人训练成不看通知。
两个新列：

- `level`：urgent / normal / digest。前两级即时推，digest 攒进日报；
- `digest_at`：该条是随哪次日报发出去的（NULL = 不是日报发的）。

默认值取 `normal`，与分级上线前的投递行为**完全一致**：分级属业务决策
（文档 §九"逐次还是分级投递"列为待批准），迁移不替业务改口径。
"""

import sqlalchemy as sa
from alembic import op

revision = "d1a7c3e9b5f2"
down_revision = "c9e2f4a8b1d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "notifications",
        sa.Column("level", sa.String(length=16), nullable=False, server_default="normal"),
    )
    op.add_column(
        "notifications",
        sa.Column("digest_at", sa.DateTime(timezone=True), nullable=True),
    )
    # 日报任务要按「级别 + 投递状态」捞待发行，给它一个索引
    op.create_index("ix_notifications_level_status", "notifications", ["level", "wecom_status"])


def downgrade() -> None:
    op.drop_index("ix_notifications_level_status", table_name="notifications")
    op.drop_column("notifications", "digest_at")
    op.drop_column("notifications", "level")
