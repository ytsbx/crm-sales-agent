"""打样单加「交样时间」（与「要求完成日」是两个不同的日期）。

Revision ID: b7f2c4e8a1d3
Revises: e1a3c5d7f902
Create Date: 2026-10-16

## 为什么要加这一列

主人 2026-10-16 指出：

> 要求完成时间和交样时间不一定是一个时间，交样时间是填充在excel里面的，
> 要求完成时间是钉钉里的字段

我原来的做法是**把两者当成一个**：Excel 的「交样时间」（H2 那格）直接取
`target_completion_date`（也就是钉钉单上的「要求完成日期」）——
**那是两个不同的日期**：

    target_completion_date  车间什么时候**做完**（钉钉单上的字段）
    deliver_date            什么时候**交给客户**（申请表上的格）

合并成一个字段，导出的申请表就会把一个日期填到另一个含义的格子里。
所以这里补一列 `deliver_date`，让两者各归各位。

存量数据处理：**不回填**（保持 NULL = 未填）。把老单子的
`target_completion_date` 抄进 `deliver_date` 等于替用户断言"这两个日期一样"——
那正是这次要修的错误假设。
"""
from alembic import op

revision = "b7f2c4e8a1d3"
down_revision = "e1a3c5d7f902"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE sample_requests ADD COLUMN IF NOT EXISTS deliver_date DATE")


def downgrade() -> None:
    op.execute("ALTER TABLE sample_requests DROP COLUMN IF EXISTS deliver_date")
