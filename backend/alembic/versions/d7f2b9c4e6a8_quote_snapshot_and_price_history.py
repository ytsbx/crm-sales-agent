"""产品报价中心：快照与历史价标识

- quote_items 加 customer_level_snapshot / price_source：
  方案 §5/A09 明列"已有报价应保留当时的客户等级、价格来源"——
  此前这两项只存在于创建时的瞬时 warnings 里，事后无法回答
  "这一版当初按哪条规则带的价"。
- customer_price_rules 加 status：历史专属价（A14）需要与当前售价
  区分且不参与匹配/冲突检查；价格规则本来就有 status，这里补齐对等能力。

Revision ID: d7f2b9c4e6a8
Revises: b3e7a9c1d4f6
Create Date: 2026-09-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd7f2b9c4e6a8'
down_revision: Union[str, None] = 'b3e7a9c1d4f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'quote_items',
        sa.Column('customer_level_snapshot', sa.String(8), nullable=True),
    )
    op.add_column(
        'quote_items',
        sa.Column('price_source', sa.String(16), nullable=True),
    )
    op.add_column(
        'customer_price_rules',
        sa.Column('status', sa.String(16), nullable=False, server_default='active'),
    )


def downgrade() -> None:
    op.drop_column('customer_price_rules', 'status')
    op.drop_column('quote_items', 'price_source')
    op.drop_column('quote_items', 'customer_level_snapshot')
