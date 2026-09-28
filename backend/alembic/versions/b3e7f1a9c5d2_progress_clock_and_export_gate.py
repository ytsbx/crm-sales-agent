"""冷落时钟口径 + 导出闸门（CRM 完整实现方案 §2.3/§11.2/场景19）

1) customers 加 last_progress_at：报价/打样/下单/回款等业务进展刷新，
   与手工跟进的 last_followup_at 分开；冷落与回收取两者较新者，
   避免"正在履约但没点记录跟进"的客户被误回收。
2) 新增 customer:export 权限：导出从"能看列表就能批量导出"收成独立授权
   （§11.2 点名的当前最具体安全缺口）。
"""

import sqlalchemy as sa
from alembic import op

revision = "b3e7f1a9c5d2"
down_revision = "e1c9b7d3a5f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "customers",
        sa.Column("last_progress_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "insert into permissions (code, name, resource, action) "
        "values ('customer:export', '导出客户', 'customer', 'export') "
        "on conflict do nothing"
    )


def downgrade() -> None:
    op.execute("delete from permissions where code = 'customer:export'")
    op.drop_column("customers", "last_progress_at")
