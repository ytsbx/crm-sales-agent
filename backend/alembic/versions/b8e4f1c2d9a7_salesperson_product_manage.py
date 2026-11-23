"""业务员可维护产品：给 salesperson 角色补 product:manage。

2026-11-23 主人确认：业务员需要新建/维护产品与 SKU。
种子角色同步更新，这里保证已经初始化的库也能获得同一权限。
"""

from alembic import op

revision = "b8e4f1c2d9a7"
down_revision = "c92b6f8143de"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id
        FROM roles r CROSS JOIN permissions p
        WHERE r.code = 'salesperson' AND p.code = 'product:manage'
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM role_permissions rp
        USING roles r, permissions p
        WHERE rp.role_id = r.id AND rp.permission_id = p.id
          AND r.code = 'salesperson' AND p.code = 'product:manage'
        """
    )
