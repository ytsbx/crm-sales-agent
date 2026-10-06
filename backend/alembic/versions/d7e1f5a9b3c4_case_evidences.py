"""案例证据多条 + 客户类型检索（第四轮返工 P2-8）。

Revision ID: d7e1f5a9b3c4
Revises: c6d0e4f8a2b3
Create Date: 2026-10-06

## 为什么要 `case_evidences`

原来案例表上写了四个列（`quote_id` / `order_id` / `sample_id` / `opportunity_id`），
**每种只能挂一条**。可 §3.7 说的是"编写者从已有时间线和单据选证据"——
一个案例常常是多张单据支撑起来的（三个订单、两份打样），单列表达不了；
而且类型写死在列名里，以后想挂合同、询价就得再加列、再改一轮代码。

一行 = 一条引用，`UNIQUE (case_id, kind, business_id)` 保证同一条不挂两遍。

## 存量数据

**把旧的四个列搬进新表**（每列一条，非空才搬）。旧列**保留不删**：
序列化仍在读它们（兼容老调用方与老查询），删除列会让没跟上的代码直接报错。
新代码以证据表为准，并会把旧列同步成"每类的第一条"，两者不会各说各话。

## 关于客户类型检索

不改表：案例的"客户类型"来自**关联客户**（`customers.customer_type`，企业/个人），
列表接口 join 一下即可。方案 §3.7 的原文是"按客户类型、产品线、阶段及问题检索"，
其中后三项是案例自己的字段，客户类型只能从客户带出来——所以它不是案例表的列。
"""

from alembic import op

revision = "d7e1f5a9b3c4"
down_revision = "c6d0e4f8a2b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS case_evidences (
            id          BIGSERIAL PRIMARY KEY,
            case_id     BIGINT      NOT NULL
                        REFERENCES sales_cases(id) ON DELETE CASCADE,
            kind        VARCHAR(24) NOT NULL,
            business_id BIGINT      NOT NULL,
            label       VARCHAR(128),
            note        VARCHAR(255),
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_case_evidence_case "
        "ON case_evidences (case_id)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_case_evidence "
        "ON case_evidences (case_id, kind, business_id)"
    )

    # 老数据搬迁：把案例表上那四个列变成新表里的行。
    # `ON CONFLICT DO NOTHING`：万一某个环境跑过两遍（或手工插过），不会因重复失败。
    for kind, column in (
        ("quote", "quote_id"),
        ("order", "order_id"),
        ("sample", "sample_id"),
        ("opportunity", "opportunity_id"),
    ):
        op.execute(
            f"""
            INSERT INTO case_evidences (case_id, kind, business_id, created_at)
            SELECT id, '{kind}', {column}, now()
            FROM sales_cases
            WHERE {column} IS NOT NULL
            ON CONFLICT (case_id, kind, business_id) DO NOTHING
            """
        )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_case_evidence")
    op.execute("DROP INDEX IF EXISTS ix_case_evidence_case")
    op.execute("DROP TABLE IF EXISTS case_evidences")
