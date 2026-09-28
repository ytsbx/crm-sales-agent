"""定制询价库（领导模块③：产品知识库 · 定制询价类）

沉淀"客户问了但我们还没有标准产品"的定制询价，作为找开发方向的数据源。

Revision ID: b8f4a6d2c0e9
Revises: d7f2b9c4e6a8
Create Date: 2026-09-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b8f4a6d2c0e9'
down_revision: Union[str, None] = 'd7f2b9c4e6a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'custom_inquiries',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('title', sa.String(200), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('customer_id', sa.BigInteger(), nullable=True),
        sa.Column('contact_id', sa.BigInteger(), nullable=True),
        sa.Column('opportunity_id', sa.BigInteger(), nullable=True),
        sa.Column('quantity', sa.Numeric(16, 3), nullable=True),
        sa.Column('target_price', sa.Numeric(16, 2), nullable=True),
        sa.Column('status', sa.String(16), nullable=False, server_default='open'),
        sa.Column('remark', sa.Text(), nullable=True),
        sa.Column('extra', sa.JSON(), nullable=True),
        sa.Column('created_by', sa.BigInteger(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_custom_inquiries_customer', 'custom_inquiries', ['customer_id'])
    op.create_index('ix_custom_inquiries_status', 'custom_inquiries', ['status'])


def downgrade() -> None:
    op.drop_index('ix_custom_inquiries_status', table_name='custom_inquiries')
    op.drop_index('ix_custom_inquiries_customer', table_name='custom_inquiries')
    op.drop_table('custom_inquiries')
