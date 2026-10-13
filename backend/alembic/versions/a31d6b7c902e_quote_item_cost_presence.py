"""保存成本记录是否存在，区分零元缺项与无成本记录。历史快照不回填。"""
import sqlalchemy as sa
from alembic import op

revision = "a31d6b7c902e"
down_revision = "9c1f7a2b4d83"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("quote_items", sa.Column("cost_present_snapshot", sa.Boolean(), nullable=True))


def downgrade():
    op.drop_column("quote_items", "cost_present_snapshot")
