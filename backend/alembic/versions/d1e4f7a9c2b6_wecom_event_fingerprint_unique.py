"""企微事件回调指纹独立列并唯一化，修复并发重复落任务。"""

from alembic import op


revision = "d1e4f7a9c2b6"
down_revision = "c8e3d5f9b2a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE wecom_sync_jobs "
        "ADD COLUMN IF NOT EXISTS event_fingerprint VARCHAR(64)"
    )
    # 兼容已有数据：把历史 JSON 中的指纹迁到独立列。
    op.execute(
        "UPDATE wecom_sync_jobs SET event_fingerprint = detail->>'fingerprint' "
        "WHERE job_type = 'external_contact' AND event_fingerprint IS NULL "
        "AND detail->>'fingerprint' IS NOT NULL"
    )
    # 历史上并发重复可能已经产生多条，保留最早一条，其余清空指纹后再建唯一索引。
    op.execute(
        "UPDATE wecom_sync_jobs AS duplicate SET event_fingerprint = NULL "
        "WHERE duplicate.event_fingerprint IS NOT NULL AND duplicate.id NOT IN ("
        "SELECT MIN(id) FROM wecom_sync_jobs WHERE event_fingerprint IS NOT NULL "
        "GROUP BY event_fingerprint)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_wecom_sync_event_fingerprint "
        "ON wecom_sync_jobs (event_fingerprint) WHERE event_fingerprint IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_wecom_sync_event_fingerprint")
    op.execute("ALTER TABLE wecom_sync_jobs DROP COLUMN IF EXISTS event_fingerprint")
