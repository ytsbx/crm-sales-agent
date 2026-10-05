"""人工跟进计划、免填原因和请求去重。"""
from alembic import op
import sqlalchemy as sa

revision = "b3f62d8e91a4"
down_revision = "a9e1c4b7d2f6"
branch_labels = None
depends_on = None


def upgrade():
    for column in [
        sa.Column("planned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exemption_reason", sa.String(32), nullable=True),
        sa.Column("next_task_id", sa.BigInteger(), nullable=True),
        sa.Column("request_key", sa.String(96), nullable=True),
        sa.Column("request_hash", sa.String(64), nullable=True),
    ]:
        op.add_column("followups", column)
    op.create_unique_constraint("uq_followup_owner_request", "followups", ["owner_id", "request_key"])


def downgrade():
    op.drop_constraint("uq_followup_owner_request", "followups", type_="unique")
    for name in ["request_hash", "request_key", "next_task_id", "exemption_reason", "planned_at"]:
        op.drop_column("followups", name)
