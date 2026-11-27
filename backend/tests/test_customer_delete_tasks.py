"""软删客户时取消未完成待办，避免任务中心留下点不开的孤儿任务。"""

import pytest
from sqlalchemy.orm import Session

from app.modules.customer.model import Customer
from app.modules.customer.service import delete_customer
from app.modules.task.model import Task
from conftest import SyncSessionAsAsync, make_engine


@pytest.mark.anyio
async def test_delete_customer_cancels_open_tasks_only():
    engine = make_engine()
    Task.__table__.create(engine)
    try:
        with Session(engine) as raw:
            raw.add(Customer(id=1, name="要删的客户", owner_id=1))
            raw.add_all([
                Task(id=1, title="待处理", customer_id=1, owner_id=1, status="pending"),
                Task(id=2, title="处理中", customer_id=1, owner_id=1, status="doing"),
                Task(id=3, title="已完成", customer_id=1, owner_id=1, status="done"),
                Task(id=4, title="别的客户", customer_id=2, owner_id=1, status="pending"),
            ])
            raw.commit()
            customer = raw.get(Customer, 1)
            count = await delete_customer(SyncSessionAsAsync(raw), customer)
            raw.commit()
            assert count == 2
            assert raw.get(Task, 1).status == "cancelled"
            assert raw.get(Task, 2).status == "cancelled"
            assert raw.get(Task, 3).status == "done"
            assert raw.get(Task, 4).status == "pending"
            assert customer.deleted_at is not None
    finally:
        engine.dispose()
