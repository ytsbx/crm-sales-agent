"""销售退货：退货单、退货明细、应收冲减快照。

Revision ID: d1a7c3e5b9f2
Revises: c4f8a1d6e2b9
Create Date: 2026-11-25

口径（2026-11-25 主人拍板 A 方案）：
- 只退已实发批次，原发货事实保留，不碰库存；
- 财务处理时先冲未收应收，剩余转待退款；
- 冲减逐期留快照，作废时能精确还原。
"""

from alembic import op
import sqlalchemy as sa

revision = "d1a7c3e5b9f2"
down_revision = "c4f8a1d6e2b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sales_returns",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("return_no", sa.String(length=32), nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("reason_code", sa.String(length=32), nullable=False, server_default="other"),
        sa.Column("reason", sa.String(length=255), nullable=False),
        sa.Column("return_date", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="CNY"),
        sa.Column("goods_amount", sa.Numeric(16, 2), nullable=False, server_default="0"),
        sa.Column("freight_refund_amount", sa.Numeric(16, 2), nullable=False, server_default="0"),
        sa.Column("total_amount", sa.Numeric(16, 2), nullable=False, server_default="0"),
        sa.Column("offset_amount", sa.Numeric(16, 2), nullable=False, server_default="0"),
        sa.Column("refund_amount", sa.Numeric(16, 2), nullable=False, server_default="0"),
        sa.Column("refund_reference", sa.String(length=128), nullable=True),
        sa.Column("settlement_note", sa.String(length=255), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("settled_by", sa.BigInteger(), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refunded_by", sa.BigInteger(), nullable=True),
        sa.Column("refunded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_by", sa.BigInteger(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_reason", sa.String(length=255), nullable=True),
        sa.Column("voided_by", sa.BigInteger(), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("void_reason", sa.String(length=255), nullable=True),
        sa.Column("remark", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["order_id"], ["sales_orders.id"]),
        sa.UniqueConstraint("return_no", name="uq_sales_returns_return_no"),
    )
    op.create_index("ix_sales_returns_order", "sales_returns", ["order_id"])
    op.create_index("ix_sales_returns_status", "sales_returns", ["status"])

    op.create_table(
        "sales_return_items",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("return_id", sa.BigInteger(), nullable=False),
        sa.Column("order_item_id", sa.BigInteger(), nullable=False),
        sa.Column("shipment_batch_item_id", sa.BigInteger(), nullable=False),
        sa.Column("batch_id", sa.BigInteger(), nullable=False),
        sa.Column("sku_snapshot", sa.String(length=200), nullable=True),
        sa.Column("specification_snapshot", sa.String(length=200), nullable=True),
        sa.Column("quantity", sa.Numeric(16, 3), nullable=False, server_default="0"),
        sa.Column("unit_price", sa.Numeric(16, 4), nullable=False, server_default="0"),
        sa.Column("amount", sa.Numeric(16, 2), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["return_id"], ["sales_returns.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["order_item_id"], ["sales_order_items.id"]),
        sa.ForeignKeyConstraint(["shipment_batch_item_id"], ["order_shipment_batch_items.id"]),
        sa.ForeignKeyConstraint(["batch_id"], ["order_shipment_batches.id"]),
        sa.UniqueConstraint("return_id", "shipment_batch_item_id", name="uq_return_batch_item"),
    )
    op.create_index("ix_sales_return_items_return", "sales_return_items", ["return_id"])

    op.create_table(
        "sales_return_plan_adjustments",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("return_id", sa.BigInteger(), nullable=False),
        sa.Column("plan_id", sa.BigInteger(), nullable=False),
        sa.Column("amount_before", sa.Numeric(16, 2), nullable=False, server_default="0"),
        sa.Column("applied_amount", sa.Numeric(16, 2), nullable=False, server_default="0"),
        sa.Column("amount_after", sa.Numeric(16, 2), nullable=False, server_default="0"),
        sa.Column("status_before", sa.String(length=16), nullable=False, server_default="pending"),
        sa.ForeignKeyConstraint(["return_id"], ["sales_returns.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plan_id"], ["receivable_plans.id"]),
        sa.UniqueConstraint("return_id", "plan_id", name="uq_return_plan_adjustment"),
    )
    op.create_index("ix_sales_return_adjustments_return", "sales_return_plan_adjustments", ["return_id"])


def downgrade() -> None:
    op.drop_index("ix_sales_return_adjustments_return", table_name="sales_return_plan_adjustments")
    op.drop_table("sales_return_plan_adjustments")
    op.drop_index("ix_sales_return_items_return", table_name="sales_return_items")
    op.drop_table("sales_return_items")
    op.drop_index("ix_sales_returns_status", table_name="sales_returns")
    op.drop_index("ix_sales_returns_order", table_name="sales_returns")
    op.drop_table("sales_returns")
