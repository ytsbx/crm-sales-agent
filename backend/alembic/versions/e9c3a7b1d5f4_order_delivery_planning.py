"""交期类型、运输天数、确认的倒排参数及节点跳过记录。"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision = 'e9c3a7b1d5f4'
down_revision = 'd8b2f6a0c4e3'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('sales_orders', sa.Column('delivery_kind', sa.String(16), nullable=True))
    op.add_column('sales_orders', sa.Column('transit_days', sa.Integer(), nullable=True))
    op.add_column('sales_orders', sa.Column('plan_offsets', postgresql.JSONB(), nullable=True))
    op.add_column('order_milestones', sa.Column('skip_reason', sa.Text(), nullable=True))
    op.add_column('order_milestones', sa.Column('skipped_by', sa.BigInteger(), nullable=True))
    op.add_column('order_milestones', sa.Column('skipped_at', sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint('ck_order_delivery_kind', 'sales_orders', "delivery_kind IS NULL OR delivery_kind IN ('shipping', 'arrival')")
    op.create_check_constraint('ck_order_transit_days', 'sales_orders', 'transit_days IS NULL OR transit_days BETWEEN 0 AND 365')

def downgrade():
    op.drop_constraint('ck_order_transit_days', 'sales_orders', type_='check')
    op.drop_constraint('ck_order_delivery_kind', 'sales_orders', type_='check')
    for col in ['skipped_at', 'skipped_by', 'skip_reason']:
        op.drop_column('order_milestones', col)
    for col in ['plan_offsets', 'transit_days', 'delivery_kind']:
        op.drop_column('sales_orders', col)
