"""公海回收候选（预告）表（返工单 6.3）。

Revision ID: a4b8c2d6e0f1
Revises: f8d2b6a4c1e7
Create Date: 2026-10-06

## 为什么要这张表

老实现是"扫描命中 → **直接清空负责人** → 客户进公海"。问题在于**不可逆**：
一个客户可能只是业务员出差两周没点跟进，回收掉之后他跟了半年的客户
就进了公海，谁都能领走。文档 §11.2 要求的是"先预告 → 主管复核 → 再执行"。

这张表就是那个"预告"：把"为什么该回收"的全部依据（原负责人、命中规则、
两个活跃时钟、当时的保护事项）落成一行，交给主管逐条或批量决定。
执行时还会**重新检查**一次最新情况（预告之后又有了新跟进/报价/订单/回款就拦下）。

## 关键约束

`uq_pool_candidate_open`：**同一客户同时只允许一张未结候选**。
老实现靠"先查有没有、再插入"，定时任务重跑或两个实例同时扫就会各插一条，
主管看到重复待办。部分唯一索引只在 `pending/deferred/approved` 下生效 ——
结案或恢复之后还能重新提名。

存量数据：这张表是新建的，没有存量需要转换。
"""

from alembic import op

revision = "a4b8c2d6e0f1"
down_revision = "f8d2b6a4c1e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public_pool_recycle_candidates (
            id                         BIGSERIAL PRIMARY KEY,
            customer_id                BIGINT      NOT NULL
                                       REFERENCES customers(id),
            owner_id                   BIGINT,
            rule_id                    BIGINT,
            level                      VARCHAR(8),
            rule_days                  BIGINT,
            last_contact_at            TIMESTAMPTZ,
            last_progress_at           TIMESTAMPTZ,
            last_active_at             TIMESTAMPTZ,
            protection_snapshot        JSONB,
            status                     VARCHAR(16) NOT NULL DEFAULT 'pending',
            notice_at                  TIMESTAMPTZ NOT NULL,
            due_at                     TIMESTAMPTZ,
            decided_by                 BIGINT,
            decided_at                 TIMESTAMPTZ,
            decision_note              VARCHAR(255),
            deferred_until             TIMESTAMPTZ,
            exception_approved         BOOLEAN NOT NULL DEFAULT FALSE,
            executed_at                TIMESTAMPTZ,
            restored_at                TIMESTAMPTZ,
            restored_by                BIGINT,
            restore_note               VARCHAR(255),
            restore_conflict_owner_id  BIGINT,
            created_at                 TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_pool_candidate_status "
        "ON public_pool_recycle_candidates (status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_pool_candidate_customer "
        "ON public_pool_recycle_candidates (customer_id)"
    )
    # 同一客户同时只允许一张未结候选（见模块说明）
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_pool_candidate_open
        ON public_pool_recycle_candidates (customer_id)
        WHERE status IN ('pending', 'deferred', 'approved')
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_pool_candidate_open")
    op.execute("DROP INDEX IF EXISTS ix_pool_candidate_customer")
    op.execute("DROP INDEX IF EXISTS ix_pool_candidate_status")
    op.execute("DROP TABLE IF EXISTS public_pool_recycle_candidates")
