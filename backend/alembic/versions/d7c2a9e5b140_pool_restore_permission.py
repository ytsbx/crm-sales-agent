"""将公海恢复授权迁移到独立角色权限。"""

from alembic import op

revision = "d7c2a9e5b140"
down_revision = "f3a8c2d7b619"
branch_labels = None
depends_on = None


def upgrade():
    # 原来按用户的有效权限集合判断，两个权限可以来自不同角色。
    # 复制旧门槛所在角色，复核权限仍由接口单独检查。
    op.execute("""
        WITH new_permission AS (
            INSERT INTO permissions (code, name, resource, action)
            VALUES ('customer:pool_restore', '恢复公海客户', 'customer', 'pool_restore')
            ON CONFLICT (code) DO NOTHING
            RETURNING id
        ), legacy_permission AS (
            SELECT COALESCE((
                SELECT NULLIF(value ->> 'text', '')
                FROM system_settings
                WHERE key = 'pool_recycle_restore_permission'
            ), 'customer:assign') AS code
        )
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT rp.role_id, new_permission.id
        FROM legacy_permission
        JOIN permissions p ON p.code = legacy_permission.code
        JOIN role_permissions rp ON rp.permission_id = p.id
        CROSS JOIN new_permission
        ON CONFLICT DO NOTHING
    """)


def downgrade():
    # 保留新权限配置和旧设置行，回退代码可继续沿用其原有设置。
    pass
