"""发货批次模型（CRM 完整实现方案 §3.5 / 验收场景13）

订单下的分批发货：批次 + 批次明细（对哪个订单明细、计划/实发多少）。
首批发货不结束整单——整单 completed 由 change_status 的未发量闸门把关。
"""

import sqlalchemy as sa
from alembic import op

revision = "c4e8a2f6b9d1"
down_revision = "b3e7f1a9c5d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "order_shipment_batches",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("order_id", sa.BigInteger(), sa.ForeignKey("sales_orders.id"), nullable=False),
        sa.Column("batch_no", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(16), nullable=False, server_default="planned"),
        sa.Column("planned_date", sa.Date(), nullable=True),
        sa.Column("actual_ship_date", sa.Date(), nullable=True),
        sa.Column("logistics_company", sa.String(64), nullable=True),
        sa.Column("tracking_no", sa.String(64), nullable=True),
        sa.Column("remark", sa.String(255), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_order_shipment_batches_order", "order_shipment_batches", ["order_id"]
    )
    op.create_table(
        "order_shipment_batch_items",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "batch_id", sa.BigInteger(), sa.ForeignKey("order_shipment_batches.id"),
            nullable=False,
        ),
        sa.Column(
            "order_item_id", sa.BigInteger(), sa.ForeignKey("sales_order_items.id"),
            nullable=False,
        ),
        sa.Column("sku_snapshot", sa.String(200), nullable=True),
        sa.Column("planned_qty", sa.Numeric(16, 3), nullable=False, server_default="0"),
        sa.Column("shipped_qty", sa.Numeric(16, 3), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_order_shipment_batch_items_batch", "order_shipment_batch_items", ["batch_id"]
    )
    op.create_unique_constraint(
        "uq_shipment_batch_item", "order_shipment_batch_items",
        ["batch_id", "order_item_id"],
    )


def downgrade() -> None:
    op.drop_table("order_shipment_batch_items")
    op.drop_table("order_shipment_batches")
