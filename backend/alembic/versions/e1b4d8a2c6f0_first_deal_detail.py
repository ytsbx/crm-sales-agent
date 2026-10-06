"""口径基准快照：首次成交的日期与来源订单跟月份一起冻（返修 R07）。

Revision ID: e1b4d8a2c6f0
Revises: c9e5b3a7f1d4
Create Date: 2026-10-06

## 为什么要这一列

R07：新客的**归属月份**早就冻在 `first_deal_month` 里了，但"首次成交日期"是每次
打开报表**实时重算**的（取该客户当前最早的非取消订单）。原首单被取消后，重算结果
会跳到下一张单 —— 于是同一行里出现"计入一月的新客、首次成交日期显示三月"，
数据自己跟自己矛盾，而且事后无从解释当初那一版是怎么算的。

修法是让日期与来源订单**跟着月份一起冻**。单开一列而不改 `first_deal_month`：
那一列的值是 `"YYYY-MM"` 纯字符串，改成对象会让所有读它的地方（口径页、下钻、
报表）都得跟着改，风险不划算。

## 存量数据

**不回填。** 本列为 NULL 的含义就是"这份快照是旧版冻的，日期不可考" ——
读的时候如实显示"未知"。回头拿现在的订单补一个日期进去，等于把"当时那一版是
怎么算的"改掉，与冻结这件事本身相冲。

（`analytics_basis_snapshots` 只在**过去年份**被读取时才会有行；老行不会因为这个
迁移多出日期，也不会少掉月份。）
"""

from alembic import op

revision = "e1b4d8a2c6f0"
down_revision = "c9e5b3a7f1d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE analytics_basis_snapshots "
        "ADD COLUMN IF NOT EXISTS first_deal_detail JSONB"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE analytics_basis_snapshots DROP COLUMN IF EXISTS first_deal_detail"
    )
