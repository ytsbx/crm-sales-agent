"""Keep personal notification removal separate from delivery history."""

from alembic import op
import sqlalchemy as sa

revision = "b3e9d7f160ac"
down_revision = "d7c2a9e5b140"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("notifications", sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("notifications", "dismissed_at")
