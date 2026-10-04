"""数据迁移：回填种子用户的部门（老库漂移）

`scripts/seed.py` 里用户的 `department_id` 只在**创建时**写：已存在的用户不会被
更新。于是"先建用户、后来才加部门归属"的老库会留下 department_id 为空的人，
数据范围（self 之外按部门看）和通知定位都会因此退化——和之前 `order:assign`
那处"只在创建时写权限"是同一类漂移。

这里只回填**空值**，且只针对种子里的 4 个账号（admin / zhangsan / lisi / wangwu）：
非空的一律不动，避免覆盖真实环境里有意的归属调整。
"""

from alembic import op

revision = "e5c1a7b3d9f4"
down_revision = "d2a7b9c4e6f8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        update users u
        set department_id = d.id
        from (
            select id from departments where name = '销售部' order by id limit 1
        ) d
        where u.department_id is null
          and u.username in ('admin', 'zhangsan', 'lisi', 'wangwu')
        """
    )


def downgrade() -> None:
    # 回填是幂等的修正，不做反向：真回滚会把真实环境里原本就为空的人重新置空，
    # 反而制造问题。要撤销请手工处理。
    pass
