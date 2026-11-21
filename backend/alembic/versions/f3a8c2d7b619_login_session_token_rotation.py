"""Track the current access token for each login session."""

import sqlalchemy as sa
from alembic import op

revision = "f3a8c2d7b619"
down_revision = "e7b3c1d5f9a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("login_sessions", sa.Column("token_id", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("login_sessions", "token_id")
