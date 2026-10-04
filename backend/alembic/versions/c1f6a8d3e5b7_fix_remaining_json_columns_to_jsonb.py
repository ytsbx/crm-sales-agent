"""把 4a9c2e6b8d1f 之后新建的 JSON 列也改回 JSONB

`4a9c2e6b8d1f` 只修了当时已存在的三个列（`customer_merge_logs` 两列 +
`logistics_quotes.raw_data`）。之后又有 5 个迁移用普通 `sa.JSON()` 建表，
而模型侧声明的是 `JSONType`（PostgreSQL 下映射到 JSONB）—— 库里是 `json`、
模型是 `jsonb`，autogenerate 每次都在报"有变更"，且 JSONB 才能建 GIN 索引、
用 `@>` 操作符。

这里把这一批显式改回 JSONB（与 4a9c2e6b8d1f 同一手法）。
"""

from alembic import op

revision = "c1f6a8d3e5b7"
down_revision = "b7e4c1a9f2d6"
branch_labels = None
depends_on = None

# (表名, 列名)：模型侧都是 JSONType（PG=JSONB），迁移却建成了 json
COLUMNS = [
    ("approval_rules", "conditions"),
    ("approval_rules", "action"),
    ("approval_rule_versions", "payload"),
    ("product_insights", "images"),
    ("custom_inquiries", "extra"),
    ("contract_documents", "filled_data"),
    ("sales_cases", "problem_tags"),
]


def upgrade() -> None:
    for table, column in COLUMNS:
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} TYPE jsonb USING {column}::jsonb"
        )


def downgrade() -> None:
    for table, column in COLUMNS:
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} TYPE json USING {column}::json"
        )
