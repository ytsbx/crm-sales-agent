"""人工核定售后后的退款/应收调整台账。"""

from alembic import op
import sqlalchemy as sa


revision = "d0f4a8c2e6b1"
down_revision = "c9e3f5b7d1a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "financial_adjustments",
        sa.Column("source_diff_id", sa.BigInteger(), nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=True),
        sa.Column("adjustment_type", sa.String(length=32), nullable=False),
        sa.Column("amount", sa.Numeric(precision=16, scale=2), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_diff_id", name="uq_financial_adjustments_source_diff"),
    )
    op.create_index(
        "ix_financial_adjustments_order_status",
        "financial_adjustments",
        ["order_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_financial_adjustments_order_status", table_name="financial_adjustments")
    op.drop_table("financial_adjustments")
