"""回款分配表：一笔记账可以覆盖多期（第十批，主人 2026-10-13 拍板）。

Revision ID: c7a1e5b3f8d2
Revises: b4e7c1a9d2f5
Create Date: 2026-10-13

## 背景

`payment_records.receivable_plan_id` 是**单个**外键：一笔回款只能挂一期。
实测（两期各应收 100/200，给第一期登记并确认 200）：超出的 100 留在第一期，
第二期仍是 `overdue`；既不能自动抵后续期，也没有"改挂期"的入口。

主人定的口径：

- 一笔银行流水**仍然是一行** `payment_records`（不把 200 拆成两条回款）；
- 超出该期应收的部分**按到期日从早到晚**自动抵后续期；
- 金额必须**实际分配到某一期**才算该期收齐（不能因为订单总额够了就把各期都标完成）；
- 分配可人工调整，但**必须留审计**，不允许系统静默改写。

## 这个迁移做三件事

1. 建 `payment_allocations` 表（payment_id, plan_id, amount, source, 时间戳），
   同一 (payment_id, plan_id) 唯一。
2. **回填历史数据**：给每条**已确认**且 `receivable_plan_id` 非空的回款，
   按该列生成一条**全额**分配（`source='auto'`）。
   这样各期"已收"金额与迁移前**完全一致** —— 口径不变、对账数字不变，
   只是把原来隐含在 `receivable_plan_id` 里的分配关系显式化。
3. 不动 `payment_records` 的任何列（用户当初选的那一期是"用户意图"，要保留）。

## 为什么不回填未确认/未挂期的回款

- **未确认**（`pending`/`rejected`）：钱还没到账，不参与"已收"汇总
  （`confirmed_amount` 只算 `status='confirmed'`），回填了反而要额外维护状态联动；
- **`receivable_plan_id` 为空**：本来就挂不到任何一期，没有可回填的分配。
  这类回款在切入口径后仍按"未分配"处理 —— 与迁移前行为一致。

## 回滚

`downgrade` 直接删表。因为在迁移期间**没有改动 `payment_records`**，
删掉分配表就回到"按 `receivable_plan_id` 汇总"的旧口径，历史数据无损。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "c7a1e5b3f8d2"
down_revision = "b4e7c1a9d2f5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "payment_allocations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("payment_id", sa.BigInteger(), nullable=False),
        sa.Column("plan_id", sa.BigInteger(), nullable=False),
        sa.Column("amount", sa.Numeric(16, 2), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="auto"),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["payment_id"], ["payment_records.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["plan_id"], ["receivable_plans.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("payment_id", "plan_id", name="uq_payment_allocations_pair"),
    )
    op.create_index(
        "ix_payment_allocations_payment", "payment_allocations", ["payment_id"]
    )
    op.create_index("ix_payment_allocations_plan", "payment_allocations", ["plan_id"])

    # 回填：已确认 + 挂了期的回款 → 一条全额分配（口径与迁移前完全一致）
    op.execute(
        """
        INSERT INTO payment_allocations
            (payment_id, plan_id, amount, source, created_by, created_at, updated_at)
        SELECT id,
               receivable_plan_id,
               received_amount,
               'auto',
               confirmed_by,
               COALESCE(confirmed_at, created_at),
               COALESCE(confirmed_at, created_at)
          FROM payment_records
         WHERE status = 'confirmed'
           AND receivable_plan_id IS NOT NULL
           AND received_amount > 0
        """
    )


def downgrade() -> None:
    op.drop_index("ix_payment_allocations_plan", table_name="payment_allocations")
    op.drop_index("ix_payment_allocations_payment", table_name="payment_allocations")
    op.drop_table("payment_allocations")
