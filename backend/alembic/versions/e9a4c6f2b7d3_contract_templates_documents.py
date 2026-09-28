"""合同模板与文档台账（CRM 完整实现方案 §3.6 / 场景14）。"""

import sqlalchemy as sa
from alembic import op

revision = "e9a4c6f2b7d3"
down_revision = "d6f0b3e8a1c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "contract_templates",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("doc_type", sa.String(16), nullable=False, server_default="contract"),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "contract_documents",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("doc_no", sa.String(32), nullable=False, unique=True),
        sa.Column("doc_type", sa.String(16), nullable=False, server_default="contract"),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("customer_id", sa.BigInteger(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("order_id", sa.BigInteger(), sa.ForeignKey("sales_orders.id"), nullable=True),
        sa.Column("quote_id", sa.BigInteger(), nullable=True),
        sa.Column("template_id", sa.BigInteger(), sa.ForeignKey("contract_templates.id"), nullable=False),
        sa.Column("content_snapshot", sa.Text(), nullable=False),
        sa.Column("filled_data", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column(
            "parent_id", sa.BigInteger(), sa.ForeignKey("contract_documents.id"), nullable=True
        ),
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("void_reason", sa.String(255), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("contract_documents")
    op.drop_table("contract_templates")
