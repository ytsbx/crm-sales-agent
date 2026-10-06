"""回收候选：等待期快照与提前执行标记（返修单第六批 8 / 追加口径 1）。

Revision ID: c9e5b3a7f1d4
Revises: d7e1f5a9b3c4
Create Date: 2026-10-06

## 为什么要这三列

返修单第 8 条：候选上**存了** `due_at` / `deferred_until`，但批准时根本不看它们，
所以"预告 7 天""暂缓 30 天"只是一句话，当天就能收走。修法是把等待期真正接进
`decide_candidate` 的校验；要接进去，就得让行上说得清"这条是按几天预告的"。

- `notice_days`：提名那一刻用的预告天数快照。
- `defer_days`：这次暂缓用的等待天数快照。

为什么不每次回读 `system_settings`：管理员中途把预告期从 7 天改成 3 天，
**不该让已经在跑的候选提前到期**（追加口径 1 明确要求"不能自行改变旧候选的期限"）。
`due_at` / `deferred_until` 本身是绝对时间、天然不受配置影响；这两列只是把
"当时按几天算的"一并留在行上，复核时能回答"为什么它的到期日是那天"。

- `early_approved`：预告期/暂缓等待期没满就要求回收，主管走了**例外动作**。
  与既有的 `exception_approved`（带着履约保护硬收）分开记 ——
  一个回答"是不是没等满等待期"，一个回答"是不是带着在途订单/欠款收的"。

## 存量数据处理

本表是返修单 6.3 新建的（迁移 `a4b8c2d6e0f1`），升级顺序上排在本次之前，
所以**可能已经有行**（预告期还没到、主管还没批的客户）。这里按时长差回填：

- `notice_days` = `due_at - notice_at`（取整天）；
- `defer_days` = `deferred_until - decided_at`（取整天）。

回填只是把"当时用的参数"补上，**不改动任何时间点**：`due_at` 与
`deferred_until` 一个字节都不动，旧候选的期限因此完全保持原样。
推不出时长的（`due_at` 为空等）留空，不猜。
"""

from alembic import op

revision = "c9e5b3a7f1d4"
down_revision = "d7e1f5a9b3c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE public_pool_recycle_candidates "
        "ADD COLUMN IF NOT EXISTS notice_days BIGINT"
    )
    op.execute(
        "ALTER TABLE public_pool_recycle_candidates "
        "ADD COLUMN IF NOT EXISTS defer_days BIGINT"
    )
    op.execute(
        "ALTER TABLE public_pool_recycle_candidates "
        "ADD COLUMN IF NOT EXISTS early_approved BOOLEAN NOT NULL DEFAULT FALSE"
    )
    # 回填（只补参数快照，不动任何时间点）
    op.execute(
        """
        UPDATE public_pool_recycle_candidates
           SET notice_days = GREATEST(
                   0,
                   FLOOR(EXTRACT(EPOCH FROM (due_at - notice_at)) / 86400)
               )::BIGINT
         WHERE notice_days IS NULL
           AND due_at IS NOT NULL
           AND notice_at IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE public_pool_recycle_candidates
           SET defer_days = GREATEST(
                   0,
                   FLOOR(EXTRACT(EPOCH FROM (deferred_until - decided_at)) / 86400)
               )::BIGINT
         WHERE defer_days IS NULL
           AND deferred_until IS NOT NULL
           AND decided_at IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE public_pool_recycle_candidates DROP COLUMN IF EXISTS early_approved")
    op.execute("ALTER TABLE public_pool_recycle_candidates DROP COLUMN IF EXISTS defer_days")
    op.execute("ALTER TABLE public_pool_recycle_candidates DROP COLUMN IF EXISTS notice_days")
