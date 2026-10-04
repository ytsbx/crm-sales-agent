"""加宽 quote_items.price_source 到 32

取价来源会写成 `customer_specific`（17 个字符），而列是 `varchar(16)`：
**客户有专属价时，给这张报价新建明细直接 500**
（asyncpg: value too long for type character varying(16)）。
模型侧同步放宽到 String(32)。
"""

import sqlalchemy as sa
from alembic import op

revision = "f7d2b8c4e1a6"
down_revision = "e5c1a7b3d9f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "quote_items",
        "price_source",
        existing_type=sa.String(16),
        type_=sa.String(32),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "quote_items",
        "price_source",
        existing_type=sa.String(32),
        type_=sa.String(16),
        existing_nullable=True,
    )
