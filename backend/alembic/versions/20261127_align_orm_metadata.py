"""补齐 ORM 与库结构的三处真实差异（非破坏性）

背景：`alembic check` 在售后发布前报出大量红项。根因之一是 `alembic/env.py`
漏导入若干业务模型，已单独修；本迁移只处理剩下的三处**真实结构差异**：

1. `order_drafts.updated_at`：库里可空，模型按 `TimestampMixin` 要求非空；
2. `quote_versions.pricing_basis`：库里可空，模型要求非空；
3. `order_schedule_changes.order_id`：模型声明了外键，库里没有。

生产在应用本迁移前 `order_drafts=0`、`quote_versions=1`（该行 pricing_basis
非空）、`order_schedule_changes=0`；因此不会因回填失败中断。
"""

from alembic import op
import sqlalchemy as sa


revision = "c3d5e7f9a1b2"
down_revision = "f5c8a1d3e7b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 历史空值先补成创建时间，避免 SET NOT NULL 直接失败。
    op.execute("UPDATE order_drafts SET updated_at = created_at WHERE updated_at IS NULL")
    op.alter_column(
        "order_drafts",
        "updated_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )

    # 生产这一列当前无空值；若其它环境有空值，迁移应显式失败而不是猜口径。
    op.alter_column(
        "quote_versions",
        "pricing_basis",
        existing_type=sa.String(length=32),
        nullable=False,
    )

    op.create_foreign_key(
        "order_schedule_changes_order_id_fkey",
        "order_schedule_changes",
        "sales_orders",
        ["order_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "order_schedule_changes_order_id_fkey",
        "order_schedule_changes",
        type_="foreignkey",
    )
    op.alter_column(
        "quote_versions",
        "pricing_basis",
        existing_type=sa.String(length=32),
        nullable=True,
    )
    op.alter_column(
        "order_drafts",
        "updated_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=True,
    )
