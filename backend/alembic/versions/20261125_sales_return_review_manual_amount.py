"""销售退货：主管复核 + 财务人工确认退货货款

Revision ID: e5b8c2d7a4f1
Revises: d1a7c3e5b9f2
Create Date: 2026-11-25

2026-11-25 主人拍板：
- 登记后先给主管复核，通过才进入财务处理；
- 最终退货货款由财务人工填写，系统只保留登记参考价；
- 驳回释放可退数量。
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e5b8c2d7a4f1"
down_revision: str | None = "d1a7c3e5b9f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sales_returns",
        sa.Column("settled_goods_amount", sa.Numeric(16, 2), nullable=True),
    )
    op.add_column(
        "sales_returns",
        sa.Column("reviewed_by", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "sales_returns",
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "sales_returns",
        sa.Column("review_note", sa.String(length=255), nullable=True),
    )
    # 老数据保持账实一致：已冲账/已退款的历史单，最终货款就是当时记录的货款。
    op.execute(
        "UPDATE sales_returns SET settled_goods_amount = goods_amount "
        "WHERE status IN ('completed', 'refunded')"
    )
    # 尚未处理的旧单补走主管复核；退货模块尚未部署，正常不会命中。
    op.execute("UPDATE sales_returns SET status = 'reviewing' WHERE status = 'pending'")


def downgrade() -> None:
    op.drop_column("sales_returns", "review_note")
    op.drop_column("sales_returns", "reviewed_at")
    op.drop_column("sales_returns", "reviewed_by")
    op.drop_column("sales_returns", "settled_goods_amount")
