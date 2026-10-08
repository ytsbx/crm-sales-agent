"""文档 §2.3/§3.2/场景04、24：过程事实、原单、主管通知、失败重试。

仅允许隔离本机测试库，禁止真实企微/钉钉 HTTP。临时账号全为虚构。
"""
import asyncio
import os
from unittest.mock import patch
from urllib.parse import urlparse
from uuid import uuid4

from sqlalchemy import delete, insert, select
from _test_support import require_isolated_db

require_isolated_db()

from app.core.config import settings
from app.core.database import SessionLocal
from app.modules.customer.model import Customer
from app.modules.followup.model import FollowUp
from app.modules.followup.service import record_and_notify
from app.modules.notification import service as ns
from app.modules.notification.model import BusinessEvent, Notification
from app.modules.order.model import SalesOrder
from app.modules.sample.model import SampleRequest, SampleItem, SampleShipment
from app.modules.user.model import Department, Permission, Role, User, role_permissions, user_roles
from scripts.check_review_regressions import BASE, call, login


async def main():
    import app.main
    from app.core.audit import AuditLog
    from app.modules.wecom import client as wecom_client
    from app.modules.dingtalk import client as ding_client
    _ = app.main
    assert urlparse(BASE).hostname in {'127.0.0.1', 'localhost', '::1'}
    db = urlparse(settings.database_url)
    assert db.hostname in {'127.0.0.1', 'localhost', '::1'}
    assert 'test' in db.path.lower() or os.getenv('CI', '').lower() == 'true'
    assert settings.wecom_push_off and settings.dingtalk_push_off
    assert not settings.scheduler_enabled and not settings.wecom_transfer_enabled
    marker = 'CHKPROC' + uuid4().hex[:8]
    user_ids = []; customer_id = order_id = sample_id = dept_id = None
    failures = []

    def forbidden_http(*args, **kwargs):
        failures.append('真实 HTTP 客户端创建')
        raise AssertionError('本测试不得创建外部 HTTP 客户端')

    def request(method, path, body=None, token=None):
        status, result = call(method, path, body=body, token=token or admin)
        assert status == 200 and result.get('code') == 0, (status, result)
        return result['data']

    async def rows(model, condition):
        async with SessionLocal() as s:
            return list((await s.execute(select(model).where(condition))).scalars())

    async def notices():
        return await rows(Notification, Notification.content.contains(marker))

    # 只禁止外部模块客户端；本机 API 仍由 urllib 调用。
    with patch.object(wecom_client.httpx, 'AsyncClient', forbidden_http), patch.object(ding_client.httpx, 'AsyncClient', forbidden_http):
        try:
            async with SessionLocal() as s:
                users = {u.username: u for u in (await s.execute(select(User))).scalars()}
                sales, manager, actor = users['zhangsan'], users['lisi'], users['admin']
                manager_role = (await s.execute(select(Role).where(Role.code == 'sales_manager'))).scalar_one()
                department = Department(name=marker)
                s.add(department); await s.flush(); dept_id = department.id
                for label, department_id in [('peer', sales.department_id), ('other', dept_id)]:
                    user = User(name=marker+label, username=marker+label, password_hash='unused-test-only',
                                department_id=department_id, status='active')
                    s.add(user); await s.flush(); user_ids.append(user.id)
                    await s.execute(insert(user_roles).values(user_id=user.id, role_id=manager_role.id))
                customer = Customer(name=marker, owner_id=sales.id)
                s.add(customer); await s.flush(); customer_id = customer.id
                order = SalesOrder(order_no=marker, customer_id=customer_id, owner_id=sales.id)
                s.add(order); await s.flush(); order_id = order.id
                await s.commit()
            admin = login('admin', 'admin123')
            zhangsan = login('zhangsan', '123456')
            lisi = login('lisi', '123456')

            # 第一个接收人的通知已 add，第二个失败：savepoint 撤掉全部部分通知。
            real_notify = ns.notify
            count = 0
            async def fail_second(*args, **kwargs):
                nonlocal count
                count += 1
                if count == 2:
                    raise RuntimeError('模拟主管通知写入失败')
                return await real_notify(*args, **kwargs)
            async with SessionLocal() as s:
                with patch.object(ns, 'notify', fail_second):
                    await record_and_notify(s, customer_id=customer_id, owner_id=sales.id,
                                            operator_id=actor.id, title=marker+'测试事实', content=marker,
                                            business_type='order', business_id=order_id, order_id=order_id,
                                            event_key=marker+':retry')
                await s.commit()
            assert not await notices()
            events = await rows(BusinessEvent, BusinessEvent.event_key == marker+':retry')
            assert len(events) == 1 and events[0].notification_processed_at is None
            assert events[0].notification_error
            event_id = events[0].id
            assert len(await rows(FollowUp, FollowUp.customer_id == customer_id)) == 1

            # 两个工作进程同时补齐：锁住同一事件，且只生成每接收人一条。
            async def retry():
                async with SessionLocal() as s:
                    result = await ns.materialize_business_notifications(s, event_id=event_id)
                    await s.commit()
                    return result
            result = await asyncio.gather(retry(), retry())
            assert sum(r['processed'] for r in result) == 1, result
            notifications = await notices()
            assert {n.user_id for n in notifications} == {manager.id, user_ids[0]}
            assert all('操作者：'+actor.name in n.content and '负责人：'+sales.name in n.content for n in notifications)
            assert all(n.business_type == 'order' and n.business_id == order_id for n in notifications)
            async with SessionLocal() as s:
                await record_and_notify(s, customer_id=customer_id, owner_id=sales.id,
                                        operator_id=actor.id, title=marker, content=marker,
                                        order_id=order_id, event_key=marker+':retry')
                await s.commit()
            assert len(await notices()) == 2
            assert len(await rows(FollowUp, FollowUp.customer_id == customer_id)) == 1

            # 生成后转移原单：列表及真正投递前再次核对权限。
            assert len([n for n in request('GET', '/notifications?page_size=100', token=lisi)['items']
                        if marker in (n['content'] or '')]) == 1
            async with SessionLocal() as s:
                order = await s.get(SalesOrder, order_id); order.owner_id = user_ids[1]
                await s.commit()
            assert not [n for n in request('GET', '/notifications?page_size=100', token=lisi)['items']
                        if marker in (n['content'] or '')]
            async with SessionLocal() as s:
                notices_rows = list((await s.execute(select(Notification).where(Notification.content.contains(marker)))).scalars())
                assert not await ns._check_process_recipients(s, notices_rows)
                await s.rollback()
                order = await s.get(SalesOrder, order_id); order.owner_id = sales.id
                await s.commit()

            # 来源无查看权 / 来源超范围：无通知内容、无链接。修改只在测试事务内，最后回滚。
            async with SessionLocal() as s:
                p = (await s.execute(select(Permission.id).where(Permission.code == 'order:view'))).scalar_one()
                await s.execute(delete(role_permissions).where(role_permissions.c.role_id == manager_role.id,
                                                              role_permissions.c.permission_id == p))
                await record_and_notify(s, customer_id=customer_id, owner_id=sales.id, operator_id=actor.id,
                                        title=marker+'无权限', content=marker, order_id=order_id,
                                        event_key=marker+':no-permission')
                assert not (await s.execute(select(Notification.id).where(Notification.title == marker+'无权限'))).all()
                await s.rollback()
                role = await s.get(Role, manager_role.id); role.data_scope = 'self'
                await s.flush()
                await record_and_notify(s, customer_id=customer_id, owner_id=sales.id, operator_id=actor.id,
                                        title=marker+'超范围', content=marker, order_id=order_id,
                                        event_key=marker+':no-scope')
                assert not (await s.execute(select(Notification.id).where(Notification.title == marker+'超范围'))).all()
                await s.rollback()

            # 真实手工跟进与两次修改各通知一次，重复保存同内容不通知。
            created = request('POST', '/followups', {'customer_id': customer_id, 'content': marker+'跟进',
                              'exemption_reason': 'waiting_external'}, token=zhangsan)['followup']
            followup_id = created['id']
            for content in [marker+'改一', marker+'改一', marker+'改二']:
                request('PATCH', f'/followups/{followup_id}', {'content': content}, token=zhangsan)
            manual = [n for n in await notices() if n.title in ('新增客户跟进', '修改客户跟进')]
            assert len(manual) == 6

            # 打样逐步动作留痕、操作者与真正来源；相同反馈/制作/确认重放无新增。
            sku = request('GET', '/pricing/sku-options')[0]['id']
            sample = request('POST', '/samples', {'customer_id': customer_id, 'owner_id': sales.id,
                                               'items': [{'sku_id': sku, 'quantity': 1}]})
            sample_id = sample['id']
            request('POST', f'/samples/{sample_id}/approve', {'approved': True})
            for _ in range(2): request('POST', f'/samples/{sample_id}/made', {})
            request('POST', f'/samples/{sample_id}/ship', {'carrier': '测试物流', 'tracking_no': marker})
            request('POST', f'/samples/{sample_id}/sign', {})
            for _ in range(2): request('POST', f'/samples/{sample_id}/feedback', {'feedback': marker+'尺寸待调整'})
            for decision in [False, False, True]:
                request('POST', f'/samples/{sample_id}/confirm', {'accepted': decision, 'remark': marker})
            sample_rows = await rows(FollowUp, FollowUp.sample_id == sample_id)
            assert len(sample_rows) == 8, [f.content for f in sample_rows]
            assert all(f.owner_id == actor.id for f in sample_rows)
            sample_notices = [n for n in await notices() if n.business_type == 'sample' and n.business_id == sample_id]
            assert len(sample_notices) == 16, len(sample_notices)
            timeline = request('GET', f'/customers/{customer_id}/timeline', token=zhangsan)
            assert len([e for e in timeline if e.get('source') == {'type': 'sample', 'id': sample_id}]) == 8
            assert request('GET', f'/samples/{sample_id}', token=zhangsan)['id'] == sample_id
            for method, body in [('PATCH', {'content': '篡改'}), ('DELETE', None)]:
                status, _ = call(method, f'/followups/{sample_rows[0].id}', token=admin, body=body)
                assert status == 422
            assert not failures
            print('OK 过程通知：同事务、部分失败恢复、并发去重、部门/权限/范围、手工修改、打样全流程')
        finally:
            async with SessionLocal() as s:
                if sample_id:
                    await s.execute(delete(SampleShipment).where(SampleShipment.sample_request_id == sample_id))
                    await s.execute(delete(SampleItem).where(SampleItem.sample_request_id == sample_id))
                    await s.execute(delete(SampleRequest).where(SampleRequest.id == sample_id))
                    await s.execute(delete(AuditLog).where(AuditLog.business_type == 'sample', AuditLog.business_id == sample_id))
                if customer_id:
                    followup_ids = select(FollowUp.id).where(FollowUp.customer_id == customer_id)
                    await s.execute(delete(AuditLog).where(AuditLog.business_type == 'followup', AuditLog.business_id.in_(followup_ids)))
                    await s.execute(delete(FollowUp).where(FollowUp.customer_id == customer_id))
                    await s.execute(delete(BusinessEvent).where(BusinessEvent.customer_id == customer_id))
                    await s.execute(delete(Notification).where(Notification.content.contains(marker)))
                    await s.execute(delete(SalesOrder).where(SalesOrder.id == order_id))
                    await s.execute(delete(Customer).where(Customer.id == customer_id))
                if user_ids:
                    await s.execute(delete(user_roles).where(user_roles.c.user_id.in_(user_ids)))
                    await s.execute(delete(User).where(User.id.in_(user_ids)))
                if dept_id: await s.execute(delete(Department).where(Department.id == dept_id))
                await s.commit()


if __name__ == '__main__':
    asyncio.run(main())
