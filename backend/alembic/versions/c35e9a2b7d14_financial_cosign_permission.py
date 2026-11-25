"""独立财务会签权限，默认授予财务；不改管理员例外和存量审批快照。"""
from alembic import op
revision = "c35e9a2b7d14"
down_revision = "b24d8f1a6c03"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""INSERT INTO permissions (code,name,resource,action)
        VALUES ('quote:finance_approve','财务会签','quote','finance_approve')
        ON CONFLICT (code) DO NOTHING""")
    op.execute("""INSERT INTO role_permissions (role_id,permission_id)
        SELECT r.id,p.id FROM roles r CROSS JOIN permissions p
        WHERE r.code='finance' AND p.code='quote:finance_approve'
        ON CONFLICT DO NOTHING""")


def downgrade():
    # 新权限可能已由管理员授给其他角色，不在回退时删除这些授权。
    pass
