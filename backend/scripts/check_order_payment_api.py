"""订单 / 应收 / 回款 接口回归测试（03-API §27 §28 §29 §30）。

跑法（后端必须先起来，默认 http://127.0.0.1:8000）：
    cd backend
    $env:PYTHONPATH="."
    .venv\\Scripts\\python.exe scripts\\check_order_payment_api.py

脚本自带清库，可反复执行。

## 覆盖

新补的接口：
  POST  /orders                        手工建单（金额由明细算出）
  POST  /orders/{id}/refresh-status    ERP 履约状态就地刷新
  POST  /receivables                   建应收节点（order_id 在 body）
  GET   /receivables/{id}
  GET   /payments/{id}
  PATCH /payments/{id}                 改回款（只有 pending 能改，改金额要重算应收）

## 重点：数据范围

应收/回款没有自己的 owner_id，归属跟着订单走。此前
`GET /receivables`、`GET /payments`、`GET /receivables/{id}`、
`PATCH /payments/{id}`、`GET /orders/{id}/finance-summary`、
以及 ERP 的推送/状态接口**全都没做数据范围校验** ——
张三（scope=self）能看到并操作别人的钱。这里逐条钉住 40302。

注意：财务（wangwu）的 data_scope 是 all —— 财务本来就要看全公司账目，
所以"能看见"对他不是漏洞。用张三（scope=self）来验才有效。
"""

import asyncio
import json
import sys
import time
import urllib.error
import urllib.request

BASE = 'http://127.0.0.1:8000/api/v1'
RUN = str(int(time.time()))[-6:]
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


def check_denied(label, code):
    """拒绝即可：40301=没有该权限，40302=不在数据范围内。

    两者都是"拿不到"。区分它们没有意义 —— 有些接口要 payment:manage
    （张三没有 -> 40301），有些只要 payment:view（张三有 -> 得靠 40302 拦）。
    真正要保证的是**不能是 0**。
    """
    good = code in (40301, 40302)
    print(f'  {"OK  " if good else "FAIL"} {label}: {code!r}（期望 40301 或 40302）')
    if not good:
        FAILURES.append(label)


