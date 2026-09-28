"""目标管理（领导模块⑧）：sales_targets 表

Revision ID: c3e7f1a9b5d2
Revises: b8f4a6d2c0e9
Create Date: 2026-09-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3e7f1a9b5d2'
down_revision: Union[str, None] = 'b8f4a6d2c0e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'sales_targets',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('period', sa.String(7), nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=True),
        sa.Column('new_customer_target', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('sales_target', sa.Numeric(16, 2), nullable=False, server_default='0'),
        sa.Column('remark', sa.String(255), nullable=True),
        sa.Column('created_by', sa.BigInteger(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_sales_targets_period_user', 'sales_targets', ['period', 'user_id'])


def downgrade() -> None:
    op.drop_index('ix_sales_targets_period_user', table_name='sales_targets')
    op.drop_table('sales_targets')
