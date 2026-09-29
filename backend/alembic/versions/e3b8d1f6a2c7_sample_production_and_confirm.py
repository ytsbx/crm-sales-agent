"""打样生产侧资料与客户确认（文档 §3.5）

文档要求「生产打样记录用途、工艺/材质、图纸版本、样品数量、目标完成日、
验收标准、费用和责任人，再分别记录制作、寄出、签收、客户确认」，并且
**「客户收到样品不等于样品被接受」**。

两件事：

1. 生产打样资料落 `sample_requests`（样品数量不重复存——它在
   `sample_items.quantity` 上，一单可以多样）；
2. 客户确认独立成 `confirm_status` 三态 + 确认时间/备注：签收是物流事实，
   确认是业务事实，混在一起就答不了"这批样到底过没过"。

`sample_fee` 与 `confirm_status` 带 server_default，历史行不需要回填，
语义上也确实都是"还没收钱/还没确认"。
"""

import sqlalchemy as sa
from alembic import op

revision = "e3b8d1f6a2c7"
down_revision = "d1a7c3e9b5f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in (
        sa.Column("purpose", sa.String(length=255), nullable=True),
        sa.Column("craft", sa.String(length=128), nullable=True),
        sa.Column("material", sa.String(length=128), nullable=True),
        sa.Column("drawing_version", sa.String(length=64), nullable=True),
        sa.Column("target_completion_date", sa.Date(), nullable=True),
        sa.Column("acceptance_criteria", sa.Text(), nullable=True),
        sa.Column("sample_fee", sa.Numeric(16, 2), nullable=False, server_default="0"),
        sa.Column("production_owner_id", sa.BigInteger(), nullable=True),
        sa.Column("made_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "confirm_status", sa.String(length=16), nullable=False, server_default="pending"
        ),
        sa.Column("customer_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirm_remark", sa.String(length=255), nullable=True),
    ):
        op.add_column("sample_requests", column)
    op.create_index(
        "ix_sample_requests_confirm_status", "sample_requests", ["confirm_status"]
    )


def downgrade() -> None:
    op.drop_index("ix_sample_requests_confirm_status", table_name="sample_requests")
    for name in (
        "confirm_remark",
        "customer_confirmed_at",
        "confirm_status",
        "made_at",
        "production_owner_id",
        "sample_fee",
        "acceptance_criteria",
        "target_completion_date",
        "drawing_version",
        "material",
        "craft",
        "purpose",
    ):
        op.drop_column("sample_requests", name)
