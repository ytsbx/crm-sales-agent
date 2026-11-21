"""Preserve ownership of execution audit after deleting a conversation."""

import sqlalchemy as sa
from alembic import op

revision = "c92b6f8143de"
down_revision = "759cf50ae9d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_sessions", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_sessions", "deleted_at")
