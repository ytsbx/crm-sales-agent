"""售后收货确认改为业务员/主管，不再使用旧收货岗位命名

Revision ID: f5c8a1d3e7b2
Revises: b7e2c9d4a1f6
Create Date: 2026-11-26

- 状态 `warehouse_pending` 改为 `receiving_pending`（待确认收到退货）；
- 收货确认人/时间/备注字段从 warehouse_* 改为 received_by / received_at / receiving_note；
- 只改命名与状态值，不改金额和历史业务数据。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f5c8a1d3e7b2"
down_revision: str | None = "b7e2c9d4a1f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE sales_returns SET status = 'receiving_pending' "
        "WHERE status = 'warehouse_pending'"
    )
    op.alter_column(
        "sales_returns",
        "warehouse_confirmed_by",
        new_column_name="received_by",
        existing_type=sa.BigInteger(),
        existing_nullable=True,
    )
    op.alter_column(
        "sales_returns",
        "warehouse_confirmed_at",
        new_column_name="received_at",
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=True,
    )
    op.alter_column(
        "sales_returns",
        "warehouse_note",
        new_column_name="receiving_note",
        existing_type=sa.String(length=255),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "sales_returns",
        "receiving_note",
        new_column_name="warehouse_note",
        existing_type=sa.String(length=255),
        existing_nullable=True,
    )
    op.alter_column(
        "sales_returns",
        "received_at",
        new_column_name="warehouse_confirmed_at",
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=True,
    )
    op.alter_column(
        "sales_returns",
        "received_by",
        new_column_name="warehouse_confirmed_by",
        existing_type=sa.BigInteger(),
        existing_nullable=True,
    )
    op.execute(
        "UPDATE sales_returns SET status = 'warehouse_pending' "
        "WHERE status = 'receiving_pending'"
    )
