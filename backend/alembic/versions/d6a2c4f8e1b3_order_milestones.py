"""跟单里程碑（领导模块⑤）：order_milestones 表

Revision ID: d6a2c4f8e1b3
Revises: c3e7f1a9b5d2
Create Date: 2026-09-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd6a2c4f8e1b3'
down_revision: Union[str, None] = 'c3e7f1a9b5d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'order_milestones',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('order_id', sa.BigInteger(), sa.ForeignKey('sales_orders.id'), nullable=False),
        sa.Column('node', sa.String(32), nullable=False),
        sa.Column('planned_date', sa.Date(), nullable=True),
        sa.Column('actual_date', sa.Date(), nullable=True),
        sa.Column('remark', sa.String(255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_order_milestones_order', 'order_milestones', ['order_id'])


def downgrade() -> None:
    op.drop_index('ix_order_milestones_order', table_name='order_milestones')
    op.drop_table('order_milestones')
