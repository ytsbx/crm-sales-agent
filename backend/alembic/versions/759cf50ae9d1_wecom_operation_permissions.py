"""Separate WeCom operations from contact handling."""

from alembic import op

revision = "759cf50ae9d1"
down_revision = "b3e9d7f160ac"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        INSERT INTO permissions (code, name, resource, action)
        VALUES
            ('wecom:sync_directory', '同步企微部门和成员', 'wecom', 'sync_directory'),
            ('wecom:sync_contacts', '同步企微联系人和跟进关系', 'wecom', 'sync_contacts'),
            ('wecom:import_preview', '预览企微客户导入', 'wecom', 'import_preview'),
            ('wecom:import', '确认企微客户导入', 'wecom', 'import'),
            ('wecom:transfer_preview', '查看企微离职交接清单', 'wecom', 'transfer_preview'),
            ('wecom:transfer', '执行企微离职交接', 'wecom', 'transfer')
        ON CONFLICT (code) DO NOTHING
    """)
    op.execute("""
        UPDATE permissions SET name = '整理企微联系人' WHERE code = 'wecom:manage'
    """)
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.code = 'sales_manager'
          AND p.code IN ('wecom:import_preview', 'wecom:transfer_preview')
          AND EXISTS (
              SELECT 1 FROM role_permissions rp JOIN permissions vp ON vp.id = rp.permission_id
              WHERE rp.role_id = r.id AND vp.code = 'wecom:view'
          )
        ON CONFLICT DO NOTHING
    """)


def downgrade():
    op.execute("""
        DELETE FROM role_permissions WHERE permission_id IN (
            SELECT id FROM permissions WHERE resource = 'wecom'
            AND action IN ('sync_directory', 'sync_contacts', 'import_preview', 'import', 'transfer_preview', 'transfer')
        )
    """)
    op.execute("""
        DELETE FROM permissions WHERE resource = 'wecom'
        AND action IN ('sync_directory', 'sync_contacts', 'import_preview', 'import', 'transfer_preview', 'transfer')
    """)
    op.execute("""
        UPDATE permissions SET name = '同步与归一转处理' WHERE code = 'wecom:manage'
    """)
