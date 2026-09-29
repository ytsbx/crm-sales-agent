"""OA 实例的唯一约束从"业务键"改到"幂等键"（文档 :43 vs §11.3 :152）

文档有两条要求，对唯一性的要求正好相反：

- :43「相同业务动作须有唯一约束，**网络重试不能重复建单**」→ 同一轮提交要能挡住；
- §11.3 :152「提交、**驳回、重提**、撤销、回填和失败重试跑通」→ 重提必须能建新一轮。

原先唯一约束放在（需求 + 版本 + 类型）上，把"重提"也一起挡死了——驳回之后再提，
代码会命中唯一约束、复用那条被驳回的行，**根本发不出去**。

改法：业务键降为普通索引（列表按它查），新增 `idempotency_key` 唯一列，
由调用方按"第几轮提交"决定值。历史行的键按业务键 + 轮次回填。
"""

import sqlalchemy as sa
from alembic import op

revision = "c4f8a2e6d1b7"
down_revision = "b2e6f8a4c1d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("oa_instances", sa.Column("submit_round", sa.BigInteger(), nullable=False,
                                            server_default="1"))
    op.add_column("oa_instances", sa.Column("idempotency_key", sa.String(length=128),
                                            nullable=True))
    # 历史行（当前库里没有，但迁移要能安全跑在已有数据上）按业务键 + 轮次回填
    op.execute(
        "update oa_instances set idempotency_key = "
        "inquiry_id || ':' || inquiry_version || ':' || oa_type || ':' || submit_round "
        "where idempotency_key is null"
    )
    op.alter_column("oa_instances", "idempotency_key", nullable=False)
    op.drop_constraint("uq_oa_instance_business", "oa_instances", type_="unique")
    op.create_unique_constraint("uq_oa_instance_idempotency", "oa_instances", ["idempotency_key"])
    op.create_index(
        "ix_oa_instances_business", "oa_instances", ["inquiry_id", "inquiry_version", "oa_type"]
    )


def downgrade() -> None:
    op.drop_index("ix_oa_instances_business", table_name="oa_instances")
    op.drop_constraint("uq_oa_instance_idempotency", "oa_instances", type_="unique")
    op.create_unique_constraint(
        "uq_oa_instance_business", "oa_instances", ["inquiry_id", "inquiry_version", "oa_type"]
    )
    op.drop_column("oa_instances", "idempotency_key")
    op.drop_column("oa_instances", "submit_round")
