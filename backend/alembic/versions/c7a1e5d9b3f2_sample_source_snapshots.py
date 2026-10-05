"""打样来源版本、采购数量快照及创建重试去重。"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c7a1e5d9b3f2"
down_revision = "b3f62d8e91a4"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('sample_requests', sa.Column('source_context', postgresql.JSONB(), nullable=True))
    op.add_column('sample_requests', sa.Column('request_key', sa.String(36), nullable=True))
    op.add_column('sample_requests', sa.Column('request_hash', sa.String(64), nullable=True))
    op.create_unique_constraint('uq_sample_source_request_key', 'sample_requests', ['request_key'])
    op.add_column('sample_items', sa.Column('source_snapshot', postgresql.JSONB(), nullable=True))
    op.add_column('sample_items', sa.Column('original_quantity', sa.Numeric(16, 3), nullable=True))
    op.add_column('sample_items', sa.Column('specification', sa.Text(), nullable=True))
    op.alter_column('sample_items', 'remark', existing_type=sa.String(255), type_=sa.Text())


def downgrade():
    op.alter_column('sample_items', 'remark', existing_type=sa.Text(), type_=sa.String(255))
    for name in ('specification', 'original_quantity', 'source_snapshot'):
        op.drop_column('sample_items', name)
    op.drop_constraint('uq_sample_source_request_key', 'sample_requests', type_='unique')
    for name in ('request_hash', 'request_key', 'source_context'):
        op.drop_column('sample_requests', name)
