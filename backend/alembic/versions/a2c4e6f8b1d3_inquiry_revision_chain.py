"""定制询价修订链（CRM 完整实现方案 §3.3 / §四"需求及修订"）。

加版本号、链条根、修订说明、投产后关联 SKU。
"""

import sqlalchemy as sa
from alembic import op

revision = "a2c4e6f8b1d3"
down_revision = "f1b3d5e7a9c2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "custom_inquiries",
        sa.Column("version", sa.BigInteger(), nullable=False, server_default="1"),
    )
    op.add_column("custom_inquiries", sa.Column("root_id", sa.BigInteger(), nullable=True))
    op.add_column("custom_inquiries", sa.Column("revision_note", sa.String(255), nullable=True))
    op.add_column(
        "custom_inquiries", sa.Column("converted_sku_id", sa.BigInteger(), nullable=True)
    )
    op.create_index("ix_custom_inquiries_root", "custom_inquiries", ["root_id"])


def downgrade() -> None:
    op.drop_index("ix_custom_inquiries_root", table_name="custom_inquiries")
    op.drop_column("custom_inquiries", "converted_sku_id")
    op.drop_column("custom_inquiries", "revision_note")
    op.drop_column("custom_inquiries", "root_id")
    op.drop_column("custom_inquiries", "version")
