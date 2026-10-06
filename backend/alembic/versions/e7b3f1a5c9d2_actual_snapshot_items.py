"""实绩快照的明细条目：结账时连「是哪几张单」一起冻（返工单第 4 条）。

Revision ID: e7b3f1a5c9d2
Revises: d4f8b2c6a1e9
Create Date: 2026-10-06

`analytics_actual_snapshots`（c9d3e7b1f5a4）冻住了**汇总值**，但构成它的**明细**
一直是实时算的：结账之后客户退掉一张单，汇总还是当天那个数（对），
可点开明细会少一笔（对不上）。"合计"和"点开看明细"是同一个数的一体两面，
一个冻一个不冻，等于自相矛盾。

这张表存**结账那一刻的明细快照**：哪张单、多少钱、当时归谁、什么时间。
存的是快照值不是指针——订单后来被改价、退货，这里都不变；
`owner_id` 记的是当时的**签单归属**，交接之后新负责人再点开，看到的仍是老签单人的数
（与汇总同源，见 analytics/targets.py 的 `sales_owner`）。

`at_text` 存**已格式化**的可读时间字符串（如 `2026-02-03 09:30`）而不是时间戳：
"冻结"的意义就是连展示都钉住；顺带也免了 date 与 datetime 两种类型挤在一列。

不建外键：`record_id` 指向 orders / shipment_batches / payment_records / customers
四张表之一，看 `record_type` 而定，一个列指不了四张表；这里也用不上级联。
老数据不回填：之前从没冻过明细，当时那批单据已经无从复原（结账时点的数才是真相，
事后按现在的状态倒推一个"看起来像"的明细，比空着更坏）。
"""

from alembic import op

revision = "e7b3f1a5c9d2"
down_revision = "d4f8b2c6a1e9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS analytics_actual_snapshot_items (
            id                   BIGSERIAL PRIMARY KEY,
            period               VARCHAR(7)  NOT NULL,
            metric               VARCHAR(24) NOT NULL,
            record_type          VARCHAR(24) NOT NULL,
            record_id            BIGINT      NOT NULL,
            owner_id             BIGINT,
            label                VARCHAR(255),
            amount               NUMERIC(16, 2) NOT NULL DEFAULT 0,
            at_text              VARCHAR(32),
            metric_basis_version VARCHAR(64) NOT NULL,
            frozen_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            frozen_by            BIGINT
        )
        """
    )
    # 下钻永远是「某期间 + 某指标（+ 某些归属人）」，就按这个顺序建
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_actual_snapshot_items_lookup "
        "ON analytics_actual_snapshot_items (period, metric, owner_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_actual_snapshot_items_lookup")
    op.execute("DROP TABLE IF EXISTS analytics_actual_snapshot_items")
