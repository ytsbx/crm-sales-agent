"""order_milestones 外键级联删除

Revision ID: 8c6d39b1a7c8
Revises: d0f4a8c2e6b1
Create Date: 2026-10-12 13:17:41.240099
"""
from typing import Sequence, Union

from alembic import op


revision: str = '8c6d39b1a7c8'
down_revision: Union[str, None] = 'd0f4a8c2e6b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 第三批审查 P2：回款/应收改动会同步生成 order_milestones 行。
    # 从属表外键按项目约定加 CASCADE，避免删 sales_orders 时批量套件撞外键。
    op.drop_constraint('order_milestones_order_id_fkey', 'order_milestones', type_='foreignkey')
    op.create_foreign_key(
        'order_milestones_order_id_fkey',
        'order_milestones', 'sales_orders',
        ['order_id'], ['id'],
        ondelete='CASCADE',
    )


def downgrade() -> None:
    op.drop_constraint('order_milestones_order_id_fkey', 'order_milestones', type_='foreignkey')
    op.create_foreign_key(
        'order_milestones_order_id_fkey',
        'order_milestones', 'sales_orders',
        ['order_id'], ['id'],
    )
