"""首次扫码账号的初始密码提示标记；已有用户不改账号密码。"""
from alembic import op
import sqlalchemy as sa
revision = "b24d8f1a6c03"
down_revision = "e1f3a5b7c9d2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("initial_password_pending", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    op.drop_column("users", "initial_password_pending")
