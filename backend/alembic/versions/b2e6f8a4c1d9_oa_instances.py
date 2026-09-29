"""OA 实例关联（文档 §四 :137 / 场景11）

唯一约束落在**业务键**（需求 + 版本 + OA 类型）而不是实例 ID：
实例 ID 是钉钉给的，重复提交时我们压根不该再向钉钉要一次实例。
靠业务键挡住第二次提交，才是真的"不重复建 OA 单"。
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b2e6f8a4c1d9"
down_revision = "a7d3e9c1f5b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "oa_instances",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("customer_id", sa.BigInteger(), nullable=True),
        sa.Column("inquiry_id", sa.BigInteger(), nullable=False),
        sa.Column("inquiry_version", sa.BigInteger(), nullable=False),
        sa.Column("oa_type", sa.String(length=24), nullable=False),
        sa.Column("process_code", sa.String(length=128), nullable=True),
        sa.Column("originator_user_id", sa.String(length=64), nullable=True),
        sa.Column("form_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("instance_id", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "inquiry_id", "inquiry_version", "oa_type", name="uq_oa_instance_business"
        ),
    )
    op.create_index("ix_oa_instances_instance", "oa_instances", ["instance_id"])
    op.create_index("ix_oa_instances_status", "oa_instances", ["status"])


def downgrade() -> None:
    op.drop_index("ix_oa_instances_status", table_name="oa_instances")
    op.drop_index("ix_oa_instances_instance", table_name="oa_instances")
    op.drop_table("oa_instances")