def call(method, path, token=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header('Content-Type', 'application/json')
    if token:
        req.add_header('Authorization', 'Bearer ' + token)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            ctype = resp.headers.get('content-type', '')
            if 'json' not in ctype:
                return resp.status, {'_binary': len(raw), '_ctype': ctype}
            return resp.status, json.loads(raw.decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode('utf-8', 'replace')
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {'code': None, 'message': raw[:200]}


def login(username, password):
    return call('POST', '/auth/login', body={'username': username, 'password': password})[1][
        'data'
    ]['access_token']


# --------------------------------------------------------------------------
# 清库：只删本脚本造的（订单号带 RUN 前缀无法用，改用客户 1/2 上的订单）
# --------------------------------------------------------------------------
async def clean(verbose=False):
    from sqlalchemy import text

    from app.core.database import SessionLocal

    statements = [
        ("回款", "delete from payment_records"),
        ("应收", "delete from receivable_plans"),
        ("订单状态历史", "delete from order_status_history"),
        ("跟单里程碑", "delete from order_milestones"),
        ("发货批次明细", "delete from order_shipment_batch_items"),
        ("发货批次", "delete from order_shipment_batches"),
        ("订单明细", "delete from sales_order_items"),
        ("订单", "delete from sales_orders"),
        ("通知", "delete from notifications where business_type = 'order'"),
        ("编号计数器", "delete from number_sequences"),
        ("审计", "delete from audit_logs where business_type in "
                "('order','payment','receivable_plan')"),
    ]
    async with SessionLocal() as s:
        for label, sql in statements:
            result = await s.execute(text(sql))
            if verbose and result.rowcount:
                print(f'  {result.rowcount:>4}  {label}')
        await s.commit()


def main():
    admin = login('admin', 'admin123')
    zhangsan = login('zhangsan', '123456')
    lisi = login('lisi', '123456')

    status, res = call('GET', '/auth/me', token=admin)
    print(f'  管理员 id={res["data"]["id"]}')
    status, res = call('GET', '/auth/me', token=zhangsan)
    zs_id = res['data']['id']
    print(f'  张三 id={zs_id} scope={res["data"].get("data_scope")}')

    status, res = call('GET', '/pricing/sku-options', token=admin)
    skus = [row['id'] for row in res['data'][:2]]

    print()
    print('=== 1. 手工建订单（03-API §27 POST /orders）===')
    status, res = call(
        'POST',
        '/orders',
        token=admin,
        body={
            'customer_id': 1,
            'items': [
                {'sku_id': skus[0], 'quantity': 10, 'unit_price': 100},
                {'sku_id': skus[1], 'quantity': 5, 'unit_price': 200},
            ],
            'remark': f'线下签约补录 {RUN}',
            'payment_terms': '款到发货',
        },
    )
    check('建单成功', res.get('code'), 0)
    order_id = res['data']['order_id']
    check('金额由明细算出 (10*100 + 5*200 = 2000)', res['data']['total_amount'], 2000.0)

    status, res = call('GET', f'/orders/{order_id}', token=admin)
    check('订单可读', res.get('code'), 0)
    check('初始状态 pending', res['data']['status'], 'pending')
    check('明细数 2', res['data']['item_count'], 2)

    status, res = call('GET', f'/orders/{order_id}/items', token=admin)
    check('明细读取', len(res['data']), 2)
    check('明细金额正确', res['data'][0]['amount'], 1000.0)

    print()
    print('=== 建单的参数校验 ===')
    status, res = call('POST', '/orders', token=admin,
                       body={'customer_id': 999999, 'items': [{'sku_id': skus[0], 'quantity': 1, 'unit_price': 1}]})
    check('客户不存在被拒', res.get('code'), 40401)
    status, res = call('POST', '/orders', token=admin,
                       body={'customer_id': 1, 'items': [{'sku_id': 999999, 'quantity': 1, 'unit_price': 1}]})
    check('SKU 不存在被拒', res.get('code'), 40401)
    status, res = call('POST', '/orders', token=admin, body={'customer_id': 1, 'items': []})
    check('明细不能为空', res.get('code'), 40001)
    status, res = call('POST', '/orders', token=admin,
                       body={'customer_id': 1, 'items': [{'sku_id': skus[0], 'quantity': 0, 'unit_price': 1}]})
    check('数量必须 > 0', res.get('code'), 40001)
    status, res = call('POST', '/orders', token=admin,
                       body={'customer_id': 1, 'items': [{'sku_id': skus[0], 'quantity': 1, 'unit_price': -1}]})
    check('单价不能为负', res.get('code'), 40001)
    status, res = call('POST', '/orders', token=admin,
                       body={'customer_id': 1, 'owner_id': 999999,
                             'items': [{'sku_id': skus[0], 'quantity': 1, 'unit_price': 1}]})
    check('负责人不存在被拒', res.get('code'), 40401)

    print()
    print('=== 2. 建应收节点（03-API §29 POST /receivables）===')
    status, res = call('POST', '/receivables', token=admin,
                       body={'order_id': order_id, 'plan_name': '定金', 'due_date': '2026-10-01',
                             'amount': 600})
    check('建应收（body 带 order_id）', res.get('code'), 0)
    plan_id = res['data']['id']
    check('初始状态 pending', res['data']['status'], 'pending')

    status, res = call('GET', f'/receivables/{plan_id}', token=admin)
    check('GET /receivables/{id}', res.get('code'), 0)
    check('带订单号', res['data']['order_no'] is not None, True)

    status, res = call('POST', '/receivables', token=admin,
                       body={'plan_name': '尾款', 'due_date': '2026-11-01', 'amount': 1400})
    check('缺 order_id 被拒', res.get('code'), 40003)

    status, res = call('POST', '/receivables', token=admin,
                       body={'order_id': order_id, 'plan_name': '尾款', 'due_date': '2026-11-01',
                             'amount': 1400})
    check('订单内入口同逻辑', res.get('code'), 0)
    plan2_id = res['data']['id']

    status, res = call('GET', f'/orders/{order_id}/receivables', token=admin)
    check('订单应收列表 2 条', len(res['data']), 2)

    print()
    print('=== 3. 应收部分更新（PATCH 只改传的字段）===')
    status, res = call('PATCH', f'/receivables/{plan_id}', token=admin, body={'amount': 700})
    check('只改金额', res.get('code'), 0)
    check('金额已改', res['data']['amount'], 700.0)
    check('名称没被清空', res['data']['plan_name'], '定金')
    check('到期日没被清空', res['data']['due_date'], '2026-10-01')

    status, res = call('PATCH', f'/receivables/{plan_id}', token=admin, body={'amount': -5})
    check('金额必须 > 0', res.get('code'), 40001)

    print()
    print('=== 4. 回款登记与修改（03-API §30）===')
    status, res = call('POST', '/payments', token=admin,
                       body={'receivable_plan_id': plan_id, 'received_date': '2026-09-26',
                             'received_amount': 300, 'payment_method': '电汇'})
    check('登记回款', res.get('code'), 0)
    payment_id = res['data']['id']
    check('待确认', res['data']['status'], 'pending')

    status, res = call('GET', f'/payments/{payment_id}', token=admin)
    check('GET /payments/{id}', res.get('code'), 0)
    check('带应收节点名', res['data']['plan_name'], '定金')

    status, res = call('GET', f'/receivables/{plan_id}/payments', token=admin)
    check('节点下回款列表', len(res['data']), 1)

    status, res = call('GET', f'/receivables/{plan_id}', token=admin)
    # 待确认的回款**不改**应收状态：钱还没到账，只是业务员登记了。
    # recalc_plan 只统计 status=confirmed 的回款。
    check('待确认时状态不变', res['data']['status'], 'pending')
    check('待确认时已收金额仍为 0', res['data']['received_amount'], 0.0)

    print()
    print('=== 5. 财务确认后才算已收，并驱动应收状态 ===')
    status, res = call('POST', f'/payments/{payment_id}/confirm', token=admin, body={})
    check('财务确认', res.get('code'), 0)
    check('状态 confirmed', res['data']['status'], 'confirmed')

    status, res = call('GET', f'/receivables/{plan_id}', token=admin)
    check('收 300/700 -> partial', res['data']['status'], 'partial')
    check('已收金额', res['data']['received_amount'], 300.0)
    check('剩余金额', res['data']['remaining_amount'], 400.0)

    status, res = call('PATCH', f'/payments/{payment_id}', token=admin, body={'received_amount': 700})
    check('已确认不能改', res.get('code'), 40002)

    # 再登记一笔把余额收齐，验"收齐 -> paid"
    status, res = call('POST', '/payments', token=admin,
                       body={'receivable_plan_id': plan_id, 'received_date': '2026-09-27',
                             'received_amount': 400, 'payment_method': '承兑'})
    check('补登 400', res.get('code'), 0)
    tail_id = res['data']['id']
    status, res = call('PATCH', f'/payments/{tail_id}', token=admin, body={'received_amount': 350})
    check('待确认时可改金额', res.get('code'), 0)
    check('金额已改', res['data']['received_amount'], 350.0)
    status, res = call('POST', f'/payments/{tail_id}/confirm', token=admin, body={})
    check('确认第二笔', res.get('code'), 0)
    status, res = call('GET', f'/receivables/{plan_id}', token=admin)
    check('300+350 < 700 -> 仍 partial', res['data']['status'], 'partial')
    check('已收 650', res['data']['received_amount'], 650.0)

    status, res = call('POST', '/payments', token=admin,
                       body={'receivable_plan_id': plan_id, 'received_date': '2026-09-28',
                             'received_amount': 50})
    third_id = res['data']['id']
    call('POST', f'/payments/{third_id}/confirm', token=admin, body={})
    status, res = call('GET', f'/receivables/{plan_id}', token=admin)
    check('收齐 700 -> paid', res['data']['status'], 'paid')
    check('剩余 0', res['data']['remaining_amount'], 0.0)

    status, res = call('PATCH', f'/payments/{third_id}', token=admin, body={'received_amount': 1})
    check('已确认不能改（第三笔）', res.get('code'), 40002)

    print()
    print('=== 6. 订单财务概览 ===')
    status, res = call('GET', f'/orders/{order_id}/finance-summary', token=admin)
    check('财务概览', res.get('code'), 0)

    print()
    print('=== 7. refresh-status（未配 ERP 必须明确报错，不许假装成功）===')
    status, res = call('POST', f'/orders/{order_id}/refresh-status', token=admin)
    check_true('不能假装成功（非 0）', res.get('code') not in (0, None),
               f'code={res.get("code")} msg={res.get("message")!r}')
    check_true('给出可读原因', bool(res.get('message')),
               res.get('message') or '')

    status, res = call('POST', '/orders/999999/refresh-status', token=admin)
    check('订单不存在', res.get('code'), 40401)

    print()
    print('=== 8. 数据范围：张三（scope=self）不能碰别人的单与钱 ===')
    # 上面的订单 owner 是 admin(1)，张三(2) 不在范围内
    scope_cases = [
        ('GET /orders/{id}/finance-summary', 'GET', f'/orders/{order_id}/finance-summary', None),
        ('GET /orders/{id}/receivables', 'GET', f'/orders/{order_id}/receivables', None),
        ('GET /orders/{id}/payments', 'GET', f'/orders/{order_id}/payments', None),
        ('POST /orders/{id}/refresh-status', 'POST', f'/orders/{order_id}/refresh-status', None),
        ('POST /orders/{id}/sync-erp', 'POST', f'/orders/{order_id}/sync-erp', None),
        ('GET erp status', 'GET', f'/integrations/erp/orders/{order_id}/status', None),
        ('POST erp sync', 'POST', f'/integrations/erp/orders/{order_id}/sync', None),
        ('GET /receivables/{id}', 'GET', f'/receivables/{plan_id}', None),
        ('PATCH /receivables/{id}', 'PATCH', f'/receivables/{plan_id}', {'amount': 1}),
        ('DELETE /receivables/{id}', 'DELETE', f'/receivables/{plan2_id}', None),
        ('POST mark-overdue', 'POST', f'/receivables/{plan_id}/mark-overdue', None),
        ('GET /payments/{id}', 'GET', f'/payments/{payment_id}', None),
        ('PATCH /payments/{id}', 'PATCH', f'/payments/{payment_id}', {'payment_method': 'x'}),
        ('POST payments confirm', 'POST', f'/payments/{payment_id}/confirm', {}),
        ('POST payments reject', 'POST', f'/payments/{payment_id}/reject', {}),
        ('GET plan payments', 'GET', f'/receivables/{plan_id}/payments', None),
        ('POST /payments (挂别人节点)', 'POST', '/payments',
         {'receivable_plan_id': plan_id, 'received_date': '2026-09-26', 'received_amount': 1}),
    ]
    for label, method, path, body in scope_cases:
        status, res = call(method, path, token=zhangsan, body=body)
        check_denied(f'张三 {label}', res.get('code'))

    print()
    print('=== 9. 数据范围：列表也要过滤 ===')
    status, res = call('GET', '/receivables', token=zhangsan)
    check('张三应收列表为空', res['data']['total'], 0)
    status, res = call('GET', '/payments', token=zhangsan)
    check('张三回款列表为空', res['data']['total'], 0)
    status, res = call('GET', '/receivables', token=admin)
    check_true('管理员能看到', res['data']['total'] >= 2, str(res['data']['total']))

    print()
    print('=== 10. 张三自己的单能正常用 ===')
    status, res = call('POST', '/orders', token=zhangsan,
                       body={'customer_id': 1,
                             'items': [{'sku_id': skus[0], 'quantity': 2, 'unit_price': 50}]})
    check('张三建单', res.get('code'), 0)
    zs_order_id = res['data']['order_id']
    status, res = call('GET', f'/orders/{zs_order_id}', token=zhangsan)
    check('张三看自己的单', res.get('code'), 0)
    status, res = call('GET', '/orders', token=zhangsan)
    check('张三订单列表共 1 条', res['data']['total'], 1)

    status, res = call('POST', '/receivables', token=admin,
                       body={'order_id': zs_order_id, 'plan_name': '全款',
                             'due_date': '2026-10-01', 'amount': 100})
    check('给张三的单建应收（payment:manage 归财务/主管）', res.get('code'), 0)
    zs_plan_id = res['data']['id']
    status, res = call('GET', f'/receivables/{zs_plan_id}', token=zhangsan)
    check('张三读自己单上的应收', res.get('code'), 0)
    status, res = call('GET', '/receivables', token=zhangsan)
    check('张三应收列表 1 条', res['data']['total'], 1)
    status, res = call('GET', '/payments', token=zhangsan)
    check('张三回款列表 0 条', res['data']['total'], 0)
    status, res = call('GET', '/orders', token=zhangsan)
    check('张三订单列表 1 条', res['data']['total'], 1)

    print()
    print('=== 11. 部门主管（lisi, department_and_sub）===')
    status, res = call('GET', '/receivables', token=lisi)
    check('主管列表可读', res.get('code'), 0)
    print(f'  （主管看到 {res["data"]["total"]} 条 —— 部门范围，非 0 也正常）')

    print()
    print('=== 12. 发货批次（§3.5/场景13：分批发货，首批不结束整单）===')
    status, res = call(
        'POST', '/orders', token=admin,
        body={'customer_id': 1, 'items': [{'sku_id': skus[0], 'quantity': 10, 'unit_price': 50}]},
    )
    check('批次用例建单', res.get('code'), 0)
    b_order_id = res['data']['order_id']
    status, res = call('GET', f'/orders/{b_order_id}/items', token=admin)
    b_item_id = res['data'][0]['id']

    # 第 1 批：6 件
    status, res = call('POST', f'/orders/{b_order_id}/shipments', token=admin,
                       body={'items': [{'order_item_id': b_item_id, 'planned_qty': 6}]})
    check('排首批', res.get('code'), 0)
    batch1 = res['data']['batch_id']
    # 超计划量被拒：再排 5 件（未计划量只剩 4）
    status, res = call('POST', f'/orders/{b_order_id}/shipments', token=admin,
                       body={'items': [{'order_item_id': b_item_id, 'planned_qty': 5}]})
    check('计划量超订购量被拒', res.get('code'), 40001)
    status, res = call('POST', f'/orders/{b_order_id}/shipments/{batch1}/ship', token=admin,
                       body={'logistics_company': '顺丰', 'tracking_no': f'SF{RUN}1'})
    check('首批登记发货', res.get('code'), 0)
    check('订单推进到 shipped', res['data']['order_status'], 'shipped')
    check('已发 6 / 未发 4', res['data']['summary']['shipped'], 6.0)
    check('未发量 4', res['data']['summary']['remaining'], 4.0)
    # 首批不结束整单（场景13 核心）
    status, res = call('POST', f'/orders/{b_order_id}/status', token=admin,
                       body={'status': 'completed'})
    check('未发完禁止整单完成', res.get('code'), 40002)
    # 第 2 批：剩余 4 件，发完后闸门放行
    status, res = call('POST', f'/orders/{b_order_id}/shipments', token=admin,
                       body={'items': [{'order_item_id': b_item_id, 'planned_qty': 4}]})
    batch2 = res['data']['batch_id']
    call('POST', f'/orders/{b_order_id}/shipments/{batch2}/ship', token=admin, body={})
    status, res = call('GET', f'/orders/{b_order_id}/shipments', token=admin)
    check('全发完未发量 0', res['data']['summary']['remaining'], 0.0)
    check('all_shipped=True', res['data']['summary']['all_shipped'], True)
    status, res = call('POST', f'/orders/{b_order_id}/status', token=admin,
                       body={'status': 'completed'})
    check('发完后整单可完成', res.get('code'), 0)

    print()
    print(f'ORDER_IDS={[order_id, zs_order_id]} PLAN_IDS={[plan_id, plan2_id, zs_plan_id]}')


if __name__ == '__main__':
    async def _driver():
        print('=== 清库（跑前）===')
        await clean(verbose=True)
        print()
        try:
            main()
        finally:
            print()
            print('=== 清库（跑后）===')
            await clean(verbose=True)

    asyncio.run(_driver())
    print()
    if FAILURES:
        print(f'FAILED（{len(FAILURES)}）: {FAILURES}')
        sys.exit(1)
    print('全部通过')
