"""里程碑补"责任人 / 来源证据 / 逾期原因"，发货批次补"逾期原因"

方案 :103 要求跟单节点记录「计划日、实际日、责任人、来源证据、逾期原因和状态」，
代码里此前只有计划日与实际日（状态是算出来的），后三项没落地：

- 没有责任人，逾期了不知道该催谁；
- 没有来源证据，事后回看无法还原"当初凭什么这么排"；
- 没有逾期原因，就只能说"第 2 批晚了 5 天"，说不出"**为什么**晚"。
  归因只能由人填——系统从数据里推不出因果，推出来的会被当成事实引用，比空着更糟。

发货批次同样补 overdue_reason：分批造成的延期必须能标在批次上，
否则"因为分批"这句话在系统里没有任何落点。
"""

import sqlalchemy as sa
from alembic import op

revision = "b8e3f5a7c9d1"
down_revision = "a7d2e4f6b8c0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("order_milestones", sa.Column("owner_id", sa.BigInteger(), nullable=True))
    op.add_column("order_milestones", sa.Column("evidence", sa.Text(), nullable=True))
    op.add_column("order_milestones", sa.Column("overdue_reason", sa.Text(), nullable=True))
    op.add_column(
        "order_shipment_batches", sa.Column("overdue_reason", sa.Text(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("order_shipment_batches", "overdue_reason")
    op.drop_column("order_milestones", "overdue_reason")
    op.drop_column("order_milestones", "evidence")
    op.drop_column("order_milestones", "owner_id")
