"""numbering rules and atomic sequences

PRD §2.6 把「编号规则」列为系统管理员能力，03-API §36 要求
`GET/POST /numbering-rules`、`PATCH /numbering-rules/{id}`。

原来单号是硬编码 `Q{YYYYMMDD}{4位}` 并用 `count(*)+1` 取流水，
删过历史单就重号、并发会撞（`sales_orders.order_no` 唯一约束会直接 500）。
新建两张表把规则做成数据、把计数器做成可加行锁的资源。

Revision ID: 8f3c1a7e5b92
Revises: 6e2b8d4f1c37
Create Date: 2026-09-26

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '8f3c1a7e5b92'
down_revision: Union[str, None] = '6e2b8d4f1c37'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'numbering_rules',
        sa.Column('code', sa.String(length=32), nullable=False),
        sa.Column('name', sa.String(length=64), nullable=False),
        sa.Column('prefix', sa.String(length=16), nullable=False, server_default=''),
        sa.Column('date_format', sa.String(length=32), nullable=False, server_default='%Y%m%d'),
        sa.Column('seq_length', sa.BigInteger(), nullable=False, server_default='4'),
        sa.Column('reset_period', sa.String(length=16), nullable=False, server_default='daily'),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('remark', sa.String(length=255), nullable=True),
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_numbering_rules_code', 'numbering_rules', ['code'], unique=True)

    op.create_table(
        'number_sequences',
        sa.Column('rule_code', sa.String(length=32), nullable=False),
        sa.Column('period_key', sa.String(length=16), nullable=False, server_default=''),
        sa.Column('current_no', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_number_sequences_key',
        'number_sequences',
        ['rule_code', 'period_key'],
        unique=True,
    )

    # 预置报价与订单两条规则，格式与原来的硬编码完全一致，
    # 保证改造后单号形态不变（历史数据不用迁移）。
    op.execute(
        """
        insert into numbering_rules (code, name, prefix, date_format, seq_length, reset_period, enabled, remark)
        values
          ('quote', '报价单号', 'Q', '%Y%m%d', 4, 'daily', true, 'PRD §2.6 默认：Q + 年月日 + 4 位流水'),
          ('order', '销售订单号', 'SO', '%Y%m%d', 4, 'daily', true, 'PRD §2.6 默认：SO + 年月日 + 4 位流水'),
          ('sample', '样品申请单号', 'SP', '%Y%m%d', 4, 'daily', true, '样品模块单号')
        on conflict (code) do nothing
        """
    )


def downgrade() -> None:
    op.drop_index('ix_number_sequences_key', table_name='number_sequences')
    op.drop_table('number_sequences')
    op.drop_index('ix_numbering_rules_code', table_name='numbering_rules')
    op.drop_table('numbering_rules')
