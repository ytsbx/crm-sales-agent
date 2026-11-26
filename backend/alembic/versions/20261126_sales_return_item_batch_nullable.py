"""销售退货：退货明细不再强制关联发货批次

Revision ID: a1c4e8f2b6d9
Revises: f3c7a9d1b5e8
Create Date: 2026-11-26

2026-11-26 业务确认：退货不需要追到具体发货批次。
新退货明细只挂订单明细，`shipment_batch_item_id/batch_id` 允许为空；
历史数据保留原值，不删列、不改历史。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a1c4e8f2b6d9"
down_revision: str | None = "f3c7a9d1b5e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "sales_return_items",
        "shipment_batch_item_id",
        existing_type=sa.BigInteger(),
        nullable=True,
    )
    op.alter_column(
        "sales_return_items",
        "batch_id",
        existing_type=sa.BigInteger(),
        nullable=True,
    )


def downgrade() -> None:
    # 新退货明细没有批次值，直接把列改回 NOT NULL 会失败。
    # 降级只恢复列类型语义；是否回填批次由运维按历史数据决定。
    op.alter_column(
        "sales_return_items",
        "shipment_batch_item_id",
        existing_type=sa.BigInteger(),
        nullable=True,
    )
    op.alter_column(
        "sales_return_items",
        "batch_id",
        existing_type=sa.BigInteger(),
        nullable=True,
    )
