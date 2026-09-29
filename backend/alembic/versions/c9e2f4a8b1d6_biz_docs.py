"""对外单据模板与生成文件（文档 §四「对外模板及生成文件」/ §3.5 / 场景12）

两张表：

- `biz_doc_templates`：模板**按类型多版本并存**。改模板 = 新增一版，
  已生成的文件仍指向它们当时用的那一版；
- `biz_docs`：已生成的打样需求单 / 下单文件。每次生成 = 新增一行
  （version+1、parent_id 指向前一版），**旧行一个字不动**——这就是场景12
  「旧文件不被覆盖」的落地点。

`input_snapshot` 用 JSONB 而不是 sa.JSON()：模型侧是 JSONType（PG 下映射到
JSONB）。历史上 `b5d7f9a1c3e6` 等几个迁移用 sa.JSON() 建表，把模型声明的 JSONB
盖成了 json，后来专门用 `4a9c2e6b8d1f` 修回来。新表按模型的实际类型建，
免得再产生一次同样的漂移。

默认模板不在这里插：由 `bizdoc.service.ensure_default_templates` 幂等播种，
这样"业务给了正式模板后新增一版替换"不会和迁移里的一次性数据打架。
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c9e2f4a8b1d6"
down_revision = "e8c3a6b9d2f5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "biz_doc_templates",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("doc_type", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("remark", sa.String(length=255), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        # 同一类型下版本号唯一，不同类型各自从 1 起
        sa.UniqueConstraint("doc_type", "version", name="uq_biz_doc_template_version"),
    )
    op.create_index("ix_biz_doc_templates_doc_type", "biz_doc_templates", ["doc_type"])

    op.create_table(
        "biz_docs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("doc_no", sa.String(length=32), nullable=False),
        sa.Column("doc_type", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("parent_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("owner_id", sa.BigInteger(), nullable=True),
        sa.Column("customer_id", sa.BigInteger(), nullable=True),
        sa.Column("contact_id", sa.BigInteger(), nullable=True),
        sa.Column("sample_request_id", sa.BigInteger(), nullable=True),
        sa.Column("order_id", sa.BigInteger(), nullable=True),
        sa.Column("inquiry_id", sa.BigInteger(), nullable=True),
        sa.Column("quote_id", sa.BigInteger(), nullable=True),
        sa.Column("source_type", sa.String(length=24), nullable=True),
        sa.Column("source_id", sa.BigInteger(), nullable=True),
        sa.Column("source_no", sa.String(length=32), nullable=True),
        sa.Column("source_version", sa.BigInteger(), nullable=True),
        sa.Column("template_id", sa.BigInteger(), nullable=False),
        sa.Column("template_version", sa.BigInteger(), nullable=False),
        sa.Column("input_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("void_reason", sa.String(length=255), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["template_id"], ["biz_doc_templates.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("doc_no"),
    )
    op.create_index("ix_biz_docs_doc_type", "biz_docs", ["doc_type"])
    op.create_index("ix_biz_docs_customer", "biz_docs", ["customer_id"])
    op.create_index("ix_biz_docs_sample", "biz_docs", ["sample_request_id"])
    op.create_index("ix_biz_docs_order", "biz_docs", ["order_id"])


def downgrade() -> None:
    op.drop_index("ix_biz_docs_order", table_name="biz_docs")
    op.drop_index("ix_biz_docs_sample", table_name="biz_docs")
    op.drop_index("ix_biz_docs_customer", table_name="biz_docs")
    op.drop_index("ix_biz_docs_doc_type", table_name="biz_docs")
    op.drop_table("biz_docs")
    op.drop_index("ix_biz_doc_templates_doc_type", table_name="biz_doc_templates")
    op.drop_table("biz_doc_templates")
