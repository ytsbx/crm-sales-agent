"""演示用户资料：部门树、手机号、几个钉钉绑定。

为什么单独一个脚本：`seed_visual_demo.py` 造的是客户/商机/订单这类业务数据，
而用户是登录账号 —— 它们的资料随时可能被人在界面上改过。所以本脚本的铁律是
**只补空值，绝不覆盖已有值**：手机有号就不动，部门已经不在"销售部"就不动。

安全闸：库名含 `crm_sales_agent`（领导看的库）直接拒绝；不含 prod/demo/test 也拒绝。

用法（注意：必须指向演示库）：
    cd backend
    DATABASE_URL=postgresql+asyncpg://crm:***@127.0.0.1:5432/crm_prod \
    PYTHONPATH=. .venv/bin/python scripts/seed_demo_users.py
"""

import asyncio
import os

from sqlalchemy import select

from app.core.database import SessionLocal
from app.modules.user.model import Department, User

# 顶级部门 + 两个下级，让「上级部门」列不再全是「顶级」
DEPARTMENTS: list[tuple[str, str | None]] = [
    ('生产部', None),
    ('财务部', None),
    ('技术部', None),
    ('销售一部', '销售部'),
    ('销售二部', '销售部'),
]

# 固定号码段（不用随机）：重复执行结果一致，也便于一眼看出是演示数据
MOBILE: dict[str, str] = {
    'admin': '13800001001',
    'zhangsan': '13800001002',
    'lisi': '13800001003',
    'wangwu': '13800001004',
    'liutao': '13800001005',
    'zhaoliu': '13800001006',
    'admin1': '13800001007',
    'admin2': '13800001008',
    'admin3': '13800001009',
    'admin4': '13800001010',
    'admin5': '13800001011',
}

# 部门归属：只覆盖"还没分过部门"和"全都堆在销售部"的情况
DEPARTMENT_OF: dict[str, str] = {
    'admin': '技术部',
    'zhangsan': '销售一部',
    'lisi': '销售一部',
    'wangwu': '销售一部',
    'liutao': '销售二部',
    'zhaoliu': '财务部',
    'admin1': '生产部',
    'admin2': '生产部',
}

# 钉钉绑定：**这是模拟绑定**，userid 不是真实钉钉账号，只用来展示"已绑定"状态
DINGTALK: dict[str, str] = {
    'admin': 'demo_dt_admin',
    'zhangsan': 'demo_dt_zhangsan',
    'lisi': 'demo_dt_lisi',
}


async def main() -> None:
    db_url = os.environ.get('DATABASE_URL', '')
    if 'crm_sales_agent' in db_url:
        raise SystemExit('拒绝对 crm_sales_agent（领导看的库）补数据')
    if not any(key in db_url for key in ('crm_prod', 'demo', 'test')):
        raise SystemExit(f'库名不在白名单里，拒绝执行：{db_url.rsplit("/", 1)[-1]}')

    async with SessionLocal() as session:
        # 1) 部门树
        existing = {
            name: dept_id
            for dept_id, name in (
                await session.execute(select(Department.id, Department.name))
            ).all()
        }
        created_depts = 0
        for name, parent_name in DEPARTMENTS:
            if name in existing:
                continue
            parent_id = existing.get(parent_name) if parent_name else None
            dept = Department(name=name, parent_id=parent_id, status='active')
            session.add(dept)
            await session.flush()
            existing[name] = dept.id
            created_depts += 1

        # 2) 用户资料：手机 / 部门 / 钉钉，一律只补空值
        users = (await session.execute(select(User))).scalars().all()
        filled_mobile = moved_dept = bound_dt = 0
        for user in users:
            if not user.mobile and user.username in MOBILE:
                user.mobile = MOBILE[user.username]
                filled_mobile += 1

            target_dept = DEPARTMENT_OF.get(user.username)
            # 只在"还没分部门"或"仍然堆在销售部"时改；已经分到别处的不动
            if target_dept and user.department_id in (None, existing.get('销售部')):
                user.department_id = existing.get(target_dept)
                moved_dept += 1

            if not user.dingtalk_userid and user.username in DINGTALK:
                user.dingtalk_userid = DINGTALK[user.username]
                bound_dt += 1

        await session.commit()
        print(
            f'部门新建 {created_depts} 个 · 补手机 {filled_mobile} 人 · '
            f'调整部门 {moved_dept} 人 · 模拟绑定钉钉 {bound_dt} 人'
        )


if __name__ == '__main__':
    asyncio.run(main())
