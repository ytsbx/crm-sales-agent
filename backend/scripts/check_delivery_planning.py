"""隔离库回归：交期口径、自然日倒排、确认生效、跳过、分批数量。"""
import asyncio
import os
from urllib.parse import urlparse
from scripts.check_schedule_change import call, login, cleanup

async def main():
    assert urlparse(os.environ['API_BASE']).hostname in ('127.0.0.1', 'localhost')
    assert 'test' in os.environ['DATABASE_URL']
    token = login('admin', 'admin123')
    oid = None
    def req(method, path, data=None, status=200):
        code, result = call(method, path, token=token, body=data)
        assert code == status, (method, path, code, result)
        return result.get('data')
    try:
        missing_before = req('GET', '/analytics/delivery')['summary']['no_due_date_open_count']
        sku = req('GET', '/pricing/sku-options')[0]['id']
        oid = req('POST', '/orders', {'customer_id': 1, 'delivery_date': '2026-10-30',
                                    'items': [{'sku_id': sku, 'quantity': 100, 'unit_price': 10}]})['order_id']
        base = f'/orders/{oid}'
        assert req('GET', '/analytics/delivery')['summary']['no_due_date_open_count'] == missing_before + 1
        nodes = req('GET', base + '/milestones')
        assert all(n['planned_date'] is None for n in nodes)
        req('POST', base + '/schedule-changes/preview', {'new_delivery_date': '2026-10-30'}, 422)
        req('POST', base + '/schedule-changes/preview', {'new_delivery_date': '2026-10-30', 'delivery_kind': 'arrival'}, 422)
        config = {'new_delivery_date': '2026-10-30', 'delivery_kind': 'arrival', 'transit_days': 3,
                  'plan_offsets': {'contract': 30, 'deposit': 28, 'pre_sample_sent': 20,
                                   'pre_sample_confirmed': 15, 'first_shipment': 0, 'payment': -15}}
        preview = req('POST', base + '/schedule-changes/preview', config)
        assert preview['new_shipment_date'] == '2026-10-27'
        assert next(n['after'] for n in preview['nodes'] if n['node'] == 'payment') == '2026-11-11'
        assert all(n['planned_date'] is None for n in req('GET', base + '/milestones'))
        change = req('POST', base + '/schedule-changes', config)
        assert req('GET', base)['delivery_kind'] is None
        results = await asyncio.gather(*[asyncio.to_thread(call, 'POST', base + f"/schedule-changes/{change['id']}/confirm", token=token, body={}) for _ in range(2)])
        assert sorted(x[0] for x in results) == [200, 422]
        order = req('GET', base)
        assert order['delivery_date'] == '2026-10-30' and order['shipment_date'] == '2026-10-27'
        nodes = {n['node']: n for n in req('GET', base + '/milestones')}
        contract = base + f"/milestones/{nodes['contract']['id']}"
        req('PATCH', contract, {'skipped': True}, 422)
        skipped = req('PATCH', contract, {'skipped': True, 'skip_reason': '现货订单已使用框架协议'})
        assert skipped['status'] == 'skipped' and skipped['skipped_by']
        req('PATCH', contract, {'actual_date': '2026-10-05'}, 422)
        deposit = base + f"/milestones/{nodes['deposit']['id']}"
        req('PATCH', deposit, {'actual_date': '2026-10-01'})
        req('PATCH', deposit, {'skipped': True, 'skip_reason': '不要'}, 422)
        sample = base + f"/milestones/{nodes['pre_sample_sent']['id']}"
        req('PATCH', sample, {'planned_date': '2026-10-10'})
        item = req('GET', base + '/shipments')['items'][0]['order_item_id']
        batch = req('POST', base + '/shipments', {'planned_date': '2026-10-27',
              'items': [{'order_item_id': item, 'planned_qty': 40}]})['batch_id']
        req('POST', base + '/shipments', {'items': [{'order_item_id': item, 'planned_qty': 61}]}, 400)
        config['transit_days'] = 5
        change2 = req('POST', base + '/schedule-changes', config)
        assert change2['affected']['shift_days'] == -2
        assert all(n['node'] not in ('contract', 'deposit') for n in change2['affected']['nodes'])
        req('POST', base + f"/schedule-changes/{change2['id']}/confirm", {})
        after = {n['node']: n for n in req('GET', base + '/milestones')}
        assert after['contract']['planned_date'] == nodes['contract']['planned_date']
        assert after['deposit']['actual_date'] == '2026-10-01'
        assert after['pre_sample_sent']['planned_date'] == '2026-10-08'
        assert req('GET', base + '/shipments')['batches'][0]['planned_date'] == '2026-10-25'
        # 提醒与统计使用相同的跳过口径；仅在临时库调用站内扫描。
        from app.core.database import SessionLocal
        from app.modules.order.milestones import notify_overdue_milestones
        from app.modules.order.model import OrderMilestone
        async with SessionLocal() as session:
            await notify_overdue_milestones(session)
            await session.commit()
            skipped_node = await session.get(OrderMilestone, nodes['contract']['id'])
            assert skipped_node.overdue_notified_at is None
        analytics = req('GET', '/analytics/delivery')
        assert not any(n['name'] == '签订合同' and n['value'] for n in analytics['overdue_nodes'])
        req('POST', base + f'/shipments/{batch}/ship', {'actual_ship_date': '2026-10-25'})
        ship = req('GET', base + '/shipments')
        assert ship['summary']['remaining'] == 60 and not ship['summary']['all_shipped']
        config['plan_offsets']['pre_sample_sent'] = 10
        change3 = req('POST', base + '/schedule-changes', config)
        assert next(n['after'] for n in change3['affected']['nodes'] if n['node'] == 'pre_sample_sent') == '2026-10-15'
        assert not change3['affected']['batches']  # 实发历史不随改期移动
        req('POST', base + f"/schedule-changes/{change3['id']}/cancel", {'reason': '暂不改'})
        assert (req('PATCH', contract, {'skipped': False}))['status'] != 'skipped'
        history = req('GET', base + '/schedule-changes')
        old = next(c for c in history if c['id'] == change['id'])
        assert old['affected']['planning']['after']['transit_days'] == 3
        assert old['affected']['applied']['new_shipment_date'] == '2026-10-27'
        req('POST', base + '/milestones/replan', {}, 422)
        print('OK 交期类型/运输天数/自然日倒排/确认并发/跳过与恢复/历史与事实保留/批次数量与首批未完成')
    finally:
        if oid:
            from sqlalchemy import text
            from app.core.database import SessionLocal
            async with SessionLocal() as s:
                for stmt in ["delete from followups where order_id=:o",
                             "delete from business_events where business_type='order' and business_id=:o",
                             "delete from audit_logs where business_type in ('order','order_milestone') and business_id=:o"]:
                    await s.execute(text(stmt), {'o': oid})
                await s.commit()
        await cleanup(oid)

if __name__ == '__main__':
    asyncio.run(main())
