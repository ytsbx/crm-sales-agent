"""目标管理补两维：团队与老客户增长（文档 §六 :121 / 场景17）

原表只有 `period / user_id / new_customer_target / sales_target`——
"按业务员"和"全公司"两种粒度，缺中间那层**团队目标**；指标只有新客数与销售额，
缺**老客户增长**。

两列：

- `department_id`：团队目标。与 `user_id` 互斥语义——有部门 = 团队目标，
  两者都空 = 全公司目标；
- `repeat_customer_target`：老客户增长目标，**金额（净额）口径**。
  文档要求"老客增长要固定比较客户集合、周期和净额"，返单客户数不反映做了多少生意。

带回填默认 0，历史行不需要数据迁移。
"""

import sqlalchemy as sa
from alembic import op

revision = "a7d3e9c1f5b8"
down_revision = "f5c1a9d3e7b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sales_targets", sa.Column("department_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "sales_targets",
        sa.Column(
            "repeat_customer_target", sa.Numeric(16, 2), nullable=False, server_default="0"
        ),
    )
    op.create_index("ix_sales_targets_department", "sales_targets", ["department_id"])


def downgrade() -> None:
    op.drop_index("ix_sales_targets_department", table_name="sales_targets")
    op.drop_column("sales_targets", "repeat_customer_target")
    op.drop_column("sales_targets", "department_id")
