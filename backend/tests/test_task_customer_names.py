"""任务名称补全必须遵循客户查看权限、数据范围及软删除边界。"""
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from conftest import SyncSessionAsAsync, make_engine, make_user
from app.modules.customer.model import Customer
from app.modules.task.model import Task
from app.modules.task.router import list_tasks


@pytest.mark.anyio
@pytest.mark.parametrize('permissions,scope,owner,deleted,expected', [
    (['customer:view'], 'self', 1, False, '名称测试客户'),
    (['customer:view'], 'self', 2, False, None),
    (['customer:view'], 'self', None, False, '名称测试客户'),
    (['customer:view'], 'all', 2, False, '名称测试客户'),
    (['customer:view'], 'all', 1, True, None),
    ([], 'all', 1, False, None),
])
async def test_list_task_customer_name_scope(permissions, scope, owner, deleted, expected):
    engine = make_engine()
    Task.__table__.create(engine)
    try:
        with Session(engine) as raw:
            raw.add(Customer(id=2, name='名称测试客户', owner_id=owner,
                             deleted_at=datetime.now(UTC) if deleted else None))
            raw.add(Task(id=1, title='敲定订单', owner_id=1, customer_id=2))
            raw.commit()
            result = await list_tasks(
                status=None, owner_id=None, mine=False, overdue=False,
                opportunity_id=None, customer_id=None, page=1, page_size=20,
                user=make_user(1, permissions=permissions, data_scope=scope),
                session=SyncSessionAsAsync(raw),
            )
            item = result['data']['items'][0]
            assert item['customer_name'] == expected
            assert item['customer_id'] == 2
    finally:
        engine.dispose()
