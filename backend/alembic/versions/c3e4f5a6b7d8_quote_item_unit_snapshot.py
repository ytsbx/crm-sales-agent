"""报价明细冻结计价单位：`quote_items.unit_snapshot`（第八批 8.7）。

为什么必须有这一列：正式报价 Excel 原来不输出计价单位（unit 固定空白），
而"按报价版本冻结条款"要求单位跟**当时那一版**走。若出图时回查 `skus.unit`，
单位一改，旧版本重新出的表就会拿今天的单位冒充当时报的价 —— 历史文件与
历史事实不一致，正是 8.7 点名要避免的。

历史行留 NULL = "未留存"，出图时按"未留存/待核实"显示，**不回填当前 SKU 单位**：
把今天的值填进历史版本，等于伪造历史。
"""
from alembic import op
import sqlalchemy as sa

revision = "c3e4f5a6b7d8"
down_revision = "b2d3f4a5c6e7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "quote_items",
        sa.Column("unit_snapshot", sa.String(length=16), nullable=True),
    )


def downgrade():
    op.drop_column("quote_items", "unit_snapshot")
