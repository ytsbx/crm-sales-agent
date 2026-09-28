"""业务事件表（CRM 完整实现方案 §四 / 验收场景04）

event_key 唯一：重放/重试的同一业务动作命中同一 key 即整体跳过——
时间线只一条、主管只收一次。notifications 表继续做逐接收人投递记录。
"""

import sqlalchemy as sa
from alembic import op

revision = "d6f0b3e8a1c4"
down_revision = "c4e8a2f6b9d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "business_events",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("event_key", sa.String(128), nullable=False, unique=True),
        sa.Column("business_type", sa.String(32), nullable=True),
        sa.Column("business_id", sa.BigInteger(), nullable=True),
        sa.Column("customer_id", sa.BigInteger(), nullable=True),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("business_events")
