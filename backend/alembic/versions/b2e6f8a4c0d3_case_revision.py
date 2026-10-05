"""案例加修订版、修订关系与审核历史（第四批 §5.1.5，口径已确认=修订稿）。

Revision ID: b2e6f8a4c0d3
Revises: a8d4f0c2e6b1
Create Date: 2026-10-06

三个已确认问题都在这一处收口：

1. **已发布案例允许主管原地改关键内容、仍保持"已发布"**——批准的是 A 版内容，
   改完变成了 B 版内容，审核结论却被复用。已确认口径（2026-10-05，方案 1）：
   **修订稿**——已发布版继续可供培训，另建修订稿重新走审核，批准后替换当前发布版。
2. **审核历史只剩最后一条**：`review_note` 是单值，被下一次审核覆盖。
   加 `review_history`（JSONB，逐条追加：轮次/结论/意见/审核人/时间），
   与打样"制作完成"的结构化事件同一套做法（§3.4）。
3. 顺带需要"这一版是第几版、取代了谁"：`version` + `revision_of_id`。

`superseded`（已被修订版取代）是新增的**状态值**，不需要迁移改枚举（status 是字符串），
但它会进 `CASE_STATUS_LABEL` 与列表口径：默认只列当前版本，历史版本显式要看。
"""

from alembic import op

revision = "b2e6f8a4c0d3"
down_revision = "a8d4f0c2e6b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 历史数据一律记第 1 版、无修订关系：它们确实没有版本链信息。
    op.execute(
        "ALTER TABLE sales_cases ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1"
    )
    op.execute(
        "ALTER TABLE sales_cases ADD COLUMN IF NOT EXISTS revision_of_id BIGINT "
        "REFERENCES sales_cases(id)"
    )
    op.execute(
        "ALTER TABLE sales_cases ADD COLUMN IF NOT EXISTS review_history JSONB"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_sales_cases_revision_of "
        "ON sales_cases (revision_of_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_sales_cases_revision_of")
    op.execute("ALTER TABLE sales_cases DROP COLUMN IF EXISTS review_history")
    op.execute("ALTER TABLE sales_cases DROP COLUMN IF EXISTS revision_of_id")
    op.execute("ALTER TABLE sales_cases DROP COLUMN IF EXISTS version")
