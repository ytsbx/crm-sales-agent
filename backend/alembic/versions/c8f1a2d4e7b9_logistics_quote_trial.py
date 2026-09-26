"""logistics: quote trial calculation

为 PRD §14「物流试算」与 02-ER §10 补：
- logistics_rates 增加体积单价、起运地、时效区间、备注（只按重量算会低估抛货）
- 新建 logistics_quotes 保存试算结果（发出去的报价要能回溯当时按什么算的）

注意：`down_revision` 挂的是 `a4034e4ebed1`（汇率那支），但这支的 revision id
在字典序上排在 `f2c8d4e6a1b3` 之后，所以 alembic 的实际执行顺序是它**最后**跑，
于是把 models 早先建的 JSONB 又用普通 JSON 覆盖了一遍（见 b7d1e4f8c2a9 的修复迁移）。
这里不改 down_revision：历史记录应当保持原样，改它反而会伪造执行顺序。

Revision ID: c8f1a2d4e7b9
Revises: a4034e4ebed1
Create Date: 2026-09-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'c8f1a2d4e7b9'
down_revision: Union[str, None] = 'a4034e4ebed1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 与 app.core.base.JSONType 对齐：PG 下应为 JSONB。
# 这个文件原来写的是 sa.JSON()，虽然 down_revision 挂错链导致它最后执行时
# 又"盖"在了 models 的 JSONB 上，但文件本身应当是对的。
JSONType = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')


def upgrade() -> None:
    # 运费费率表补列：体积计费与时效区间
    op.add_column(
        'logistics_rates',
        sa.Column('origin_region', sa.String(length=64), nullable=True),
    )
    op.add_column(
        'logistics_rates',
        sa.Column('unit_price_per_volume', sa.Numeric(precision=10, scale=4), nullable=True),
    )
    op.add_column('logistics_rates', sa.Column('eta_days_max', sa.BigInteger(), nullable=True))
    op.add_column('logistics_rates', sa.Column('remark', sa.String(length=255), nullable=True))

    op.create_table(
        'logistics_quotes',
        sa.Column('customer_id', sa.BigInteger(), nullable=True),
        sa.Column('opportunity_id', sa.BigInteger(), nullable=True),
        sa.Column('sku_id', sa.BigInteger(), nullable=True),
        sa.Column('quantity', sa.Numeric(precision=16, scale=3), nullable=True),
        sa.Column('origin', sa.String(length=64), nullable=True),
        sa.Column('destination', sa.String(length=64), nullable=True),
        sa.Column('shipping_method', sa.String(length=32), nullable=False),
        sa.Column('chargeable_weight', sa.Numeric(precision=16, scale=4), nullable=False),
        sa.Column('actual_weight', sa.Numeric(precision=16, scale=4), nullable=False),
        sa.Column('volume', sa.Numeric(precision=16, scale=4), nullable=False),
        sa.Column('currency', sa.String(length=8), nullable=False),
        sa.Column('amount', sa.Numeric(precision=16, scale=2), nullable=False),
        sa.Column('unit_price', sa.Numeric(precision=16, scale=4), nullable=True),
        sa.Column('eta_days', sa.BigInteger(), nullable=True),
        sa.Column('provider', sa.String(length=64), nullable=True),
        sa.Column('raw_data', JSONType, nullable=True),
        sa.Column('created_by', sa.BigInteger(), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ),
        sa.ForeignKeyConstraint(['sku_id'], ['skus.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_logistics_quotes_customer', 'logistics_quotes', ['customer_id'], unique=False
    )
    op.create_index(
        'ix_logistics_quotes_opportunity', 'logistics_quotes', ['opportunity_id'], unique=False
    )


def downgrade() -> None:
    op.drop_index('ix_logistics_quotes_opportunity', table_name='logistics_quotes')
    op.drop_index('ix_logistics_quotes_customer', table_name='logistics_quotes')
    op.drop_table('logistics_quotes')
    op.drop_column('logistics_rates', 'remark')
    op.drop_column('logistics_rates', 'eta_days_max')
    op.drop_column('logistics_rates', 'unit_price_per_volume')
    op.drop_column('logistics_rates', 'origin_region')
