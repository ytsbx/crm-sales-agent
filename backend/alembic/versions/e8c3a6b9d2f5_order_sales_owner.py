"""订单签单负责人：业绩归属与当前负责人分离（文档 §3.8 / :61）

文档 :61 要求「交接后保留历史创建人、跟进作者、报价批准人和**历史业绩归属**」，
而 :61 同一句还要求交接「列出客户、商机、待办、打样、跟单及可访问档案，
**逐项分配接手人**」。这两条用一列做不到：

- 只留 owner_id（会随交接变）→ 业绩跟着走，原销售的功劳被抹掉；
- 交接干脆不动订单（此前就是这样）→ 接手人在系统里看不到这些订单
  （订单列表按 owner_id 过滤），既没人跟进也不计入任何人的名下。

所以拆成两列：
- `sales_owner_id`：签单负责人，创建时写死，业绩（签单额/回款额/目标实际值）算给他；
- `owner_id`：当前负责人，随交接与手工调整变化，管数据范围与跟进责任。

历史订单的 sales_owner_id **回填为当前 owner_id**：迁移前系统没有记录过签单
时点的人，只能取当前值——这是诚实的近似（对未被交接过的订单是准确的），
不能反推的就不假装能反推。
"""

import sqlalchemy as sa
from alembic import op

revision = "e8c3a6b9d2f5"
down_revision = "d7b2f5a8c1e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sales_orders", sa.Column("sales_owner_id", sa.BigInteger(), nullable=True))
    # 历史订单无法还原签单时点的人：取当前负责人作为归属（未交接过的单子就是准确的）
    op.execute("update sales_orders set sales_owner_id = owner_id where sales_owner_id is null")
    op.create_index("ix_sales_orders_sales_owner", "sales_orders", ["sales_owner_id"])


def downgrade() -> None:
    op.drop_index("ix_sales_orders_sales_owner", table_name="sales_orders")
    op.drop_column("sales_orders", "sales_owner_id")
