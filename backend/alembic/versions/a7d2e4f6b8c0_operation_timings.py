"""操作耗时埋点（文档 §六「评价操作是否省时」/ 场景18）

场景18 的合格条件是"操作时间与重复字段数**可与表格流程比较**"，而此前系统里
一个耗时数字都没有——拿不出证据，就只能靠印象说"我们更快"。

计时必须由前端做：服务端只看得到单据落库时间，那是流程跨度（可能开着页面去开会、
也可能隔夜），不是业务员真正花在这件事上的时间。服务端负责校验与聚合。

typed_fields / rework_count 由前端尽力而为地数（重复填写字段数、返工次数），
数不准时上报 0，汇总里也照实标注——不拿估算值当证据。
"""

import sqlalchemy as sa
from alembic import op

revision = "a7d2e4f6b8c0"
down_revision = "e1f4b8d2c6a9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operation_timings",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("operation", sa.String(length=48), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("duration_ms", sa.BigInteger(), nullable=False),
        sa.Column("business_type", sa.String(length=32), nullable=True),
        sa.Column("business_id", sa.BigInteger(), nullable=True),
        sa.Column("typed_fields", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rework_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="web"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_operation_timings_op_user", "operation_timings", ["operation", "user_id"]
    )
    op.create_index("ix_operation_timings_created", "operation_timings", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_operation_timings_created", table_name="operation_timings")
    op.drop_index("ix_operation_timings_op_user", table_name="operation_timings")
    op.drop_table("operation_timings")
