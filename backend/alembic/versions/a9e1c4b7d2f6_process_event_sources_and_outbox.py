"""客户过程的打样来源及持久主管通知待办。"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "a9e1c4b7d2f6"
down_revision = "f7d2b8c4e1a6"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("notifications", sa.Column("business_event_id", sa.BigInteger(), nullable=True))
    op.create_unique_constraint("uq_notification_event_recipient", "notifications", ["business_event_id", "user_id"])
    op.add_column("followups", sa.Column("sample_id", sa.BigInteger(), nullable=True))
    op.add_column("business_events", sa.Column("notification_payload", postgresql.JSONB(), nullable=True))
    op.add_column("business_events", sa.Column("notification_processed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("business_events", sa.Column("notification_error", sa.String(255), nullable=True))
    op.create_index("ix_business_events_pending_notification", "business_events", ["id"],
                    postgresql_where=sa.text("notification_payload IS NOT NULL AND notification_processed_at IS NULL"))


def downgrade():
    op.drop_constraint("uq_notification_event_recipient", "notifications", type_="unique")
    op.drop_column("notifications", "business_event_id")
    op.drop_index("ix_business_events_pending_notification", table_name="business_events")
    op.drop_column("business_events", "notification_error")
    op.drop_column("business_events", "notification_processed_at")
    op.drop_column("business_events", "notification_payload")
    op.drop_column("followups", "sample_id")
