"""合并客户跟进人与售后财务打回迁移分叉

Revision ID: fbe435eab96a
Revises: d4f6a8b0c2e1, e1a3c5d7b9f2
Create Date: 2026-11-27 11:11:42.945445
"""
from typing import Sequence, Union


revision: str = 'fbe435eab96a'
down_revision: Union[str, None] = ('d4f6a8b0c2e1', 'e1a3c5d7b9f2')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
