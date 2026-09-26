"""customer tags and merge logs

02-ER §5 的 tags / customer_tags / customer_merge_logs：
补齐客户标签（PRD §6.1 列表要展示标签）与客户合并（PRD §6.3）。
查重打分此前已完成，合并是它的必要后续动作。

Revision ID: e9b3c5d7f1a2
Revises: d4a7b1c9e2f3
Create Date: 2026-09-26

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e9b3c5d7f1a2'
down_revision: Union[str, None] = 'd4a7b1c9e2f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'tags',
        sa.Column('name', sa.String(length=64), nullable=False),
        sa.Column('type', sa.String(length=32), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('sort_no', sa.BigInteger(), nullable=False),
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
    )
    op.create_index('ix_tags_type_status', 'tags', ['type', 'status'], unique=False)

    op.create_table(
        'customer_tags',
        sa.Column('customer_id', sa.BigInteger(), nullable=False),
        sa.Column('tag_id', sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ),
        sa.ForeignKeyConstraint(['tag_id'], ['tags.id'], ),
        sa.PrimaryKeyConstraint('customer_id', 'tag_id'),
    )

    op.create_table(
        'customer_merge_logs',
        sa.Column('source_customer_id', sa.BigInteger(), nullable=False),
        sa.Column('target_customer_id', sa.BigInteger(), nullable=False),
        sa.Column('operator_id', sa.BigInteger(), nullable=True),
        sa.Column('merge_snapshot', sa.JSON(), nullable=True),
        sa.Column('moved', sa.JSON(), nullable=True),
        sa.Column('reason', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_customer_merge_logs_source', 'customer_merge_logs', ['source_customer_id'], unique=False
    )
    op.create_index(
        'ix_customer_merge_logs_target', 'customer_merge_logs', ['target_customer_id'], unique=False
    )


def downgrade() -> None:
    op.drop_index('ix_customer_merge_logs_target', table_name='customer_merge_logs')
    op.drop_index('ix_customer_merge_logs_source', table_name='customer_merge_logs')
    op.drop_table('customer_merge_logs')
    op.drop_table('customer_tags')
    op.drop_index('ix_tags_type_status', table_name='tags')
    op.drop_table('tags')
