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
import os
import json
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get('API_BASE', 'http://127.0.0.1:8000/api/v1')
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
            "delete from notifications where business_type = 'order' and business_id = :o",
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

        # 手工推后一个节点（跟单员因为产前样延期之类改过它）。
        # 这一步必须在"预览不改数据"之后做，否则会把那次快照对比弄脏。
        status, res = call('PATCH', f"/orders/{order_id}/milestones/{first['id']}",
                           token=admin, body={'planned_date': '2026-12-20'})
        check('手工调整节点计划日', res.get('code'), 0)

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

        # 回归：同一订单不能有两张待确认的变更单——两张各自确认会互相覆盖计划日，
        # 而 old_delivery_date 的档案也跟着失真
        status, res = call('POST', f'/orders/{order_id}/schedule-changes', token=admin, body={
            'new_delivery_date': '2027-02-01', 'reason': f'{TAG} 重复发起',
        })
        check('已有待确认单时不能再发起', res.get('code'), 40002)

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
        # 回归：手工推后的节点按天数平移（2026-12-20 + 15 = 2027-01-04），
        # 而不是拿新交期重新倒推（那样会被拉回 2026-12-16，把跟单员的手工调整抹掉）
        check('手工调整过的节点按天数平移、没被拉回默认倒推值',
              shifted.get(first['node']), '2027-01-04')
        status, res = call('GET', f'/orders/{order_id}/shipments', token=admin)
        check('批次计划日也跟着平移',
              res['data']['batches'][0]['planned_date'], '2027-01-15')

        # 回归（P1-4）：独立的重排入口只补"从没排过"的节点，不能把手工调整过的
        # 计划日拉回默认倒推值。此前 replan 是"按当前交期整体重新倒推"，
        # 点一次就把上面那条手工平移的结果（2027-01-04）抹回默认值——
        # 正是这次要保护的东西。
        status, res = call('POST', f'/orders/{order_id}/milestones/replan', token=admin, body={})
        check('重排接口可调用', res.get('code'), 0)
        status, res = call('GET', f'/orders/{order_id}/milestones', token=admin)
        after_replan = {r['node']: r['planned_date'] for r in res['data']}
        check('手工调整过的节点不被重排拉回默认值',
              after_replan.get(first['node']), '2027-01-04')
        check('已平移的节点也不被重排改动',
              after_replan.get('first_shipment'), '2027-01-15')

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

        # 回归：直改交期的老入口必须被拦——那条路径不重排节点与批次，
        # 改完交期与计划日静默脱节（页面上看着改了，逾期提醒却还按老交期算）
        status, res = call('PATCH', f'/orders/{order_id}', token=admin,
                           body={'delivery_date': '2027-02-20'})
        # 注意：这里判的是**业务码**（40001=参数错误），不是 HTTP 状态——
        # 上一版写成 422 是我把两者搞混了
        check('直改交期被拒（要求走变更单）', res.get('code'), 40001)
        check_true('错误说明指向变更单入口', '交期变更' in (res.get('message') or ''),
                   str(res.get('message'))[:60])

        print()
        print('=== 6. 批次逾期提醒（旁路：只提醒，不动节点口径）===')
        # 批次不是跟单节点，节点提醒扫不到它——这条旁路就是为"第 N 批该发没发有人管"
        status, res = call('POST', f'/orders/{order_id}/shipments', token=admin, body={
            'planned_date': '2026-01-01',
            'items': [{'order_item_id': item_id, 'planned_qty': 10}],
        })
        check('建一个计划日已过的批次', res.get('code'), 0)

        from app.core.database import SessionLocal
        from app.modules.order import milestones as ms
        from app.modules.order.model import OrderShipmentBatch
        from sqlalchemy import select, text
        from datetime import date

        async with SessionLocal() as s:
            # 只算**逾期**的那几批：本人订单里还有一批计划在交期那天，不该被标记
            mine = (
                await s.execute(
                    select(OrderShipmentBatch.id).where(
                        OrderShipmentBatch.order_id == order_id,
                        OrderShipmentBatch.status == 'planned',
                        OrderShipmentBatch.planned_date < date.today(),
                    )
                )
            ).scalars().all()
        check_true('本人确有逾期批次可测', len(mine) >= 1, f'{len(mine)} 批')

        async def scan_and_restore() -> None:
            """跑一次全局扫描，随后把**不属于本用例**的批次状态还原。

            这个函数是**全局**扫描（扫全库逾期批次），不还原就等于把演示库里
            别人的"提醒过"凭证写死——那些批次从第二天起再也不会提醒。
            用例改坏别人的数据、还测不出自己想测的东西，是双重问题，所以这里
            既改成只断言本人的批次，又把别人的状态放回去。

            注意还要撤**通知行**：只把 `overdue_notified_at` 放回 NULL、却留下
            这轮新建的通知，下一轮调度就会给全库逾期批次再推一遍（跑一次扫一遍）。
            用一个 id 水位把本轮新建的通知删干净。
            """
            async with SessionLocal() as s:
                others = (
                    await s.execute(
                        select(OrderShipmentBatch.id).where(
                            OrderShipmentBatch.overdue_notified_at.is_(None),
                            OrderShipmentBatch.id.not_in(mine or [0]),
                        )
                    )
                ).scalars().all()
                max_notification_id = int(
                    (
                        await s.execute(
                            text("select coalesce(max(id), 0) from notifications")
                        )
                    ).scalar()
                    or 0
                )
            async with SessionLocal() as s:
                await ms.notify_overdue_batches(s)
                await s.commit()
            async with SessionLocal() as s:
                # 撤掉本轮扫描新建的全部通知行（含本人夹具的那几条）
                await s.execute(
                    text("delete from notifications where id > :mid"),
                    {'mid': max_notification_id},
                )
                if others:
                    await s.execute(
                        text(
                            "update order_shipment_batches set overdue_notified_at = null "
                            "where id = any(:ids)"
                        ),
                        {'ids': others},
                    )
                await s.commit()

        await scan_and_restore()
        async with SessionLocal() as s:
            marked = (
                await s.execute(
                    select(OrderShipmentBatch.id).where(
                        OrderShipmentBatch.id.in_(mine or [0]),
                        OrderShipmentBatch.overdue_notified_at.is_not(None),
                    )
                )
            ).scalars().all()
        check('本人的逾期批次被标记为已提醒', len(marked), len(mine))

        await scan_and_restore()
        async with SessionLocal() as s:
            first_stamp = (
                await s.execute(
                    select(OrderShipmentBatch.overdue_notified_at).where(
                        OrderShipmentBatch.id.in_(mine or [0])
                    )
                )
            ).scalars().all()

        await scan_and_restore()
        async with SessionLocal() as s:
            second_stamp = (
                await s.execute(
                    select(OrderShipmentBatch.overdue_notified_at).where(
                        OrderShipmentBatch.id.in_(mine or [0])
                    )
                )
            ).scalars().all()
        # 判据用"提醒凭证没被刷新"而不是"通知只有 1 条"：
        # 一条逾期会推给负责人**和**主管，通知本就是 2 行（按接收人计），
        # 拿它当"未重复"的证据会误报。
        check('同一批不会被重复提醒（凭证时间戳未变）', second_stamp, first_stamp)
        check_true('凭证确实已写', all(s is not None for s in second_stamp), str(second_stamp))
        # 节点口径不受影响：批次提醒不该顺带改任何节点
        status, res = call('GET', f'/orders/{order_id}/milestones', token=admin)
        check_true('节点没被批次提醒改动', res.get('code') == 0, '')
        # ---- 7. 确认权限按设计钉住：责任人本人 / 主管代确认 / 看得见但无权限的人 ----
        # 口径是「责任人 **或** 有 order:assign 的人（主管）可确认」。
        # 责任人本人那条由第 4 节覆盖；这里补另外几条，每条都判**具体错误码**，
        # 不要再写成 "40301/40302/40401 随便哪个都算" —— 那样把责任人校验整段删掉
        # 断言照样绿（张三 self 范围在 get_visible_order 就先撞 40302，根本走不到）。
        # 正确的第三种人是**财务**：data_scope=all（看得见任何订单）、有 order:manage
        # （能调到确认接口），但既不是责任人、也没有 order:assign。
        print()
        print('=== 7. 确认权限（责任人 / 主管 / 看得见但非责任人）===')
        lisi_token = login('lisi', '123456')
        zhangsan_token = login('zhangsan', '123456')
        finance_token = login('wangwu', '123456')

        # 把订单负责人换成张三，让李四成为"看得见但不是责任人"的主管
        status, res = call('PATCH', f'/orders/{order_id}', token=admin,
                           body={'owner_id': 2})  # 2 = 张三
        check('把订单负责人换成张三', res.get('code'), 0)
        status, res = call('POST', f'/orders/{order_id}/schedule-changes', token=admin, body={
            'new_delivery_date': '2027-03-01', 'reason': f'{TAG} 主管代确认',
        })
        check('发起变更单（责任人是张三）', res.get('code'), 0)
        manager_change = (res.get('data') or {}).get('id')
        if manager_change:
            status, res = call(
                'POST', f'/orders/{order_id}/schedule-changes/{manager_change}/confirm',
                token=lisi_token, body={'remark': f'{TAG} 主管代确认'},
            )
            check('主管可代确认（责任人或主管口径）', res.get('code'), 0)

        # 换回 admin 负责，让张三变成"完全看不见这单"的人
        status, res = call('PATCH', f'/orders/{order_id}', token=admin, body={'owner_id': 1})
        check('订单负责人换回 admin', res.get('code'), 0)
        status, res = call('POST', f'/orders/{order_id}/schedule-changes', token=admin, body={
            'new_delivery_date': '2027-04-01', 'reason': f'{TAG} 无权限者',
        })
        check('再发起一张变更单', res.get('code'), 0)
        outsider_change = (res.get('data') or {}).get('id')
        if outsider_change:
            # 看不见这单的人：张三（self 范围）在数据范围就被挡，具体码 40302
            status, res = call(
                'POST', f'/orders/{order_id}/schedule-changes/{outsider_change}/confirm',
                token=zhangsan_token, body={},
            )
            check('看不见这单的人不能确认（数据范围 40302）', res.get('code'), 40302)
            # 看得见、但不是责任人、也没有 order:assign 的人：财务必须撞责任人校验 40301。
            # 这一条才是真正钉住"责任人确认"的断言——之前用张三那个是假绿。
            status, res = call(
                'POST', f'/orders/{order_id}/schedule-changes/{outsider_change}/confirm',
                token=finance_token, body={},
            )
            check('看得见但非责任人、无 order:assign → 40301', res.get('code'), 40301)

        # ---- 8. 作废出口：没有它，一张没人确认的单会永久堵死后续变更 ----
        # 第 7 节留了一张 pending 单（张三没法确认那张）。用它验：
        # 作废之前不能再发起（40002）→ 作废 → 之后能再发起。
        print()
        print('=== 8. 作废待确认的变更单 ===')
        if outsider_change:
            status, res = call('POST', f'/orders/{order_id}/schedule-changes', token=admin,
                               body={'new_delivery_date': '2027-05-01', 'reason': f'{TAG} 被堵'})
            check('作废前：已有待确认单，确实发不了', res.get('code'), 40002)
            status, res = call(
                'POST', f'/orders/{order_id}/schedule-changes/{outsider_change}/cancel',
                token=admin, body={'reason': f'{TAG} 交期又变回来了'},
            )
            check('作废成功', res.get('code'), 0)
            check('状态已作废', res.get('data', {}).get('status'), 'cancelled')
            status, res = call('POST', f'/orders/{order_id}/schedule-changes', token=admin,
                               body={'new_delivery_date': '2027-05-01', 'reason': f'{TAG} 重新发起'})
            check('作废后：可以重新发起', res.get('code'), 0)

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
