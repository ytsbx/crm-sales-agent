"""交期变更单：展示受影响节点及批次 + 责任人确认 + 保留前后版本（方案 :105）

原文：「客户改交期、样品未通过或生产延期时，展示受影响节点及批次，
责任人确认调整并保留修改前后版本。」

以前改交期只调一个字段、顺手重排计划日，既没有"受影响面"给他看，
也没有"谁确认的"这个动作，翻审计日志才知道谁改了什么。

`affected` 存节点与批次的 before/after 计划日；确认时**不回写**——
它就是那一版调整的存档，再改一次会生成新的一张单，旧的永远留着当时的对比。
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c9f4a6b8d0e2"
down_revision = "b8e3f5a7c9d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "order_schedule_changes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("old_delivery_date", sa.Date(), nullable=True),
        sa.Column("new_delivery_date", sa.Date(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("affected", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("owner_id", sa.BigInteger(), nullable=True),
        sa.Column("confirmed_by", sa.BigInteger(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirm_remark", sa.Text(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_order_schedule_changes_order", "order_schedule_changes", ["order_id"]
    )
    op.create_index(
        "ix_order_schedule_changes_status", "order_schedule_changes", ["status"]
    )


def downgrade() -> None:
    op.drop_index("ix_order_schedule_changes_status", table_name="order_schedule_changes")
    op.drop_index("ix_order_schedule_changes_order", table_name="order_schedule_changes")
    op.drop_table("order_schedule_changes")
