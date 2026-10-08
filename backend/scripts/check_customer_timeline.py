"""客户业务过程：隔离库验证真实动作、来源授权、幂等、并发和回滚。

仅允许本机 API + 名称含 test 的库（或 CI 独立 PG），全部推送和调度关闭。
"""
import asyncio
from datetime import UTC, date, datetime
import os
import re
from urllib.parse import urlparse
from uuid import uuid4

from sqlalchemy import delete, select
from _test_support import require_isolated_db

require_isolated_db()

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.deps import CurrentUser
from app.modules.customer.model import Customer
from app.modules.followup.model import FollowUp
from app.modules.followup.service import record_progress_event
from app.modules.notification.model import BusinessEvent
from app.modules.order.model import SalesOrder, SalesOrderItem
from app.modules.payment.model import PaymentRecord
from app.modules.timeline.service import build_timeline
from app.modules.user.model import User
from scripts.check_review_regressions import BASE, call, login


async def main():
    import app.main  # register models without starting scheduler
    from app.core.audit import AuditLog
    from app.modules.notification.model import Notification
    from app.modules.order.model import (
        OrderMilestone, OrderScheduleChange, OrderShipmentBatch,
        OrderShipmentBatchItem, OrderStatusHistory,
    )
    from app.modules.payment.model import ReceivablePlan

    loopback = {"127.0.0.1", "localhost", "::1"}
    db_url = urlparse(settings.database_url)
    assert urlparse(BASE).hostname in loopback and db_url.hostname in loopback
    assert "test" in db_url.path.lower() or os.getenv("CI", "").lower() == "true"
    assert settings.dingtalk_push_off and settings.wecom_push_off
    assert not settings.scheduler_enabled and not settings.wecom_transfer_enabled
    _ = app.main
    customer_id = order_id = None
    marker = f"CHKTIME{uuid4().hex[:10]}"
    contact_at = datetime(2026, 1, 1, tzinfo=UTC)
    next_at = datetime(2026, 12, 1, tzinfo=UTC)
    try:
        async with SessionLocal() as s:
            users = {u.username: u for u in (await s.execute(select(User))).scalars()}
            sales = users['zhangsan']
            finance = users['wangwu']
            c = Customer(name=marker, owner_id=sales.id,
                         last_followup_at=contact_at, next_followup_at=next_at)
            s.add(c)
            await s.flush()
            customer_id = c.id
            o = SalesOrder(order_no=marker, customer_id=c.id, owner_id=sales.id,
                           delivery_date=date(2026, 12, 1), total_amount=1000)
            s.add(o)
            await s.flush()
            order_id = o.id
            item = SalesOrderItem(order_id=o.id, quantity=10, unit_price=100,
                                  amount=1000, sku_snapshot='测试定制件')
            s.add(item)
            await s.flush()
            item_id = item.id
            await s.commit()
        admin = login('admin', 'admin123')
        zhangsan = login('zhangsan', '123456')
        wangwu = login('wangwu', '123456')
        lisi = login('lisi', '123456')

        def request(method, path, body=None, token=admin):
            status, result = call(method, path, body=body, token=token)
            assert status == 200 and result.get('code') == 0, (status, result)
            return result['data']

        async def timeline():
            return await asyncio.to_thread(request, 'GET', f'/customers/{customer_id}/timeline', None, zhangsan)

        # 仅排计划/待确认交期/登记与驳回回款不算已经发生的事实。
        batches = [request('POST', f'/orders/{order_id}/shipments', {
            'planned_date': '2026-12-01', 'items': [{'order_item_id': item_id, 'planned_qty': qty}],
        })['batch_id'] for qty in (4, 6)]
        request('GET', f'/orders/{order_id}/milestones')
        change = request('POST', f'/orders/{order_id}/schedule-changes', {
            'delivery_kind': 'shipping', 'new_delivery_date': '2026-12-08', 'reason': marker,
        })['id']
        plan = request('POST', f'/orders/{order_id}/receivables', {
            'plan_name': marker, 'due_date': '2026-12-01', 'amount': 1000,
        })['id']
        payments = [request('POST', '/payments', {
            'receivable_plan_id': plan,
            'received_date': '2026-10-05', 'received_amount': amount,
        })['id'] for amount in (123.45, 10)]
        request('POST', f'/payments/{payments[1]}/reject', {})
        assert not [e for e in await timeline() if e['kind'] == 'followup']
        status, _ = call('POST', f'/payments/{payments[0]}/confirm', token=zhangsan, body={})
        assert status == 403
        assert not [e for e in await timeline() if e['kind'] == 'followup']

        async def race(path, token=admin):
            results = await asyncio.gather(*[
                asyncio.to_thread(call, 'POST', path, token=token, body={}) for _ in range(2)
            ])
            assert sum(status == 200 and result.get('code') == 0 for status, result in results) == 1, results
            assert all(status < 500 for status, _ in results), results

        await race(f'/orders/{order_id}/schedule-changes/{change}/confirm')
        for batch in batches:
            await race(f'/orders/{order_id}/shipments/{batch}/ship')
        await race(f'/payments/{payments[0]}/confirm', wangwu)
        second = request('POST', f'/orders/{order_id}/schedule-changes', {
            'delivery_kind': 'shipping', 'new_delivery_date': '2026-12-15', 'reason': '第二次真实变化',
        })['id']
        request('POST', f'/orders/{order_id}/schedule-changes/{second}/confirm', {})
        events = [e for e in await timeline() if e['kind'] == 'followup']
        assert len(events) == 5, events
        assert all(e['source'] == {'type': 'order', 'id': order_id} for e in events)
        assert [e['at'] for e in events] == sorted([e['at'] for e in events], reverse=True)
        payment = next(e for e in events if '财务确认' in e['detail'])
        assert payment['operator_name'] == finance.name and '123.45' not in payment['detail']
        assert sum('已实发' in e['detail'] for e in events) == 2
        assert any('2026-12-08 → 2026-12-15' in e['detail'] for e in events)
        assert any('2026-12-01 → 2026-12-08' in e['detail'] for e in events)
        assert request('GET', f'/orders/{order_id}', token=zhangsan)
        assert len([e for e in request('GET', f'/customers/{customer_id}/timeline', token=lisi)
                    if e['kind'] == 'followup']) == 5
        async with SessionLocal() as s:
            c = await s.get(Customer, customer_id)
            assert c.last_followup_at == contact_at and c.next_followup_at == next_at
            assert c.last_progress_at and c.last_progress_at > contact_at
            assert (await s.get(PaymentRecord, payments[0])).status == 'confirmed'
            # 同一事件重放不会刷新时钟或多写动态。
            progress = c.last_progress_at
            assert not await record_progress_event(
                s, customer_id=customer_id, operator_id=finance.id, title='重复', content='重复',
                order_id=order_id, event_key=f'payment:confirm:{payments[0]}',
            )
            await s.refresh(c)
            assert c.last_progress_at == progress
            # 模块无权与订单超范围均不返回系统事实或来源 ID。
            viewer = CurrentUser(sales, {'customer:view'}, ['sales'], 'self')
            assert not [e for e in await build_timeline(s, 'customer', customer_id, user=viewer)
                        if e['kind'] == 'followup']
            o = await s.get(SalesOrder, order_id)
            o.owner_id = finance.id
            await s.flush()
            viewer.permissions.add('order:view')
            assert not [e for e in await build_timeline(s, 'customer', customer_id, user=viewer)
                        if e['kind'] == 'followup']
            await s.rollback()
            # 超过一页的不可见新动态不能挤掉较早的可见事实；历史无来源仍显示。
            viewer = CurrentUser(sales, {'customer:view', 'order:view'}, ['sales'], 'self')
            s.add_all([FollowUp(customer_id=customer_id, order_id=-1, followup_type='系统',
                                content='不可见单据', owner_id=sales.id,
                                created_at=datetime.now(UTC)) for _ in range(105)])
            s.add(FollowUp(customer_id=customer_id, followup_type='系统', content='历史无来源',
                          created_at=datetime.now(UTC)))
            await s.flush()
            visible = [e for e in await build_timeline(s, 'customer', customer_id, user=viewer)
                       if e['kind'] == 'followup']
            assert len(visible) == 6 and not any(e['detail'] == '不可见单据' for e in visible)
            assert next(e for e in visible if e['detail'] == '历史无来源')['source'] is None
            await s.rollback()
            # 写入后回滚，不留下动态或事件键。
            await record_progress_event(s, customer_id=customer_id, operator_id=sales.id,
                                        title='回滚', content='回滚', order_id=order_id,
                                        event_key=f'{marker}:rollback')
            await s.rollback()
            assert (await s.execute(select(BusinessEvent.id).where(
                BusinessEvent.event_key == f'{marker}:rollback'))).scalar_one_or_none() is None
            assert len((await s.execute(select(FollowUp).where(
                FollowUp.customer_id == customer_id))).scalars().all()) == 5

            # ---- 时间线上的文案可读性（2026-10-06 修的三处）----
            # 用户报的原话是「创建商机 owner_id: 2」和
            # 「记录时约定：2026-10-21T02:00:26+00:00」——
            # 一条把内部字段名和内部编号亮出来，一条把机器格式的时间原样摆出来。
            s.add(AuditLog(
                business_type='customer', business_id=customer_id,
                action='transfer', operator_id=sales.id,
                after_data={'owner_id': finance.id},
                created_at=datetime.now(UTC),
            ))
            s.add(FollowUp(
                customer_id=customer_id, followup_type='微信', content='文案探针',
                next_action='等回话',
                planned_at=datetime(2026, 10, 21, 10, 0, tzinfo=UTC),
                owner_id=sales.id, created_at=datetime.now(UTC),
            ))
            await s.flush()
            probe = await build_timeline(s, 'customer', customer_id, user=viewer)

            audit_probe = next(e for e in probe if (e['detail'] or '').startswith('负责人：'))
            # 负责人要显示成人名，不能是内部编号（修复前渲染成 `owner_id：2`）
            assert audit_probe['detail'] == f'负责人：{finance.name}', audit_probe['detail']
            # 标题不再是「动作 + 对象」硬拼（修复前是「创建商机」这种，
            # 遇到「添加需求明细」还会拼成「添加需求明细商机」）
            assert audit_probe['title'] == '客户：转移负责人', audit_probe['title']

            follow_probe = next(e for e in probe if (e['detail'] or '').startswith('文案探针'))
            # 时间必须是「2026-10-21 18:00」这种；日期部分随时区走，所以只锁格式。
            # 下面这条才是关键：修复前这里是 `2026-10-21T10:00:00+00:00`。
            assert '记录时约定：2026-10-21T' not in follow_probe['detail'], follow_probe['detail']
            assert re.search(r'记录时约定：\d{4}-\d{2}-\d{2} \d{2}:\d{2}', follow_probe['detail']), \
                follow_probe['detail']

            # 整条时间线上都不该再出现内部字段名
            for event in probe:
                detail = event['detail'] or ''
                for field in ('owner_id', 'quote_version_id', 'loss_reason'):
                    assert field not in detail, (field, detail)
            await s.rollback()
        print('OK 客户时间线：三类事实、两次交期变化、并发重复、操作者、来源授权、时钟与回滚、文案可读性')
    finally:
        if customer_id:
            async with SessionLocal() as s:
                batch_ids = select(OrderShipmentBatch.id).where(OrderShipmentBatch.order_id == order_id)
                await s.execute(delete(OrderShipmentBatchItem).where(OrderShipmentBatchItem.batch_id.in_(batch_ids)))
                for model in (OrderScheduleChange, OrderMilestone, OrderShipmentBatch,
                              OrderStatusHistory, PaymentRecord, ReceivablePlan, SalesOrderItem):
                    await s.execute(delete(model).where(model.order_id == order_id))
                await s.execute(delete(FollowUp).where(FollowUp.customer_id == customer_id))
                await s.execute(delete(BusinessEvent).where(BusinessEvent.customer_id == customer_id))
                await s.execute(delete(Notification).where(Notification.business_type == 'order', Notification.business_id == order_id))
                await s.execute(delete(AuditLog).where(AuditLog.business_type == 'order', AuditLog.business_id == order_id))
                await s.execute(delete(AuditLog).where(AuditLog.business_type == 'payment', AuditLog.business_id.in_(payments if 'payments' in locals() else [])))
                if 'plan' in locals():
                    await s.execute(delete(AuditLog).where(AuditLog.business_type == 'receivable', AuditLog.business_id == plan))
                await s.execute(delete(SalesOrder).where(SalesOrder.id == order_id))
                await s.execute(delete(Customer).where(Customer.id == customer_id))
                await s.commit()


if __name__ == '__main__':
    asyncio.run(main())
