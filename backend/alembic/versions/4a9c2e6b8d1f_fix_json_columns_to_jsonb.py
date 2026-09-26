"""fix JSON columns that were left as json instead of jsonb

三个 JSON 列在库里是 `json` 而不是模型声明的 JSONB，原因是 `c8f1a2d4e7b9`
（物流试算）的 revision id 字典序排在 `f2c8d4e6a1b3` 之后，alembic 把它排在最后执行，
于是它重建 `logistics_quotes.raw_data` 时用普通 JSON 盖掉了 models 建的 JSONB。
`customer_merge_logs` 的两列同理（由 e9b3c5d7f1a2 先建为 JSONB，再被覆盖）。

后果不只是类型不整齐：JSONB 才能建 GIN 索引，也才能用 `@>` 这类操作符，
将来按 raw_data 里的字段查同步记录时，json 类型会直接报错。

这一版把它们显式改回 JSONB，让 autogenerate 不再每次报"有变更"。
`USING ...::jsonb` 是必要的：不加的话 PG 会尝试隐式转换失败。

Revision ID: 4a9c2e6b8d1f
Revises: b7d1e4f8c2a9
Create Date: 2026-09-26

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '4a9c2e6b8d1f'
down_revision: Union[str, None] = 'b7d1e4f8c2a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (表名, 列名)
COLUMNS = [
    ('customer_merge_logs', 'merge_snapshot'),
    ('customer_merge_logs', 'moved'),
    ('logistics_quotes', 'raw_data'),
]


def upgrade() -> None:
    for table, column in COLUMNS:
        op.execute(
            f'ALTER TABLE {table} ALTER COLUMN {column} TYPE jsonb USING {column}::jsonb'
        )


def downgrade() -> None:
    for table, column in COLUMNS:
        op.execute(
            f'ALTER TABLE {table} ALTER COLUMN {column} TYPE json USING {column}::json'
        )
