"""任务加来源业务对象 + 合同加协议生效日（外部审查第二批 阶段 C）。

Revision ID: e2a6c0d4f8b3
Revises: d1f5b9c3e7a4
Create Date: 2026-10-05

两件事：

1. **月结到期提醒靠标题去重**（审查第 7 条）。任务表只有 `source='system'` 和一段
   标题文字，既说不清"这条待办是哪份协议带出来的"，也无法可靠去重——
   任务一被完成，下次扫描按「标题 + 状态在办」找不到它，就再建一条同样的。
   → 加 `source_business_type` / `source_business_id`（前端据此跳到具体协议）
   和 `source_key`（去重身份键，含到期日，所以续签换期就是另一个周期）。
   去重键上建**部分唯一索引**：人工建的任务不写这个键，不受影响。

2. **续签要说清楚新协议什么时候生效**（审查阶段 C 的建议，业务方 2026-10-05 已采纳）。
   只记"到期日"处理不了「提前签、未来才生效」——那种情况下旧协议还得继续适用一段。
   → `contract_documents` 加 `effective_date`。

全部可空，历史数据不受影响（`source_key` 为空的旧任务不参与去重）。
"""

from alembic import op
import sqlalchemy as sa

revision = "e2a6c0d4f8b3"
down_revision = "d1f5b9c3e7a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("source_business_type", sa.String(length=32), nullable=True))
    op.add_column("tasks", sa.Column("source_business_id", sa.BigInteger(), nullable=True))
    op.add_column("tasks", sa.Column("source_key", sa.String(length=128), nullable=True))
    op.create_index(
        "uq_tasks_source_key",
        "tasks",
        ["source_key"],
        unique=True,
        postgresql_where=sa.text("source_key IS NOT NULL"),
    )
    op.add_column("contract_documents", sa.Column("effective_date", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("contract_documents", "effective_date")
    op.drop_index("uq_tasks_source_key", table_name="tasks")
    op.drop_column("tasks", "source_key")
    op.drop_column("tasks", "source_business_id")
    op.drop_column("tasks", "source_business_type")
