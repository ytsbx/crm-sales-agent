"""样品申请支持软删：给 sample_requests 加 deleted_at。

Revision ID: b4d1f8a2c6e3
Revises: a3c9e5f7b2d1
Create Date: 2026-10-15

## 为什么原来没有这一列

这个模块**当初是刻意不做软删的**（`sample/service.py` 里写着「sample_requests
**没有** deleted_at（这个模块不做软删）」，还提醒后人别照别的模块加
`deleted_at.is_(None)`）。理由是打样单属于**留痕单据**：审批轮次、车间依据、
图纸版本都挂在它上面。

## 为什么现在加

留痕的理由仍然成立，但它推不出"不许删"—— 真正的死角是**误建的单子没法清理**：
选错客户、点错按钮建出来的那一条，只能永远挂在样品列表里
（主人 2026-10-15 问：「样品管理没有删除吗，就一直待在那？」）。

所以补的是**软删 + 收闸**，不是放开物理删除：

- 只允许 `pending` / `rejected`（还没走流程的）删除；
  已批准 / 已寄出 / 已签收的**拒绝**——它们连着审批留痕与车间依据。
- 删掉的是 `deleted_at` 标记，行、审计、附件、历史全留着。

## 判据落在哪

- 可删范围写在 `sample/router.py` 的 `SAMPLE_DELETABLE_STATUSES`，
  不在这里、也不散落各处。
- 列表默认 `deleted_at IS NULL`；`include_deleted=true` 时只看已删的。

## 存量数据

全部为 NULL（= 都没删），老单据行为不变。
"""
from alembic import op

revision = "b4d1f8a2c6e3"
# ⚠️ 接在**当前主干 head** 之后（`a3c9e5f7b2d1` 打样申请单字段那条）。
# 不要改回更早的 revision：两条线并行时各自加迁移，沿用旧的 down_revision
# 会让 alembic 出现**两个 head**，`alembic upgrade head` 直接报
# "Multiple head revisions are present"，部署时才发现就晚了。
# 本迁移只给 `sample_requests` 加一列，与其它迁移无先后依赖。
down_revision = "a3c9e5f7b2d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 软删标记：NULL = 没删。可空、不给默认值 —— 存量行不需要回填，
    # `ADD COLUMN ... IF NOT EXISTS` 之后老数据天然是 NULL。
    op.execute("ALTER TABLE sample_requests ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ")


def downgrade() -> None:
    op.execute("ALTER TABLE sample_requests DROP COLUMN IF EXISTS deleted_at")
