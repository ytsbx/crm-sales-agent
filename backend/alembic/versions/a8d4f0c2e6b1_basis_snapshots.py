"""口径基准快照表：老客池与首次成交日按年冻结（第三批 §4.1.5）。

Revision ID: a8d4f0c2e6b1
Revises: f6c2e8a4b1d9
Create Date: 2026-10-05

问题：老客池与"首次成交"每次都从**可变的订单状态**现算——

    veteran_ids = select(customer_id).where(status != 'cancelled', created_at < 年初)
    first_deal  = min(created_at) group by customer_id where status != 'cancelled'

于是事后取消一张**往年**订单，会让这个客户从整年老客池里消失；
取消人家的首单，首次成交月也跟着往后跳。历史指标被就地改写，去年报出去的数
今年再看就变了——而且没人能复现当初那一版。

做法（保守版，边界明确）：
- **过去年份**：第一次被读取时算一次并落库（惰性冻结），之后一直用快照；
- **当年**：照旧实时算、不冻结（数据还在产生，冻了反而会冻在半路上）；
- 补算入口：`POST /analytics/sales-targets/bases/refreeze?year=` 供管理员在有
  正当理由时重算某一年，写审计。

一行一年（`year` 主键），只存指标真正需要的两份数据，避免整库快照：
- `veteran_customer_ids`：年初之前已有非取消订单的客户 id；
- `first_deal_month`：首次成交落在该年的客户 → 月份（`YYYY-MM`）。
"""

from alembic import op

revision = "a8d4f0c2e6b1"
down_revision = "f6c2e8a4b1d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS analytics_basis_snapshots (
            year INTEGER PRIMARY KEY,
            veteran_customer_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            first_deal_month JSONB NOT NULL DEFAULT '{}'::jsonb,
            metric_basis_version VARCHAR(64) NOT NULL,
            computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            computed_by BIGINT
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS analytics_basis_snapshots")
