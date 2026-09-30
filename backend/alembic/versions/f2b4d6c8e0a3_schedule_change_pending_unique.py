"""同一订单只允许一张待确认的交期变更单（部分唯一索引）

应用层已经在 create_change 里挡了一道，这里再上库级约束：两张 pending 并存时
各自确认会互相覆盖计划日，而 old_delivery_date 的档案也随之失真
（后者以"前者已改过的交期"为基准）。应用层的检查挡不住并发插入，索引可以。

用**部分**唯一索引（where status='pending'）：确认过的历史单可以有任意多张，
这正是"保留修改前后版本"要的。
"""

from alembic import op

revision = "f2b4d6c8e0a3"
down_revision = "e1a3c5b7d9f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "create unique index uq_schedule_change_pending_per_order "
        "on order_schedule_changes (order_id) where status = 'pending'"
    )


def downgrade() -> None:
    op.execute("drop index if exists uq_schedule_change_pending_per_order")
