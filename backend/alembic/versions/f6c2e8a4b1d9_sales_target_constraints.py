"""销售目标加约束：期间标准化、非负、人/部门互斥、作用域唯一（第三批 §4.1.2 / §4.1.6）。

Revision ID: f6c2e8a4b1d9
Revises: e4b8d2f6a1c9
Create Date: 2026-10-05

`sales_targets` 原来没有任何约束，于是：

- `period` 只过 `datetime.strptime(p, '%Y-%m')`，而它是**宽容**的——`2026-1` 也通过。
  存进去以后，实际值按 `f"{year}-{int(m):02d}"` 生成 `2026-01`，两边永远对不上，
  那一行目标就变成"设了但达成为 0"。
- `new_customer_target` / `sales_target` 允许负数。
- `user_id` 与 `department_id` 可以**同时**有值——到底是个人目标还是团队目标说不清。
- 没有唯一约束：同一 (期间, 人, 部门) 可以插出多行，`month-user_id` 键冲突时
  先查到哪条算哪条（§4.1.2）。

本迁移只做**能自证的事**：能救的期间标准化；救不了的（彻底不合格式）与重复行
**软删**（不物理删，可回滚）；然后加约束与唯一索引，把口径钉在数据库层。

唯一索引用 `NULLS NOT DISTINCT`（PG 15+）：`user_id`/`department_id` 都是可空的，
默认的 NULL 视为互不相同会让"全公司目标"能插出无数行。配合 `WHERE deleted_at IS NULL`，
软删的历史行不参与去重。
"""

from alembic import op

revision = "f6c2e8a4b1d9"
down_revision = "e4b8d2f6a1c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ① 能救的期间先标准化：2026-1 → 2026-01
    op.execute(
        r"""
        UPDATE sales_targets
           SET period = to_char(to_date(period, 'YYYY-MM'), 'YYYY-MM')
         WHERE period ~ '^[0-9]{4}-[0-9]{1,2}$'
           AND period <> to_char(to_date(period, 'YYYY-MM'), 'YYYY-MM')
        """
    )
    # ② 救不了的软删：格式都不对的期间是查不出来的死行，留着只会挡住约束
    op.execute(
        r"""
        UPDATE sales_targets SET deleted_at = now()
         WHERE deleted_at IS NULL
           AND period !~ '^[0-9]{4}-(0[1-9]|1[0-2])$'
        """
    )
    # ③ 同一 (期间, 人, 部门) 的重复行：保留最新一条，其余软删（可回滚）
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY period, COALESCE(user_id, -1), COALESCE(department_id, -1)
                       ORDER BY updated_at DESC NULLS LAST, id DESC
                   ) AS rn
              FROM sales_targets
             WHERE deleted_at IS NULL
        )
        UPDATE sales_targets t SET deleted_at = now()
          FROM ranked r
         WHERE t.id = r.id AND r.rn > 1
        """
    )
    # ④ 约束：期间格式、非负、人/部门互斥
    op.execute(
        r"""
        ALTER TABLE sales_targets
          ADD CONSTRAINT ck_sales_targets_period
          CHECK (period ~ '^[0-9]{4}-(0[1-9]|1[0-2])$')
        """
    )
    op.execute(
        """
        ALTER TABLE sales_targets
          ADD CONSTRAINT ck_sales_targets_nonneg
          CHECK (new_customer_target >= 0 AND sales_target >= 0
                 AND repeat_customer_target >= 0)
        """
    )
    op.execute(
        """
        ALTER TABLE sales_targets
          ADD CONSTRAINT ck_sales_targets_owner
          CHECK (NOT (user_id IS NOT NULL AND department_id IS NOT NULL))
        """
    )
    # ⑤ 作用域唯一：同一期间同一作用域只允许一条活行
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_sales_targets_scope
            ON sales_targets (period, user_id, department_id)
            NULLS NOT DISTINCT
         WHERE deleted_at IS NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_sales_targets_scope")
    op.execute("ALTER TABLE sales_targets DROP CONSTRAINT IF EXISTS ck_sales_targets_owner")
    op.execute("ALTER TABLE sales_targets DROP CONSTRAINT IF EXISTS ck_sales_targets_nonneg")
    op.execute("ALTER TABLE sales_targets DROP CONSTRAINT IF EXISTS ck_sales_targets_period")
