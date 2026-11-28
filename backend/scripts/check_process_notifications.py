"""过程事实与手工跟进：只写客户时间线，不再推送主管（2026-11-28 口径）。

仅允许隔离本机测试库，禁止真实企微/钉钉 HTTP。临时账号全为虚构。
"""
import asyncio
import os
from urllib.parse import urlparse
from uuid import uuid4

from sqlalchemy import delete, select
from _test_support import require_isolated_db

require_isolated_db()

from app.core.config import settings
from app.core.database import SessionLocal
from app.modules.customer.model import Customer, CustomerFollower
from app.modules.followup.model import FollowUp
from app.modules.followup.service import record_and_notify
from app.modules.notification.model import BusinessEvent, Notification
from app.modules.order.model import SalesOrder
from app.modules.user.model import User
from scripts.check_review_regressions import BASE, call, login


async def main():
    import app.main

    _ = app.main
    assert urlparse(BASE).hostname in {'127.0.0.1', 'localhost', '::1'}
    db = urlparse(settings.database_url)
    assert db.hostname in {'127.0.0.1', 'localhost', '::1'}
    assert 'test' in db.path.lower() or os.getenv('CI', '').lower() == 'true'
    assert settings.wecom_push_off and settings.dingtalk_push_off
    assert not settings.scheduler_enabled and not settings.wecom_transfer_enabled

    marker = 'CHKPROC' + uuid4().hex[:8]
    customer_id = order_id = followup_id = None
    try:
        async with SessionLocal() as s:
            sales = (await s.execute(select(User).where(User.username == 'zhangsan'))).scalar_one()
            customer = Customer(name=marker, owner_id=sales.id)
            s.add(customer)
            await s.flush()
            customer_id = customer.id
            s.add(CustomerFollower(customer_id=customer_id, user_id=sales.id, source='fixture'))
            order = SalesOrder(order_no=marker, customer_id=customer_id, owner_id=sales.id)
            s.add(order)
            await s.flush()
            order_id = order.id
            await s.commit()

        async def rows(model, condition):
            async with SessionLocal() as s:
                return list((await s.execute(select(model).where(condition))).scalars())

        async def notices():
            return await rows(Notification, Notification.content.contains(marker))

        async def followups():
            return await rows(FollowUp, FollowUp.customer_id == customer_id)

        # 1) 业务动作留痕：写跟进 + 业务事件，不产生主管通知。
        async with SessionLocal() as s:
            await record_and_notify(
                s, customer_id=customer_id, owner_id=sales.id, operator_id=sales.id,
                title=marker + '转单', content=marker, business_type='order',
                business_id=order_id, order_id=order_id, event_key=marker + ':order',
            )
            await s.commit()
        assert len(await followups()) == 1, [f.content for f in await followups()]
        assert len(await rows(BusinessEvent, BusinessEvent.event_key == marker + ':order')) == 1
        assert not await notices()

        # 2) 同一事件重放：不重复留痕、仍无通知。
        async with SessionLocal() as s:
            await record_and_notify(
                s, customer_id=customer_id, owner_id=sales.id, operator_id=sales.id,
                title=marker + '转单', content=marker, business_type='order',
                business_id=order_id, order_id=order_id, event_key=marker + ':order',
            )
            await s.commit()
        assert len(await followups()) == 1
        assert not await notices()

        # 3) 手工跟进新增/修改：留痕，但也不发主管通知。
        admin = login('admin', 'admin123')
        zhangsan = login('zhangsan', '123456')

        def request(method, path, body=None, token=None):
            status, result = call(method, path, body=body, token=token or admin)
            assert status == 200 and result.get('code') == 0, (status, result)
            return result['data']

        created = request(
            'POST', '/followups',
            {'customer_id': customer_id, 'content': marker + '手工跟进',
             'exemption_reason': 'waiting_external'},
            token=zhangsan,
        )['followup']
        followup_id = created['id']
        for content in [marker + '改一', marker + '改一', marker + '改二']:
            request('PATCH', f'/followups/{followup_id}', {'content': content}, token=zhangsan)
        assert len(await followups()) == 2
        assert not await notices()
        print('PASS：业务动作与手工跟进只留时间线，不再推送主管')
    finally:
        async with SessionLocal() as s:
            if customer_id:
                await s.execute(delete(FollowUp).where(FollowUp.customer_id == customer_id))
                await s.execute(delete(SalesOrder).where(SalesOrder.customer_id == customer_id))
                await s.execute(delete(CustomerFollower).where(CustomerFollower.customer_id == customer_id))
                await s.execute(delete(Customer).where(Customer.id == customer_id))
            await s.execute(delete(BusinessEvent).where(BusinessEvent.event_key.like(marker + '%')))
            await s.commit()


if __name__ == '__main__':
    asyncio.run(main())
