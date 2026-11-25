"""回款作废：记录作废人、作废时间与原因。

背景（2026-11-25 主人拍板 A 方案）：
- 已确认回款挡住订单取消，而回款侧没有作废入口；
- 新增 `voided` 状态，原记录不删、分配与凭证保留，只把"这笔钱还算不算已收"
  改判为否；
- 作废是财务动作，必须能回答"谁在什么时候、为什么作废"。

状态列本来就是 String(16)，不扩枚举；这里只补三个可空列，存量数据不受影响。
"""

from alembic import op
import sqlalchemy as sa

revision = "b7d3f9a2c4e1"
down_revision = "e6b4d2f8a1c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "payment_records",
        sa.Column("voided_by", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "payment_records",
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "payment_records",
        sa.Column("void_reason", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("payment_records", "void_reason")
    op.drop_column("payment_records", "voided_at")
    op.drop_column("payment_records", "voided_by")
