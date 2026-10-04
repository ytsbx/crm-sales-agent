"""数据迁移：给销售主管角色补 order:assign（交期变更代确认）

`scripts/seed.py` 给 `sales_manager` 补了 `order:assign`，但那是"重新播种才生效"：
已存在的库（本地/生产）不会再跑一遍 seed 的角色授权，于是老库主管发起/确认
交期变更仍然 40301「转移订单负责人」权限缺失，而干净种子库（CI）却是好的——
正是"本地绿、线上红"那类漂移。

这里做一次幂等的数据迁移：权限行缺了就补、角色关联缺了就补，已存在的跳过。
"""

from alembic import op

revision = "b7e4c1a9f2d6"
down_revision = "a3c5e7b9d1f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) 权限目录里可能都还没有这一项（老库从未被新 seed 覆盖）
    op.execute(
        """
        insert into permissions (code, name, resource, action)
        select 'order:assign', '转移订单负责人', 'order', 'assign'
        where not exists (select 1 from permissions where code = 'order:assign')
        """
    )
    # 2) 关联到销售主管（已存在则跳过，可重复执行）
    op.execute(
        """
        insert into role_permissions (role_id, permission_id)
        select r.id, p.id
        from roles r, permissions p
        where r.code = 'sales_manager'
          and p.code = 'order:assign'
          and not exists (
              select 1 from role_permissions rp
              where rp.role_id = r.id and rp.permission_id = p.id
          )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        delete from role_permissions rp
        using roles r, permissions p
        where rp.role_id = r.id
          and rp.permission_id = p.id
          and r.code = 'sales_manager'
          and p.code = 'order:assign'
        """
    )
