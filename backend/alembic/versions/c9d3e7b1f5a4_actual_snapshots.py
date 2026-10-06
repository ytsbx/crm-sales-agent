"""期间实绩快照：结账后金额不再随订单状态变（第三批 §4.1.5 后半）。

Revision ID: c9d3e7b1f5a4
Revises: a3f7c1e5b9d2
Create Date: 2026-10-06

`analytics_basis_snapshots` 当初把"客户集合与首次成交日"按年冻住了，
但**金额**仍然是每次现算的——客户今年退掉去年的一张单，去年那一期的数就跟着变小。

这张表把「（期间 × 作用域 × 指标）→ 实绩」抄一份存档，结账以后不再变。
作用域用字符串键（`company` / `dept:3` / `user:5`）而不是两个可空列：
复合主键里的 NULL 在 PG 里互不相等，用可空列做键等于没约束。

不建外键：scope_key 是个复合含义的字符串，指向 users 或 departments 看前缀而定，
一个列没法同时指向两张表；这里用不上级联，硬凑外键反而挡住正常的部门调整。
"""

from alembic import op

revision = "c9d3e7b1f5a4"
down_revision = "a3f7c1e5b9d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS analytics_actual_snapshots (
            period               VARCHAR(7)  NOT NULL,
            scope_key            VARCHAR(32) NOT NULL,
            metric               VARCHAR(24) NOT NULL,
            actual_value         NUMERIC(16, 2) NOT NULL DEFAULT 0,
            metric_basis_version VARCHAR(64) NOT NULL,
            frozen_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            frozen_by            BIGINT,
            note                 TEXT,
            PRIMARY KEY (period, scope_key, metric)
        )
        """
    )
    # 按期间查是主用法（"这个月结账了没有"）
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_analytics_actual_snapshots_period "
        "ON analytics_actual_snapshots (period)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_analytics_actual_snapshots_period")
    op.execute("DROP TABLE IF EXISTS analytics_actual_snapshots")
