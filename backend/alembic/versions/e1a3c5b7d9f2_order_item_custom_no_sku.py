"""订单明细支持无 SKU 的定制件（场景09 主路径的最后一环）

场景09：尚无正式 SKU 时，用需求编号也能询价、报价、打样，投产后再关联 SKU。
询价/报价/打样三环此前都通了，**唯独转订单必炸**——sales_order_items.sku_id
是 NOT NULL，而定制明细的 sku_id 为 None，插入直接违反约束。

这里把 sku_id 放开为可空，并补两个溯源列（与 quote_items 同构）：
inquiry_id + inquiry_no_snapshot。定制订单行靠需求编号说清"这是什么"，
等投产后再补 sku_id 即可。
"""

import sqlalchemy as sa
from alembic import op

revision = "e1a3c5b7d9f2"
down_revision = "d0a5b7c9e1f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("sales_order_items", "sku_id", existing_type=sa.BigInteger(), nullable=True)
    op.add_column(
        "sales_order_items", sa.Column("inquiry_id", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "sales_order_items",
        sa.Column("inquiry_no_snapshot", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sales_order_items", "inquiry_no_snapshot")
    op.drop_column("sales_order_items", "inquiry_id")
    # 回滚时无 SKU 的行没法满足 NOT NULL，先删掉再收紧（测试期数据可删）
    op.execute("delete from sales_order_items where sku_id is null")
    op.alter_column("sales_order_items", "sku_id", existing_type=sa.BigInteger(), nullable=False)
