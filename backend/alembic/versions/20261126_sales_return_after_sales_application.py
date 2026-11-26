"""销售售后申请：金额构成、打款信息、退款登记表附件、仓库确认

Revision ID: b7e2c9d4a1f6
Revises: a1c4e8f2b6d9
Create Date: 2026-11-26

按分销商售后 OA 表单补齐申请字段；不改变历史退货单已有金额字段。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7e2c9d4a1f6"
down_revision: str | None = "a1c4e8f2b6d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # warehouse_pending 比原来的 16 字符长，先把状态列放宽，否则 Postgres 会截断/报错。
    op.alter_column(
        "sales_returns",
        "status",
        existing_type=sa.String(length=16),
        type_=sa.String(length=32),
        existing_nullable=False,
    )
    op.add_column(
        "sales_returns",
        sa.Column(
            "goods_refund_amount",
            sa.Numeric(16, 2),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "sales_returns",
        sa.Column(
            "adjustment_amount",
            sa.Numeric(16, 2),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "sales_returns",
        sa.Column("adjustment_note", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "sales_returns",
        sa.Column(
            "payment_method",
            sa.String(length=32),
            nullable=False,
            server_default="线下转账",
        ),
    )
    op.add_column(
        "sales_returns",
        sa.Column("receiving_account", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "sales_returns",
        sa.Column("application_attachment_file_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "sales_returns",
        sa.Column("return_tracking_no", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "sales_returns",
        sa.Column("warehouse_confirmed_by", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "sales_returns",
        sa.Column("warehouse_confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "sales_returns",
        sa.Column("warehouse_note", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    # 先把新状态回退成历史能接受的值，再缩回 16 字符，避免 downgrade 失败。
    op.execute(
        "UPDATE sales_returns SET status = 'completed' "
        "WHERE status = 'warehouse_pending'"
    )
    op.alter_column(
        "sales_returns",
        "status",
        existing_type=sa.String(length=32),
        type_=sa.String(length=16),
        existing_nullable=False,
    )
    op.drop_column("sales_returns", "warehouse_note")
    op.drop_column("sales_returns", "warehouse_confirmed_at")
    op.drop_column("sales_returns", "warehouse_confirmed_by")
    op.drop_column("sales_returns", "return_tracking_no")
    op.drop_column("sales_returns", "application_attachment_file_id")
    op.drop_column("sales_returns", "receiving_account")
    op.drop_column("sales_returns", "payment_method")
    op.drop_column("sales_returns", "adjustment_note")
    op.drop_column("sales_returns", "adjustment_amount")
    op.drop_column("sales_returns", "goods_refund_amount")
