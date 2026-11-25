"""报价明细增加"成本完整性"快照标记

## 为什么要这一版（2026-10-13，第七批 P1-1）

`ProductCost` 的四项成本（采购/生产/包装/加工）都是可空列，`total_cost`
把 NULL 当 0 参与合计 —— 它自己的文档就写着"调用方必须自己看 `is_complete`"。
但**报价明细的成本快照没有记这个判断**：`cost_snapshot` 是 NOT NULL default 0，
"没成本"和"成本真的为 0"落库后长得一样。

后果（审查实测）：只填 `purchase_cost=10`，其余三项为 NULL，报价 100、
保护价 120 —— 合计成本 10 → 毛利率 90% → 命中"毛利 ≥ 30% 免审" →
**低于保护价的报价被自动批准**。这是"算不出来却算出了一个好看的数字"。

## 这一列怎么用

- `False`：生成快照时成本**不完整**（`ProductCost` 的 `is_complete` 为假，
  即四项里有 NULL）→ 毛利视为**未知**，不参与毛利免审。
- `True`：四项都填了（显式 0 也算填）→ 完整，照常算毛利。
- `NULL`：**历史数据**（本迁移之前生成的报价明细）→ 判据回退到原来的
  "成本 ≤ 0 视为缺失"。这样做是**刻意**的：老单的成本快照我们已经无法
  回溯它当时四项填没填全，若把 NULL 一律当成"不完整"，等于把历史上所有
  老报价单的毛利免审全部作废、审批量凭空暴涨。新数据从严，老数据不动。

可空而不是 NOT NULL default false，正是为了让"历史未知"与"确知不完整"
在库里分得开 —— 与 `sku_field_authorities` 那类快照列同一个思路。
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "9c1f7a2b4d83"
down_revision = "4b7e2a9c5f31"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "quote_items",
        sa.Column(
            "cost_complete_snapshot",
            sa.Boolean(),
            nullable=True,
            comment="生成快照时产品成本四项是否填全；NULL=历史数据（按旧判据）",
        ),
    )


def downgrade() -> None:
    op.drop_column("quote_items", "cost_complete_snapshot")
