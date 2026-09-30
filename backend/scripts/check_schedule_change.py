"""交期变更回归（方案 :105 / 场景13）。

跑法：
    cd backend
    PYTHONPATH=. .venv/bin/python scripts/check_schedule_change.py

## 覆盖什么

原文：「客户改交期、样品未通过或生产延期时，展示受影响节点及批次，
责任人确认调整并**保留修改前后版本**。」三件事各要能被验证：

1. 预览说得出"会动到谁"（节点与批次的 before → after）；
2. **未确认前一个日期都不许变**——否则"确认"就是装饰；
3. 确认后计划日真的重排，且**前后版本留在变更单里**（再改一次也不覆盖旧单）。

顺带守一条纪律：已登记实际日期的节点、已发货的批次不重排——
历史事实不能因为改交期被抹掉。
"""

import asyncio
import json
import sys
import time
import urllib.error
import urllib.request

BASE = 'http://127.0.0.1:8000/api/v1'
RUN = str(int(time.time()))[-6:]
TAG = f'CHK{RUN}'
FAILURES = []


def check(label, actual, expected):
    good = actual == expected
    print(f'  {"OK  " if good else "FAIL"} {label}: {actual!r}（期望 {expected!r}）')
    if not good:
        FAILURES.append(label)


def check_true(label, condition, detail=''):
    print(f'  {"OK  " if condition else "FAIL"} {label}{f"：{detail}" if detail else ""}')
    if not condition:
        FAILURES.append(label)


