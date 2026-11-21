"""Persist complete manual allocation intent, including an empty allocation.

Revision ID: d8a2f6c1b4e9
Revises: c7a1e5b3f8d2
"""
import sqlalchemy as sa
from alembic import op
revision = "d8a2f6c1b4e9"
down_revision = "c7a1e5b3f8d2"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("payment_records", sa.Column("allocation_mode", sa.String(16), nullable=False, server_default="legacy"))
    # Existing rows are untouched financially. A manual row implies a complete manual decision.
    op.execute("""UPDATE payment_records p SET allocation_mode = CASE
        WHEN EXISTS (SELECT 1 FROM payment_allocations a WHERE a.payment_id=p.id AND a.source='manual') THEN 'manual'
        ELSE 'auto' END
        WHERE EXISTS (SELECT 1 FROM payment_allocations a WHERE a.payment_id=p.id)""")

def downgrade():
    # Empty manual decisions cannot be represented by the old schema; refusing avoids silently reallocating money.
    raise RuntimeError("Cannot downgrade allocation intent: retain the column to preserve manual financial decisions")
