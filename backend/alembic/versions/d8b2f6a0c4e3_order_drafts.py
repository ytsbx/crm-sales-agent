"""独立订单草稿与正式订单明细来源。"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'd8b2f6a0c4e3'
down_revision = 'c7a1e5d9b3f2'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('order_drafts',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('customer_id', sa.BigInteger(), sa.ForeignKey('customers.id'), nullable=False),
        sa.Column('opportunity_id', sa.BigInteger(), nullable=True),
        sa.Column('owner_id', sa.BigInteger(), nullable=False),
        sa.Column('source_context', postgresql.JSONB(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False, server_default='draft'),
        sa.Column('currency', sa.String(8), nullable=False, server_default='CNY'),
        sa.Column('delivery_date', sa.Date(), nullable=True),
        sa.Column('payment_terms', sa.Text(), nullable=True),
        sa.Column('remark', sa.Text(), nullable=True),
        sa.Column('order_id', sa.BigInteger(), sa.ForeignKey('sales_orders.id'), nullable=True),
        sa.Column('request_key', sa.String(36), nullable=False, unique=True),
        sa.Column('request_hash', sa.String(64), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('created_by', sa.BigInteger(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_order_drafts_owner', 'order_drafts', ['owner_id'])
    op.create_table('order_draft_items',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('draft_id', sa.BigInteger(), sa.ForeignKey('order_drafts.id'), nullable=False),
        sa.Column('source_snapshot', postgresql.JSONB(), nullable=False),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('quantity', sa.Numeric(16,3), nullable=False),
        sa.Column('unit_price', sa.Numeric(16,4), nullable=True),
        sa.Column('specification', sa.Text(), nullable=True),
        sa.Column('remark', sa.Text(), nullable=True))
    op.create_index('ix_order_draft_items_draft', 'order_draft_items', ['draft_id'])
    op.add_column('biz_docs', sa.Column('order_draft_id', sa.BigInteger(), nullable=True))
    op.add_column('sales_order_items', sa.Column('quote_item_id', sa.BigInteger(), nullable=True))
    op.add_column('sales_order_items', sa.Column('source_snapshot', postgresql.JSONB(), nullable=True))


def downgrade():
    op.drop_column('biz_docs', 'order_draft_id')
    op.drop_column('sales_order_items', 'source_snapshot')
    op.drop_column('sales_order_items', 'quote_item_id')
    op.drop_table('order_draft_items')
    op.drop_table('order_drafts')
