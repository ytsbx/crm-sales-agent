"""销售售后：财务打回字段

财务发现金额/收款信息不对时，只把单退回业务员/主管修改，不自己改金额。
新增四个字段记录打回原因、操作人、时间和累计次数；状态 `finance_returned`
本身是字符串，不需要改数据库枚举。
"""

from alembic import op
import sqlalchemy as sa


revision = "e1a3c5d7b9f2"
down_revision = "c3d5e7f9a1b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sales_returns",
        sa.Column("finance_return_reason", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "sales_returns",
        sa.Column("finance_returned_by", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "sales_returns",
        sa.Column("finance_returned_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "sales_returns",
        sa.Column(
            "finance_return_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("sales_returns", "finance_return_count")
    op.drop_column("sales_returns", "finance_returned_at")
    op.drop_column("sales_returns", "finance_returned_by")
    op.drop_column("sales_returns", "finance_return_reason")
