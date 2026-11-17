"""OA 实例来源：区分历史钉钉单与自研 OA 单

2026-11-13 起合同审批改走自研 OA，`oa_instances` 同时存两类实例：
老钉钉行只读保留（provider=dingtalk），新合同行 provider=self_oa。
"""

from alembic import op

revision = "e7b3c1d5f9a2"
down_revision = "d4f7a2c9b1e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 本地曾手工补列但未推进迁移版本，正式升级仍需补齐索引。
    op.execute(
        "ALTER TABLE oa_instances ADD COLUMN IF NOT EXISTS "
        "provider VARCHAR(16) NOT NULL DEFAULT 'dingtalk'"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_oa_instances_provider "
        "ON oa_instances (provider)"
    )


def downgrade() -> None:
    op.drop_index("ix_oa_instances_provider", table_name="oa_instances")
    op.drop_column("oa_instances", "provider")
