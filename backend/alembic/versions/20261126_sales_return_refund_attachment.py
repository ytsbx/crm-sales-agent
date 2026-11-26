"""销售退货：财务退款附件

Revision ID: f3c7a9d1b5e8
Revises: e5b8c2d7a4f1
Create Date: 2026-11-26

2026-11-26 主人要求：财务登记实际退款时，可以上传退款凭证附件。
只加一个指向文件中心的 file_id，不复制文件元信息。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f3c7a9d1b5e8"
down_revision: str | None = "e5b8c2d7a4f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sales_returns",
        sa.Column("refund_attachment_file_id", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sales_returns", "refund_attachment_file_id")
