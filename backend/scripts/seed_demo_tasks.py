"""演示任务：给【演示造数】客户造一批任务，让工作台与销售任务页有真实数字。

为什么单独一个脚本：seed_visual_demo.py 有「已有演示客户就不再造」的闸门，
任务属于另一批数据，放在一起会导致想补任务时被那道闸门挡掉。

安全闸：库名含 `crm_sales_agent`（领导看的库）直接拒绝；不含 prod/demo/test 也拒绝。

用法：
    cd backend
    DATABASE_URL=postgresql+asyncpg://crm:***@127.0.0.1:5432/crm_prod \
    PYTHONPATH=. .venv/bin/python scripts/seed_demo_tasks.py

只追加，不删既有数据；重复执行会累加。
"""

import asyncio
import os
import random
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.database import SessionLocal
from app.modules.customer.model import Customer
from app.modules.task.model import Task
from app.modules.user.model import User

MARK = '【演示造数】'
TASK_COUNT = 30


async def main() -> None:
    db_url = os.environ.get('DATABASE_URL', '')
    if 'crm_sales_agent' in db_url:
        raise SystemExit('拒绝对 crm_sales_agent（领导看的库）造数')
    if not any(key in db_url for key in ('crm_prod', 'demo', 'test')):
        raise SystemExit(f'库名不在白名单里，拒绝执行：{db_url.rsplit("/", 1)[-1]}')

    rng = random.Random(20261116)
    now = datetime.now(UTC)

    async with SessionLocal() as session:
        users = (await session.execute(select(User))).scalars().all()
        customers = (
            await session.execute(select(Customer).where(Customer.name.like(f'%{MARK}%')))
        ).scalars().all()
        if not customers or not users:
            raise SystemExit('没有演示客户或用户，先跑 seed_visual_demo.py')

        created = 0
        for index in range(TASK_COUNT):
            customer = rng.choice(customers)
            owner = rng.choice(users)
            # 每三条里有一条逾期：工作台的「逾期」和成员明细的红字才有内容
            is_overdue = index % 3 == 0
            due = (
                now - timedelta(days=rng.randint(1, 12), hours=rng.randint(0, 8))
                if is_overdue
                else now + timedelta(days=rng.randint(1, 18), hours=rng.randint(0, 8))
            )
            session.add(
                Task(
                    title=f'跟进 {customer.short_name} 的报价',
                    customer_id=customer.id,
                    owner_id=owner.id,
                    priority=rng.choice(['high', 'normal', 'normal', 'low']),
                    status=rng.choice(['pending', 'doing']) if is_overdue else 'pending',
                    due_at=due,
                )
            )
            created += 1

        await session.commit()
        print(f'任务 {created} 条已写入（其中 {created // 3} 条已逾期）')


if __name__ == '__main__':
    asyncio.run(main())
