"""案例库（CRM 完整实现方案 §3.7 / 场景15）。"""

import sqlalchemy as sa
from alembic import op

revision = "f1b3d5e7a9c2"
down_revision = "e9a4c6f2b7d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sales_cases",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("author_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("customer_id", sa.BigInteger(), sa.ForeignKey("customers.id"), nullable=True),
        sa.Column("customer_label", sa.String(128), nullable=True),
        sa.Column("industry", sa.String(64), nullable=True),
        sa.Column("product_line", sa.String(64), nullable=True),
        sa.Column("stage_reached", sa.String(32), nullable=True),
        sa.Column("problem_tags", sa.JSON(), nullable=True),
        sa.Column("background", sa.Text(), nullable=True),
        sa.Column("goal", sa.Text(), nullable=True),
        sa.Column("key_actions", sa.Text(), nullable=True),
        sa.Column("objection_handling", sa.Text(), nullable=True),
        sa.Column("process", sa.Text(), nullable=True),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("lessons", sa.Text(), nullable=True),
        sa.Column("quote_id", sa.BigInteger(), nullable=True),
        sa.Column("order_id", sa.BigInteger(), nullable=True),
        sa.Column("sample_id", sa.BigInteger(), nullable=True),
        sa.Column("opportunity_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("reviewer_id", sa.BigInteger(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_note", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_sales_cases_status", "sales_cases", ["status"])
    op.create_index("ix_sales_cases_customer", "sales_cases", ["customer_id"])


def downgrade() -> None:
    op.drop_table("sales_cases")
