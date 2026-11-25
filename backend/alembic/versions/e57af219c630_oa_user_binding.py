"""Store the native Tongxu OA account separately from DingTalk login."""

import sqlalchemy as sa
from alembic import op

revision = "e57af219c630"
down_revision = "c3a9d1e7f5b2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("oa_user_id", sa.String(36), nullable=True))
    op.create_unique_constraint("uq_users_oa_user_id", "users", ["oa_user_id"])


def downgrade():
    op.drop_constraint("uq_users_oa_user_id", "users", type_="unique")
    op.drop_column("users", "oa_user_id")
