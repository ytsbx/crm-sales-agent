"""审计日志按业务对象反查的索引（回收站复审：接出「谁删除、谁合并」）。

## 为什么加

回收站现在要回答「这条记录是谁删的」。删除的操作人**只记在 `audit_logs`** 里
（`business_type` / `business_id` / `operator_id`），列表页一次要把整页
（最多 200 条 × 四类对象）的留痕全取回来。

而 `audit_logs` 在此之前**只有主键一个索引** —— 按 `(business_type, business_id)`
去查就是全表扫描。这张表是全项目长得最快的一张（每次写操作都记一条），
本机开发库已经 3.7 万行。不加索引，回收站会随着流水账一年比一年慢。

## 索引怎么定

    (business_type, business_id, id DESC)

- 前两列是等值条件（`business_type = 'customer' AND business_id IN (...)`）；
- 第三列 `id DESC` 配合「取**最新**一条」的写法：
  查询是 `WHERE action = 'delete' AND ... ORDER BY id DESC`，每条只取第一条。
  把 `id` 放进索引并倒序，就能直接顺着索引走、不必再排序。
  取最新一条不是可有可无的 ——「删掉 → 恢复 → 再删」之后要显示的是**本次**那个人，
  不是第一次那条旧留痕。
- **`action` 刻意不放进索引**：它的选择度极低（值就那几个），放进去只是把索引撑大。
  `business_id IN (...)` 已经把范围收得足够小。

## 顺带说明

纯新增索引，不改列、不回填数据。用的是**非并发**的 `CREATE INDEX`，
迁移期间会短暂持有写锁 —— 本项目的迁移都在停机窗口执行，够用。
真要在线建得用 `CREATE INDEX CONCURRENTLY`，而它要求跑在事务外，
alembic 默认包在事务里，本项目没这个必要。
"""
from alembic import op

revision = "a7c1e5b9d3f2"
down_revision = "b8e2d4f6a1c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_audit_logs_business "
        "ON audit_logs (business_type, business_id, id DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_audit_logs_business")