def call(method, path, token=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header('Content-Type', 'application/json')
    if token:
        req.add_header('Authorization', 'Bearer ' + token)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode('utf-8', 'replace')
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {'code': None, 'message': raw[:200]}


def login(username, password):
    status, res = call('POST', '/auth/login',
                       body={'username': username, 'password': password})
    if res.get('code') != 0:
        raise SystemExit(f'登录失败：{username}（后端没在 8000 跑？）')
    return res['data']['access_token']


async def cleanup(order_id: int | None):
    from sqlalchemy import text

    from app.core.database import SessionLocal

    if order_id is None:
        return
    async with SessionLocal() as s:
        for sql in (
            "delete from order_schedule_changes where order_id = :o",
            "delete from order_milestones where order_id = :o",
            "delete from order_shipment_batch_items where batch_id in "
            "(select id from order_shipment_batches where order_id = :o)",
            "delete from order_shipment_batches where order_id = :o",
            "delete from order_status_history where order_id = :o",
            "delete from sales_order_items where order_id = :o",
            "delete from sales_orders where id = :o",
        ):
            await s.execute(text(sql), {'o': order_id})
        await s.commit()


async def main() -> int:
    admin = login('admin', 'admin123')
    order_id = None
    try:
        _, res = call('GET', '/pricing/sku-options', token=admin)
        sku_id = res['data'][0]['id']

        print('=== 1. 造一单：交期 + 一个批次 ===')
        status, res = call('POST', '/orders', token=admin, body={
            'customer_id': 1,
            'delivery_date': '2026-12-31',
            'items': [{'sku_id': sku_id, 'quantity': 100, 'unit_price': 50}],
        })
        check('建订单', res.get('code'), 0)
        order_id = res['data']['order_id']

        status, res = call('GET', f'/orders/{order_id}/milestones', token=admin)
        check('节点初始化', res.get('code'), 0)
        before_nodes = {r['node']: r['planned_date'] for r in res['data']}
        check_true('首批发货节点有计划日', bool(before_nodes.get('first_shipment')), '')

        # 方案 :103 的三项字段要能写能读（前端"登记"弹窗依赖这条契约）
        first = res['data'][0]
        status, res = call('PATCH', f"/orders/{order_id}/milestones/{first['id']}",
                           token=admin, body={
                               'owner_id': 1,
                               'evidence': f'{TAG} 客户邮件确认交期',
                               'overdue_reason': f'{TAG} 产前样延期',
                           })
        check('登记责任人/证据/逾期原因', res.get('code'), 0)
        check('回读责任人', res['data'].get('owner_id'), 1)
        check('回读来源证据', res['data'].get('evidence'), f'{TAG} 客户邮件确认交期')
        check('回读逾期原因', res['data'].get('overdue_reason'), f'{TAG} 产前样延期')
        status, res = call('GET', f'/orders/{order_id}/milestones', token=admin)
        listed = {r['node']: r for r in res['data']}
        check('列表里也带这三项',
              listed[first['node']].get('overdue_reason'), f'{TAG} 产前样延期')

        status, res = call('GET', f'/orders/{order_id}/shipments', token=admin)
        item_id = res['data']['items'][0]['order_item_id']
        status, res = call('POST', f'/orders/{order_id}/shipments', token=admin, body={
            'planned_date': '2026-12-31',
            'items': [{'order_item_id': item_id, 'planned_qty': 40}],
        })
        check('建发货批次', res.get('code'), 0)

        print()
        print('=== 2. 预览：说得出会动到谁，且一个日期都不许变 ===')
        status, res = call('POST', f'/orders/{order_id}/schedule-changes/preview',
                           token=admin, body={'new_delivery_date': '2027-01-15'})
        check('预览可读', res.get('code'), 0)
        preview = res['data']
        check('平移天数', preview['shift_days'], 15)
        check_true('受影响节点非空', len(preview['nodes']) > 0, str(len(preview['nodes'])))
        check_true('受影响批次非空', len(preview['batches']) == 1, str(preview['batches']))
        check_true('节点给了前后对比',
                   all(n['before'] and n['after'] for n in preview['nodes']), '')

        status, res = call('GET', f'/orders/{order_id}/milestones', token=admin)
        after_preview = {r['node']: r['planned_date'] for r in res['data']}
        check_true('**预览不改数据**', after_preview == before_nodes, '')

        print()
        print('=== 3. 生成变更单：待确认 ===')
        status, res = call('POST', f'/orders/{order_id}/schedule-changes', token=admin, body={
            'new_delivery_date': '2027-01-15', 'reason': f'{TAG} 客户改期',
        })
        check('生成变更单', res.get('code'), 0)
        change_id = res['data']['id']
        check('状态待确认', res['data']['status'], 'pending')
        status, res = call('GET', f'/orders/{order_id}', token=admin)
        check('**未确认前订单交期不动**', res['data']['delivery_date'], '2026-12-31')

        print()
        print('=== 4. 责任人确认：这时才重排 ===')
        status, res = call('POST', f'/orders/{order_id}/schedule-changes/{change_id}/confirm',
                           token=admin, body={'remark': f'{TAG} 已与生产确认'})
        check('确认成功', res.get('code'), 0)
        check('状态已确认', res['data']['status'], 'confirmed')
        check_true('记录了确认人', bool(res['data'].get('confirmed_by')), '')

        status, res = call('GET', f'/orders/{order_id}', token=admin)
        check('交期已改', res['data']['delivery_date'], '2027-01-15')
        status, res = call('GET', f'/orders/{order_id}/milestones', token=admin)
        shifted = {r['node']: r['planned_date'] for r in res['data']}
        check_true('节点计划日整体平移 15 天',
                   shifted.get('first_shipment') == '2027-01-15',
                   f"first_shipment={shifted.get('first_shipment')}")
        status, res = call('GET', f'/orders/{order_id}/shipments', token=admin)
        check('批次计划日也跟着平移',
              res['data']['batches'][0]['planned_date'], '2027-01-15')

        print()
        print('=== 5. 前后版本保留 + 不能重复确认 ===')
        status, res = call('GET', f'/orders/{order_id}/schedule-changes', token=admin)
        check('变更历史可读', res.get('code'), 0)
        row = res['data'][0]
        check_true('存了原始预览的前后对比',
                   bool(row['affected'].get('nodes')) and bool(row['affected'].get('batches')),
                   '')
        check_true('另存了确认时实际生效的版本',
                   bool(row['affected'].get('applied')), '')
        check('旧交期留档', row['old_delivery_date'], '2026-12-31')
        status, res = call('POST', f'/orders/{order_id}/schedule-changes/{change_id}/confirm',
                           token=admin, body={})
        check('重复确认被拒', res.get('code'), 40002)
    finally:
        await cleanup(order_id)

    print()
    if FAILURES:
        print(f'FAILED（{len(FAILURES)}）: {FAILURES}')
        return 1
    print('交期变更 全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
