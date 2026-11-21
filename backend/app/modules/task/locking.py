"""客户 → 任务的统一锁序，防止客户转交与任务写入成环。"""

from sqlalchemy import select

from app.modules.customer.model import Customer
from app.modules.task.model import Task


async def lock_customers(session, customer_ids):
    for cid in sorted({cid for cid in customer_ids if cid is not None}):
        (await session.execute(select(Customer).where(Customer.id == cid).with_for_update()
                               .execution_options(populate_existing=True))).scalars().all()


async def lock_task_customers(session, task_ids):
    """先读父关联并锁客户；调用方锁任务后必须核对关联未被合并等操作改变。"""
    parents = dict((await session.execute(select(Task.id, Task.customer_id)
                   .where(Task.id.in_(task_ids)))).all())
    await lock_customers(session, parents.values())
    return parents
