"""交期变更单的作废字段 + 钉钉的"本次尝试时间"

两件事一起做，都是自查发现的：

1. **变更单缺作废出口**：库上有"一单只允许一张 pending"的部分唯一索引
   （f2b4d6c8e0a3 加的），但我没给作废路径——一张没人确认的变更单会**永久堵死**
   这个订单之后所有的交期变更，除了改库没有出路。而错误文案还写着
   "请先确认或作废它"，指的是一条不存在的路。补三个字段：作废原因、作废人、作废时间。

2. **钉钉重试覆盖了 created_at**：判断"这次尝试是不是卡住了"需要用"本次尝试时间"，
   我图省事复用了 created_at，于是"这条审批单最早什么时候发起的"被改写成
   最后一次重试的时间。补一个 last_attempt_at，两个语义分开。
"""

import sqlalchemy as sa
from alembic import op

revision = "a3c5e7b9d1f4"
down_revision = "f2b4d6c8e0a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("order_schedule_changes", sa.Column("cancel_reason", sa.Text(), nullable=True))
    op.add_column(
        "order_schedule_changes", sa.Column("cancelled_by", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "order_schedule_changes",
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "oa_instances", sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True)
    )
    # 存量行：把 created_at 抄成 last_attempt_at，避免"老行没有尝试时间"导致
    # 卡住判定永远不生效（它们在语义上就是那次尝试的时间）
    op.execute("update oa_instances set last_attempt_at = created_at where last_attempt_at is null")


def downgrade() -> None:
    op.drop_column("oa_instances", "last_attempt_at")
    op.drop_column("order_schedule_changes", "cancelled_at")
    op.drop_column("order_schedule_changes", "cancelled_by")
    op.drop_column("order_schedule_changes", "cancel_reason")
