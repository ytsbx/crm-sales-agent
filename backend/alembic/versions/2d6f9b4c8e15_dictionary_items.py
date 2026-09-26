"""dictionary items for controlled vocabularies

03-API §36 要求 `GET/POST /dictionaries`、`PATCH /dictionaries/{id}` 与
`GET/POST /customer-levels`、`PATCH /customer-levels/{id}`。

客户等级与其它字典形状完全一致（code / label / 排序 / 启停），
所以共用 `dictionary_items` 一张表，用 type 区分，不另开表。

顺带预置 A/B/C/D 四个等级：PRD 里等级一直是"人工评定"但没有定义清单，
界面上要有个可选的来源。

Revision ID: 2d6f9b4c8e15
Revises: 8f3c1a7e5b92
Create Date: 2026-09-26

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '2d6f9b4c8e15'
down_revision: Union[str, None] = '8f3c1a7e5b92'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'dictionary_items',
        sa.Column('type', sa.String(length=32), nullable=False),
        sa.Column('code', sa.String(length=64), nullable=False),
        sa.Column('label', sa.String(length=128), nullable=False),
        sa.Column('sort_no', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('remark', sa.String(length=255), nullable=True),
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_dictionary_items_type_code',
        'dictionary_items',
        ['type', 'code'],
        unique=True,
    )
    op.create_index(
        'ix_dictionary_items_type', 'dictionary_items', ['type', 'sort_no'], unique=False
    )

    op.execute(
        """
        insert into dictionary_items (type, code, label, sort_no, enabled, remark)
        values
          ('customer_level', 'A', 'A 级客户', 1, true, 'PRD §6 人工评定；公海回收天数在公海规则里单独配'),
          ('customer_level', 'B', 'B 级客户', 2, true, null),
          ('customer_level', 'C', 'C 级客户', 3, true, null),
          ('customer_level', 'D', 'D 级客户', 4, true, null),
          ('product_category', 'turnover_box', '周转箱', 1, true, '产品分类字典，供产品表单下拉'),
          ('product_category', 'pallet', '托盘', 2, true, null),
          ('product_category', 'cold_chain', '冷链保温', 3, true, null)
        on conflict (type, code) do nothing
        """
    )


def downgrade() -> None:
    op.drop_index('ix_dictionary_items_type', table_name='dictionary_items')
    op.drop_index('ix_dictionary_items_type_code', table_name='dictionary_items')
    op.drop_table('dictionary_items')
