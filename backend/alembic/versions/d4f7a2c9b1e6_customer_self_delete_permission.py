"""数据迁移：客户软删除 + 业务员只能删自己负责的客户

`scripts/seed.py` 给 `salesperson` 补了 `customer:delete_own`，但那是"重新播种
才生效"：已存在的库（本地/生产）不会再跑一遍 seed 的角色授权，于是老库业务员
仍然删不了自己的客户，而干净种子库（CI）却是好的。

这里做一次幂等的数据迁移：权限行缺了就补、角色关联缺了就补，已存在的跳过。
`customer:delete` 本身**不**下放给业务员 —— 那会把公海客户（无负责人、人人可见）
也放进删除范围；两档权限见 `customer/service.py::ensure_can_delete_customer`。
"""

from alembic import op

revision = "d4f7a2c9b1e6"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) 权限目录里可能都还没有这一项（老库从未被新 seed 覆盖）
    op.execute(
        """
        insert into permissions (code, name, resource, action)
        select 'customer:delete_own', '删除自己的客户', 'customer', 'delete_own'
        where not exists (select 1 from permissions where code = 'customer:delete_own')
        """
    )
    # 2) 关联到业务员角色（已存在则跳过，可重复执行）
    op.execute(
        """
        insert into role_permissions (role_id, permission_id)
        select r.id, p.id
        from roles r, permissions p
        where r.code = 'salesperson'
          and p.code = 'customer:delete_own'
          and not exists (
              select 1 from role_permissions rp
              where rp.role_id = r.id and rp.permission_id = p.id
          )
        """
    )


def downgrade() -> None:
    # 先清所有角色关联再删权限行：只删 salesperson 那一行的话，若有其它角色
    # 也被授过这个码，删权限行会撞外键。
    op.execute(
        """
        delete from role_permissions rp
        using permissions p
        where rp.permission_id = p.id
          and p.code = 'customer:delete_own'
        """
    )
    op.execute("delete from permissions where code = 'customer:delete_own'")
