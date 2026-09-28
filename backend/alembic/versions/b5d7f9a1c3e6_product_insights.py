"""新品洞察（CRM 完整实现方案 §3.3 第三类）。"""

import sqlalchemy as sa
from alembic import op

revision = "b5d7f9a1c3e6"
down_revision = "a2c4e6f8b1d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "product_insights",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("source", sa.String(64), nullable=True),
        sa.Column("target_customer", sa.String(128), nullable=True),
        sa.Column("direction", sa.Text(), nullable=True),
        sa.Column("selling_points", sa.Text(), nullable=True),
        sa.Column("price_assumption", sa.Numeric(16, 2), nullable=True),
        sa.Column("conclusion", sa.Text(), nullable=True),
        sa.Column("images", sa.JSON(), nullable=True),
        sa.Column("owner_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("reviewer_id", sa.BigInteger(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_note", sa.String(255), nullable=True),
        sa.Column("converted_inquiry_id", sa.BigInteger(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_product_insights_status", "product_insights", ["status"])
    op.create_index("ix_product_insights_owner", "product_insights", ["owner_id"])


def downgrade() -> None:
    op.drop_table("product_insights")
