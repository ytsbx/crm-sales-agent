"""打样费用改为可空：空着 = 未填，与「免费 0 元」区分开。

背景：`sample_fee` 原来是 NOT NULL + server_default 0，而前端清空费用输入框时
提交 `null`，直接撞非空约束、报数据库错误。
口径由业务方确认为「空着 = 没填」——所以列可空、不再给默认值。
已存在的历史数据保持原值不动（原来存 0 的仍是 0）。
"""
from alembic import op
import sqlalchemy as sa

revision = "f4d8b2e6a1c9"
down_revision = "e9c3a7b1d5f4"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "sample_requests",
        "sample_fee",
        existing_type=sa.Numeric(16, 2),
        nullable=True,
        server_default=None,
    )


def downgrade():
    # 收紧回 NOT NULL 之前必须先把 NULL 兜成 0，否则回滚会因为空值失败
    op.execute("UPDATE sample_requests SET sample_fee = 0 WHERE sample_fee IS NULL")
    op.alter_column(
        "sample_requests",
        "sample_fee",
        existing_type=sa.Numeric(16, 2),
        nullable=False,
        server_default="0",
    )
