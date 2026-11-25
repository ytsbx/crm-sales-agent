"""企微事件回调指纹独立列并唯一化，修复并发重复落任务。"""

from alembic import context, op
import sqlalchemy as sa


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
    # 历史重复不能由迁移擅自选择或改写；暂停迁移交人工确认。
    if not context.is_offline_mode():
        duplicates = op.get_bind().execute(sa.text(
            "SELECT event_fingerprint, COUNT(*) AS n, "
            "string_agg(id::text, ',' ORDER BY id) AS row_ids "
            "FROM wecom_sync_jobs WHERE event_fingerprint IS NOT NULL "
            "GROUP BY event_fingerprint HAVING COUNT(*) > 1"
        )).fetchall()
        if duplicates:
            detail = "; ".join(
                f"fingerprint={row[0]} count={row[1]} ids={row[2]}"
                for row in duplicates[:20]
            )
            raise RuntimeError(
                "wecom_sync_jobs 存在重复企微事件指纹，无法建立唯一约束；"
                f"请人工确认历史记录后重试迁移：{detail}"
            )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_wecom_sync_event_fingerprint "
        "ON wecom_sync_jobs (event_fingerprint) WHERE event_fingerprint IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_wecom_sync_event_fingerprint")
    op.execute("ALTER TABLE wecom_sync_jobs DROP COLUMN IF EXISTS event_fingerprint")
