"""Allow customer contracts without a generated template.

Revision ID: e1a3c5d7f902
Revises: c2f5a8d1e4b7
"""
from alembic import op
import sqlalchemy as sa

revision = 'e1a3c5d7f902'
down_revision = 'c2f5a8d1e4b7'
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column('contract_documents', 'template_id', existing_type=sa.BigInteger(), nullable=True)


def downgrade():
    if op.get_bind().execute(sa.text('SELECT 1 FROM contract_documents WHERE template_id IS NULL LIMIT 1')).first():
        raise RuntimeError('存在客户原稿合同，不能降级为强制模板合同')
    op.alter_column('contract_documents', 'template_id', existing_type=sa.BigInteger(), nullable=False)
