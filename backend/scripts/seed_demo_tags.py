"""演示客户标签：造一批常用标签，并给演示客户打上。

为什么单独一个脚本：标签是**受控字典**（客户详情里只能从建好的标签里选），
和客户/商机那批业务数据不是一类东西；而且标签可能被人手工维护过，
所以一律**按名字查重、已存在就复用**，同一对「客户 + 标签」不会重复插入。

安全闸：库名含 `crm_sales_agent`（领导看的库）直接拒绝；不含 prod/demo/test 也拒绝。

用法：
    cd backend
    DATABASE_URL=postgresql+asyncpg://crm:***@127.0.0.1:5432/crm_prod \
    PYTHONPATH=. .venv/bin/python scripts/seed_demo_tags.py
"""

import asyncio
import os
import random

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.database import SessionLocal
from app.modules.customer.model import Customer, Tag, customer_tags

# (名称, 分组, 排序) —— 分组统一用 custom，和界面上「自定义」一致
TAGS: list[tuple[str, str, int]] = [
    ('重点客户', 'custom', 1),
    ('价格敏感', 'custom', 2),
    ('长期合作', 'custom', 3),
    ('新客户', 'custom', 4),
    ('待激活', 'custom', 5),
    ('高风险', 'custom', 6),
]


async def main() -> None:
    db_url = os.environ.get('DATABASE_URL', '')
    if 'crm_sales_agent' in db_url:
        raise SystemExit('拒绝对 crm_sales_agent（领导看的库）造数')
    if not any(key in db_url for key in ('crm_prod', 'demo', 'test')):
        raise SystemExit(f'库名不在白名单里，拒绝执行：{db_url.rsplit("/", 1)[-1]}')

    rng = random.Random(20261116)

    async with SessionLocal() as session:
        existing = {
            name: tag_id
            for tag_id, name in (await session.execute(select(Tag.id, Tag.name))).all()
        }
        created = 0
        for name, kind, sort_no in TAGS:
            if name in existing:
                continue
            tag = Tag(name=name, type=kind, status='active', sort_no=sort_no)
            session.add(tag)
            await session.flush()
            existing[name] = tag.id
            created += 1

        customers = (await session.execute(select(Customer.id))).scalars().all()
        tag_ids = list(existing.values())

        rows = []
        for index, customer_id in enumerate(customers):
            # 每个客户 1~3 个标签；用序号轮转保证每个标签都有客户在用
            count = 1 + index % 3
            picked = {tag_ids[(index + step * 2) % len(tag_ids)] for step in range(count)}
            for tag_id in picked:
                rows.append({'customer_id': customer_id, 'tag_id': tag_id})

        if rows:
            # 同一对「客户 + 标签」已存在就跳过，重复执行不会炸唯一约束
            stmt = pg_insert(customer_tags).values(rows).on_conflict_do_nothing()
            await session.execute(stmt)

        await session.commit()
        print(f'标签新建 {created} 个（共 {len(existing)} 个）· 客户打标 {len(rows)} 条')


if __name__ == '__main__':
    asyncio.run(main())
