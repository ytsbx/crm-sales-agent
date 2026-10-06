"""交接逐项结果表 + 合并冲突留痕列（返工单 6.6 / 6.7 / 6.8）。

Revision ID: b5c9d3e7f1a2
Revises: a4b8c2d6e0f1
Create Date: 2026-10-06

## 为什么要有 `wecom_transfer_items`

老实现把交接的失败明细塞进 `wecom_sync_jobs.detail` 这个 JSON，
而且是 `detail["wecom_failures"] = failures[:10]` —— **在存的时候就截断了**，
失败计数又取这个截断后列表的长度。失败 12 条，报表上只能看到一部分，
而且补查也补不出来：残的那一份就是库里那一份。

逐项一行之后，三件事同时成立：
1. 失败明细完整保存，界面可以分页翻；
2. 两侧结果分开记 —— 改 CRM 归属与调企微转接是两个独立动作，
   企微调用发出去就撤不回来，"CRM 成功、企微失败"必须能表达，
   而不是笼统一个"部分失败"；
3. 重试按项做 —— 只挑没办完的，已转出去的关系不会再发一遍
   （重复调用企微会报重复操作，运营看到一堆莫名其妙的失败）。

## 为什么要给 `customer_merge_logs` 加 `conflicts`

客户合并会碰到"两边同一个 SKU 定了不同价""税号不一致"这类必须由人拍板的事。
原来代码是静默挑一个用，事后没人说得出为什么变成这样。现在把当时的选择记下来。

## 存量数据

两张表都是新增/加列，没有存量需要转换：`conflicts` 允许为空，历史合并记录
原本也没有这个信息（不编造）。

## 为什么要 ON DELETE CASCADE

`wecom_transfer_items` 是**某次交接的从属记录**：任务没了，逐项结果就没有留着的
意义。更现实的原因是清理会撞外键 —— 既有的运维脚本与回归套件会按条件删
`wecom_sync_jobs`，没有级联时删一条被引用过的任务就会报错、把整个清理打断
（实测到过一次：某个套件的清理语句因此中断，它自己的夹具也留在了库里，
最后被守门套件抓出来）。
"""

from alembic import op

revision = "b5c9d3e7f1a2"
down_revision = "a4b8c2d6e0f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS wecom_transfer_items (
            id              BIGSERIAL PRIMARY KEY,
            job_id          BIGINT      NOT NULL
                            REFERENCES wecom_sync_jobs(id) ON DELETE CASCADE,
            kind            VARCHAR(24) NOT NULL,
            business_id     BIGINT,
            label           VARCHAR(200),
            from_owner_id   BIGINT,
            to_owner_id     BIGINT,
            from_owner_name VARCHAR(64),
            to_owner_name   VARCHAR(64),
            crm_status      VARCHAR(16) NOT NULL DEFAULT 'pending',
            crm_error       TEXT,
            wecom_status    VARCHAR(16) NOT NULL DEFAULT 'not_applicable',
            wecom_error     TEXT,
            attempts        BIGINT      NOT NULL DEFAULT 0,
            updated_at      TIMESTAMPTZ
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_wecom_transfer_items_job "
        "ON wecom_transfer_items (job_id, kind)"
    )
    # 按"还没办完"筛的索引：重试与"只看未完成"的列表都走它
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_wecom_transfer_items_open "
        "ON wecom_transfer_items (job_id, crm_status)"
    )

    # 合并时的冲突处理口径（当时谁选了什么）
    op.execute(
        "ALTER TABLE customer_merge_logs "
        "ADD COLUMN IF NOT EXISTS conflicts JSONB"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE customer_merge_logs DROP COLUMN IF EXISTS conflicts"
    )
    op.execute("DROP INDEX IF EXISTS ix_wecom_transfer_items_open")
    op.execute("DROP INDEX IF EXISTS ix_wecom_transfer_items_job")
    op.execute("DROP TABLE IF EXISTS wecom_transfer_items")
