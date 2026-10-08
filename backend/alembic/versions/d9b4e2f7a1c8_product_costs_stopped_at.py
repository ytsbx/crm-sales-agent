"""给 product_costs 加 stopped_at：人工停用的成本立即退出核价（第十一批 11.5）。

## 为什么要单独一列

点"失效"原来的做法是把 `effective_to` 写成**今天**，而取价判据是
"截止日**含**当天"——于是点了失效当天照样取得到（复审实测："接口提示已失效，
当天再取还是取到同一条"）。

两个现成的改法都不行：

- **把截止日写成昨天**：能生效，但库里的截止日就不再等于操作日，事后翻记录
  分不清"人停用的"还是"自然到昨天到期"；而且"今天才生效"的成本会形成
  `effective_from > effective_to` 的怪区间。
- **把取价判据改成"不含当天"**：会连累另一类正常设置的有效期 ——
  那是一个有明确语义的规则，不该为了一个"立即停用"被动。

所以单开一列记"**什么时候被人停用的**"（时间戳，不是布尔：翻记录时能看出时刻）。
口径 2026-10-08 与主人确认。

## 对老数据的影响

现有行一律为 `NULL` = 未被人工停用 —— 与它们现在的行为完全一致，**零改动**。
被这条成本停用过的历史报价不受影响：报价记的是它当时取到的那份成本快照。
"""
from alembic import op
import sqlalchemy as sa

revision = "d9b4e2f7a1c8"
down_revision = "c3f8a1d6e9b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "product_costs",
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("product_costs", "stopped_at")
