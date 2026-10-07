"""报价版本补三个抬头快照：客户名 / 联系人 / 有效期（第八批 §8.7 返修）。

## 为什么加这三列

审查（2026-10-07）实测：币种、付款/交付/贸易条款、明细计价单位**都已经**从版本快照取，
但出对客文件时——

- 客户名实时读 `customers.name`
- 联系人名实时读 `contacts.name`
- 有效期实时读 `quotes.valid_until`

于是同一份 V1 重新生成：客户改名后印的是新名字，主单有效期改了也跟着变，
**当时真正发给客户的那一份反而复现不出来**。这三列就是把它们钉在版本上。

## 历史数据不回填

老版本行没有这些值，出图时明确写「待核实」（与计价单位的旧口径一致，见
`quote_items.unit_snapshot` 的说明）。"当时抬头到底是谁"只有当事人知道，
迁移若拿当前客户名填进去，等于制造一份假的留存证据 —— 那比留空更糟。
"""
import sqlalchemy as sa
from alembic import op

revision = "c1d2e3f4a5b6"
down_revision = "b9c0d1e2f3a4"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "quote_versions",
        sa.Column("customer_name_snapshot", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "quote_versions",
        sa.Column("contact_name_snapshot", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "quote_versions",
        sa.Column("valid_until_snapshot", sa.Date(), nullable=True),
    )


def downgrade():
    op.drop_column("quote_versions", "valid_until_snapshot")
    op.drop_column("quote_versions", "contact_name_snapshot")
    op.drop_column("quote_versions", "customer_name_snapshot")
