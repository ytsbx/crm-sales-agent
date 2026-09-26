"""sample: request / item / shipment

PRD §19 与 02-ER §13 的样品模块：申请单 / 明细 / 寄样记录。
补的是「商机有样品阶段但没有样品实体」这个断层。

Revision ID: d4a7b1c9e2f3
Revises: c8f1a2d4e7b9
Create Date: 2026-09-26

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4a7b1c9e2f3'
down_revision: Union[str, None] = 'c8f1a2d4e7b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'sample_requests',
        sa.Column('opportunity_id', sa.BigInteger(), nullable=True),
        sa.Column('customer_id', sa.BigInteger(), nullable=True),
        sa.Column('contact_id', sa.BigInteger(), nullable=True),
        sa.Column('owner_id', sa.BigInteger(), nullable=True),
        sa.Column('status', sa.String(length=24), nullable=False),
        sa.Column('remark', sa.Text(), nullable=True),
        sa.Column('reject_reason', sa.String(length=255), nullable=True),
        sa.Column('requested_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('shipped_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('signed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('feedback', sa.Text(), nullable=True),
        sa.Column('created_by', sa.BigInteger(), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(['opportunity_id'], ['opportunities.id'], ),
        sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_sample_requests_opportunity', 'sample_requests', ['opportunity_id'], unique=False
    )
    op.create_index(
        'ix_sample_requests_customer', 'sample_requests', ['customer_id'], unique=False
    )
    op.create_index(
        'ix_sample_requests_owner_status',
        'sample_requests',
        ['owner_id', 'status'],
        unique=False,
    )
    op.create_index('ix_sample_requests_status', 'sample_requests', ['status'], unique=False)

    op.create_table(
        'sample_items',
        sa.Column('sample_request_id', sa.BigInteger(), nullable=False),
        sa.Column('sku_id', sa.BigInteger(), nullable=False),
        sa.Column('quantity', sa.Numeric(precision=16, scale=3), nullable=False),
        sa.Column('remark', sa.String(length=255), nullable=True),
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(['sample_request_id'], ['sample_requests.id'], ),
        sa.ForeignKeyConstraint(['sku_id'], ['skus.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_sample_items_request', 'sample_items', ['sample_request_id'], unique=False
    )

    op.create_table(
        'sample_shipments',
        sa.Column('sample_request_id', sa.BigInteger(), nullable=False),
        sa.Column('carrier', sa.String(length=64), nullable=True),
        sa.Column('tracking_no', sa.String(length=64), nullable=True),
        sa.Column('shipping_fee', sa.Numeric(precision=16, scale=2), nullable=False),
        sa.Column('shipped_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('signed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(['sample_request_id'], ['sample_requests.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_sample_shipments_request', 'sample_shipments', ['sample_request_id'], unique=False
    )


def downgrade() -> None:
    op.drop_index('ix_sample_shipments_request', table_name='sample_shipments')
    op.drop_table('sample_shipments')
    op.drop_index('ix_sample_items_request', table_name='sample_items')
    op.drop_table('sample_items')
    op.drop_index('ix_sample_requests_status', table_name='sample_requests')
    op.drop_index('ix_sample_requests_owner_status', table_name='sample_requests')
    op.drop_index('ix_sample_requests_customer', table_name='sample_requests')
    op.drop_index('ix_sample_requests_opportunity', table_name='sample_requests')
    op.drop_table('sample_requests')
