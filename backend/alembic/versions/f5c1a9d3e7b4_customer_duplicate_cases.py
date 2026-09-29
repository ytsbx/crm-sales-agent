"""撞单裁定单（文档 §11.4 验收 20：历史客户导入与现有客户撞单）

查重打分与合并都早有实现，缺的是中间那一环：**疑似之后由谁定**。
此前导入遇到疑似只"跳过并报告"，业务拿到的是一行文字、没有可跟进的待办。

`evidence` 存当时的匹配证据快照（事后回看要还原"当初凭什么提示"），
`resolved_owner_id` 只由人填——没有默认值、不按建档时间自动推导。
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f5c1a9d3e7b4"
down_revision = "e3b8d1f6a2c7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "customer_duplicate_cases",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("customer_id", sa.BigInteger(), nullable=False),
        sa.Column("candidate_id", sa.BigInteger(), nullable=False),
        sa.Column("score", sa.Numeric(6, 2), nullable=True),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("decision", sa.String(length=24), nullable=True),
        sa.Column("resolved_owner_id", sa.BigInteger(), nullable=True),
        sa.Column("resolved_by", sa.BigInteger(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("remark", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["customers.id"]),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_customer_dup_status", "customer_duplicate_cases", ["status"])
    op.create_index("ix_customer_dup_customer", "customer_duplicate_cases", ["customer_id"])


def downgrade() -> None:
    op.drop_index("ix_customer_dup_customer", table_name="customer_duplicate_cases")
    op.drop_index("ix_customer_dup_status", table_name="customer_duplicate_cases")
    op.drop_table("customer_duplicate_cases")
