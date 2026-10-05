"""合同钉死报价版本 + 生成稿落盘（外部审查第二批 阶段 B）。

Revision ID: d1f5b9c3e7a4
Revises: c7e3a9f5b2d8
Create Date: 2026-10-05

背景（外部审查 2026-10-05 第二条第 2、4 点）：

1. 合同只记 `quote_id`，不记**报价版本**。报价是可以出 V2 的（`quote_versions` 表
   一行一版，金额挂在版本上），所以事后无法证明这份合同的金额依据的是 V1 还是 V2。
   → 加 `quote_version_id`，生成时钉死；之后报价出 V2、V3 都不影响已生成的合同。

2. 下载是每次拿**当前资料**重新渲染。客户名从 A 改成 B 之后，同一份合同再下载，
   正文还是 A（正文走快照），抬头却成了 B；两次下载的内容甚至不一样。
   → 加 `generated_file_id`：生成时把 PDF 渲染一次落盘并建文件记录（带 sha256），
   下载直接返回当时那一份。本字段为空的老数据回落实时渲染（router 里有说明）。

两列都可空，历史数据不受影响。
"""

from alembic import op
import sqlalchemy as sa

revision = "d1f5b9c3e7a4"
down_revision = "c7e3a9f5b2d8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "contract_documents",
        sa.Column("quote_version_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_contract_documents_quote_version",
        "contract_documents",
        "quote_versions",
        ["quote_version_id"],
        ["id"],
    )
    op.add_column(
        "contract_documents",
        sa.Column("generated_file_id", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("contract_documents", "generated_file_id")
    op.drop_constraint(
        "fk_contract_documents_quote_version", "contract_documents", type_="foreignkey"
    )
    op.drop_column("contract_documents", "quote_version_id")
