"""售后调整台账按「稳定业务身份」去重（第十批审查 ①）。

Revision ID: b4e7c1a9d2f5
Revises: a31d6b7c902e
Create Date: 2026-10-13

## 修的是什么

同一笔外部退款"先核定 100、再更正为 200 并重新核定"，会生成**两条** `pending`
台账：100 + 200 = 300 —— 凭空多出一笔待处理退款。

根因不是漏了去重，而是**去重的键选错了**：

- `financial_adjustments` 原来只有 `uq_financial_adjustments_source_diff`
  （按差异 ID 唯一）；
- 而差异指纹 `build_diff_key` **把金额与期间也算进哈希**
  （这是差异队列的正确设计：值变了必须出新的一条，否则"上次已核定"会把新问题
  一起吞掉）；
- 于是"更正金额"必然产生**新差异**，`source_diff_id` 换了，唯一约束就绕过去了。

差异记录保留历史是对的（要能回答"改过什么"），但**台账不能按差异版本各记一笔**。

## 改法

加 `source_key`：由「系统 + 店铺 + 对象类型 + 来源单」组成，**不含金额与期间**。
再加**部分唯一索引**，只约束 `status='pending'` 的行：

- 同一来源**未处理**时再来一次更正 → 应用层**更新那一条**的金额并写明更正痕迹
  （不是新增）；
- 已经处理过（pending 之外）→ 不占位，允许新增一条 —— 那是真正的**差额**。

## 存量数据

历史行 `source_key` 为 NULL，部分唯一索引把 NULL 排除在外，因此**不需要回填**
也不会建不起来。为什么不回填：老行的 `source_diff_id` 指向的差异可能已被后续
更正取代，按现在的口径去推它的"业务身份"是在猜；而且回填会把历史的多条
pending 撞成冲突，需要人为决定保留哪条 —— 那属于财务判断，不该由迁移脚本做。
存量里若确实存在重复待处理，请按有记录的人工复核流程处理。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "b4e7c1a9d2f5"
down_revision = "a31d6b7c902e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "financial_adjustments",
        sa.Column("source_key", sa.String(length=160), nullable=True),
    )
    op.create_index(
        "uq_financial_adjustments_source_pending",
        "financial_adjustments",
        ["source_key"],
        unique=True,
        postgresql_where=sa.text("status = 'pending' AND source_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_financial_adjustments_source_pending",
        table_name="financial_adjustments",
    )
    op.drop_column("financial_adjustments", "source_key")
