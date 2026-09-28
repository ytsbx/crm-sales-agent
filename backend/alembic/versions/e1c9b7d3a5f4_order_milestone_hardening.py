"""跟单里程碑加固：并发唯一约束 + 逾期提醒凭证 + 初始化留痕

- (order_id, node) 唯一：并发首次打开同一订单的跟单 Tab 会同时初始化六节点，
  此前只有普通索引，双初始化的脏数据会永久留存；
- overdue_notified_at：逾期提醒"每节点只推一次"的凭证；
- created_by：初始化是 GET 带的副作用，留痕到人，将来可查"这六行谁初始化的"。

Revision ID: e1c9b7d3a5f4
Revises: d6a2c4f8e1b3
Create Date: 2026-09-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e1c9b7d3a5f4'
down_revision: Union[str, None] = 'd6a2c4f8e1b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'order_milestones',
        sa.Column('overdue_notified_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column('order_milestones', sa.Column('created_by', sa.BigInteger(), nullable=True))
    op.create_unique_constraint(
        'uq_order_milestones_order_node', 'order_milestones', ['order_id', 'node']
    )


def downgrade() -> None:
    op.drop_constraint('uq_order_milestones_order_node', 'order_milestones', type_='unique')
    op.drop_column('order_milestones', 'created_by')
    op.drop_column('order_milestones', 'overdue_notified_at')
