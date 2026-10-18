"""撞单裁定：未决案件唯一 + 裁定前后归属（返工单 6.5）。

Revision ID: f8d2b6a4c1e7
Revises: e7b3f1a5c9d2
Create Date: 2026-10-06

两件事：

1. **同一对客户最多一张未决案件**。原来只有应用层的"先查有没有、再插入"，
   两个并发查重会各插一条，同一件事在待裁定队列里出现两次。
   用**表达式索引**把 A/B 与 B/A 归一成同一对（`LEAST`/`GREATEST`），
   并且只约束未决的（`status='pending'`）—— 结案后允许再开新的，
   那时是新一轮争议，不该被历史挡着。

2. `before_owners`：裁定**前**两条客户的归属（`{客户id: 原负责人id}`）。
   原来只留 `resolved_owner_id`（裁定后），事后回看答不出
   "这次裁定把谁从谁手里改到了谁名下"。

存量数据处理：若库里已经存在同一对的**多张未决案件**（历史遗留），
唯一索引会建不起来。这里先**结案**掉多余的（保留 id 最小的那一张，
其余置为 `resolved` 并标 `decision='superseded'`），而不是删 ——
删除会让已经发生过的事失去痕迹。
"""

from alembic import context, op
import sqlalchemy as sa

revision = "f8d2b6a4c1e7"
down_revision = "e7b3f1a5c9d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE customer_duplicate_cases "
        "ADD COLUMN IF NOT EXISTS before_owners JSONB"
    )

    # 历史重复不能由迁移擅自裁定；在线升级时暂停交人工处理。
    if not context.is_offline_mode():
        duplicates = op.get_bind().execute(sa.text(
            "SELECT LEAST(customer_id, candidate_id) AS customer_id, "
            "GREATEST(customer_id, candidate_id) AS candidate_id, COUNT(*) AS n, "
            "string_agg(id::text, ',' ORDER BY id) AS row_ids "
            "FROM customer_duplicate_cases WHERE status = 'pending' "
            "GROUP BY LEAST(customer_id, candidate_id), GREATEST(customer_id, candidate_id) "
            "HAVING COUNT(*) > 1"
        )).fetchall()
        if duplicates:
            detail = "; ".join(
                f"pair=({row[0]},{row[1]}) count={row[2]} ids={row[3]}"
                for row in duplicates[:20]
            )
            raise RuntimeError(
                "customer_duplicate_cases 存在重复未决案件，无法建立唯一约束；"
                f"请人工确认历史记录后重试迁移：{detail}"
            )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_customer_dup_pending_pair
        ON customer_duplicate_cases (
            LEAST(customer_id, candidate_id),
            GREATEST(customer_id, candidate_id)
        )
        WHERE status = 'pending'
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_customer_dup_pending_pair")
    op.execute(
        "ALTER TABLE customer_duplicate_cases DROP COLUMN IF EXISTS before_owners"
    )
