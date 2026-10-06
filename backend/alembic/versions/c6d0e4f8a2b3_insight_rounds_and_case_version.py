"""第四轮返工：洞察逐轮留痕表 + 案例版本唯一约束（P1-4 / P1-5）。

Revision ID: c6d0e4f8a2b3
Revises: b5c9d3e7f1a2
Create Date: 2026-10-06

## 为什么要 `product_insight_rounds`

`product_insights` 上那几个字段（`review_round` / `review_note` / `reviewer_id` /
`reviewed_at`）**只有最后一轮的值**：第 3 轮通过之后，第 1 轮报的是什么内容、
第 2 轮是谁为什么否掉的，全被覆盖了。审计日志那边只记"改了哪几个字段名"，
没有当时的内容，等于没留痕。

一行 = 一轮，存"这一轮报的内容 + 谁批的 + 什么时候 + 结论"。
`UNIQUE (insight_id, round)` 既是幂等的保证（同一轮重复提交不新增行），
也是并发时的最后一道底线。

外键写 `ON DELETE CASCADE` —— 轮次记录是洞察的从属明细，
而且不写级联的话，清理历史数据时会撞外键把清理脚本整个打断（这个坑踩过一次）。

## 为什么要案例的版本唯一索引

`revise_case` 的老实现对"在途修订稿"那行加 `FOR UPDATE`，但**第一次修订时
那行根本不存在** —— 锁不到任何行，两个并发请求各建一条
`revision_of_id=1, version=2`（双请求实测复现）。

应用层已改为"先锁原版那一行再重查"，这个索引是数据库层面的底线：
同一原版、同一版本号，未删除的记录只允许一条。

## 存量数据

建索引前先处理历史遗留的重复版本：同一 `(revision_of_id, version)` 只保留
**id 最小**的那条，其余转 `superseded`（**不删** —— 删除会让发生过的事失去痕迹，
而 superseded 状态本来就表示"不是当前版本、内容仍可读"）。
线上这套数据目前是空的（案例与洞察都是 0 条），这段主要为已有环境的库兜底。
"""

from alembic import op

revision = "c6d0e4f8a2b3"
down_revision = "b5c9d3e7f1a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS product_insight_rounds (
            id               BIGSERIAL PRIMARY KEY,
            insight_id       BIGINT      NOT NULL
                             REFERENCES product_insights(id) ON DELETE CASCADE,
            round            BIGINT      NOT NULL DEFAULT 1,
            content_snapshot JSONB,
            submitted_by     BIGINT,
            submitted_at     TIMESTAMPTZ,
            review_result    VARCHAR(16),
            reviewer_id      BIGINT,
            reviewed_at      TIMESTAMPTZ,
            review_note      VARCHAR(255),
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_insight_round_insight "
        "ON product_insight_rounds (insight_id)"
    )
    # 同一轮只允许一行：幂等的保证，也是并发时的底线
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_insight_round "
        "ON product_insight_rounds (insight_id, round)"
    )

    # ---- 案例：同一原版同一版本号只允许一条未删除记录 ----
    # 先给存量重复"降级"（保留 id 最小的那条），否则索引建不起来
    op.execute(
        """
        UPDATE sales_cases SET status = 'superseded'
        WHERE id IN (
            SELECT id FROM (
                SELECT id, row_number() OVER (
                    PARTITION BY revision_of_id, version ORDER BY id
                ) AS rn
                FROM sales_cases
                WHERE revision_of_id IS NOT NULL AND deleted_at IS NULL
            ) ranked
            WHERE rn > 1
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_case_revision_version
        ON sales_cases (revision_of_id, version)
        WHERE revision_of_id IS NOT NULL AND deleted_at IS NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_case_revision_version")
    op.execute("DROP INDEX IF EXISTS uq_insight_round")
    op.execute("DROP INDEX IF EXISTS ix_insight_round_insight")
    op.execute("DROP TABLE IF EXISTS product_insight_rounds")
