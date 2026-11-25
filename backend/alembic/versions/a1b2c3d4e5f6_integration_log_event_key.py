"""集成日志事件键独立列与成功事件唯一约束。

同一 ERP 回调可能并发到达。JSONB 查重只能覆盖串行重放，无法阻止两个事务
同时通过查重后各写一条成功日志。迁移前发现历史重复时主动失败，不自动删除、
合并或选择业务记录。
"""

from alembic import context, op
import sqlalchemy as sa


revision = "a1b2c3d4e5f6"
down_revision = "d1e4f7a9c2b6"
branch_labels = None
depends_on = None

INDEX_NAME = "uq_integration_logs_erp_success_event"


def upgrade() -> None:
    op.add_column(
        "integration_logs",
        sa.Column("event_key", sa.String(length=64), nullable=True),
    )
    # 历史回调已经把稳定键写入 response_data；迁移到独立列后查询不再依赖 JSONB。
    op.execute(
        "UPDATE integration_logs "
        "SET event_key = response_data->>'event_key' "
        "WHERE integration_type = 'erp' AND direction = 'inbound' "
        "AND event_key IS NULL "
        "AND response_data->>'event_key' IS NOT NULL"
    )
    # 离线脚本没有可查询的数据库；重复检查仍在真实在线升级时执行。
    if not context.is_offline_mode():
        duplicate_rows = op.get_bind().execute(
            sa.text(
                "SELECT event_key, COUNT(*) AS n, "
                "string_agg(id::text, ',' ORDER BY id) AS row_ids "
                "FROM integration_logs "
                "WHERE integration_type = 'erp' AND direction = 'inbound' "
                "AND status = 'success' AND event_key IS NOT NULL "
                "GROUP BY event_key HAVING COUNT(*) > 1"
            )
        ).fetchall()
        if duplicate_rows:
            detail = "; ".join(
                f"event_key={row[0]} count={row[1]} ids={row[2]}"
                for row in duplicate_rows[:20]
            )
            raise RuntimeError(
                "integration_logs 存在重复的成功 ERP 事件，无法建立唯一约束；"
                f"请人工确认历史记录后重试迁移：{detail}"
            )
    op.execute(
        f"CREATE UNIQUE INDEX {INDEX_NAME} "
        "ON integration_logs (event_key) "
        "WHERE integration_type = 'erp' AND direction = 'inbound' "
        "AND status = 'success' AND event_key IS NOT NULL"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {INDEX_NAME}")
    op.drop_column("integration_logs", "event_key")
