"""演示数据种子：跑通 报价 → 审批 → 转订单 → 应收 → 回款 全链路。

背景：seed.py 只有客户/线索/产品等主数据，订单与回款是 0 条，
财务（wangwu）登录进去没有可看的东西，复购链路也没法演示。

跑法（后端必须先起来，默认 http://127.0.0.1:8000）：
    cd backend
    PYTHONPATH=. .venv/bin/python scripts/seed_demo.py

幂等：按客户名找"演示客户"，如果它名下已经有订单就直接跳过，
可反复执行。数据归属：张三（业务员）名下，回款确认用王五（财务），
审批通过用李四（主管）——正好把四个演示角色都用上。
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get('API_BASE', 'http://127.0.0.1:8000/api/v1')
DEMO_CUSTOMER_NAME = '示例客户·青岛海川机械（演示）'


def call(method, path, token=None, body=None):
    # path 里可能带中文（keyword 搜索），必须按 UTF-8 percent-encode
    path = urllib.parse.quote(path, safe='/?&=')
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
    status, res = call('POST', '/auth/login', body={'username': username, 'password': password})
    if res.get('code') != 0:
        print(f'登录失败：{username} -> {res}')
        sys.exit(1)
    return res['data']['access_token']


def die(step, res):
    print(f'FAILED {step}: {json.dumps(res, ensure_ascii=False)[:400]}')
    sys.exit(1)


def main():
    admin = login('admin', 'admin123')
    zhangsan = login('zhangsan', '123456')
    lisi = login('lisi', '123456')
    wangwu = login('wangwu', '123456')

    # ---- 1. 演示客户（幂等：按名字找）----
    status, res = call('GET', f'/customers?keyword={DEMO_CUSTOMER_NAME}', token=admin)
    if res.get('code') != 0:
        die('查演示客户', res)
    found = [row for row in res['data']['items'] if row['name'] == DEMO_CUSTOMER_NAME]
    if found:
        customer_id = found[0]['id']
        print(f'客户已存在 id={customer_id}')
    else:
        status, res = call(
            'POST',
            '/customers',
            token=zhangsan,
            body={
                'name': DEMO_CUSTOMER_NAME,
                'level': 'B',
                'country': '中国',
                'region': '山东 青岛',
                'source': '展会',
                'remark': 'seed_demo 造的演示客户，可整条删除',
            },
        )
        if res.get('code') != 0:
            die('建演示客户', res)
        customer_id = res['data']['id']
        print(f'客户已创建 id={customer_id}')
        call(
            'POST',
            f'/customers/{customer_id}/contacts',
            token=zhangsan,
            body={'name': '王经理（演示）', 'mobile': '13800000000', 'is_primary': True},
        )

    # ---- 2. 幂等闸门：该客户已有订单就不再造 ----
    status, res = call('GET', f'/orders?customer_id={customer_id}', token=admin)
    if res.get('code') != 0:
        die('查已有订单', res)
    if res['data']['total'] > 0:
        print('该客户名下已有订单，演示数据齐全，跳过。')
        print(f'ORDER_ID={res["data"]["items"][0]["id"]}')
        return

    # ---- 3. 商机 ----
    status, res = call('POST', '/opportunities', token=zhangsan, body={
        'customer_id': customer_id,
        'title': '海川机械年度采购（演示）',
        'expected_amount': 30000,
    })
    if res.get('code') != 0:
        die('建商机', res)
    opportunity_id = res['data']['id']
    print(f'商机已创建 id={opportunity_id}')

    # ---- 4. 报价（张三创建，正常价格不触发低价审批）----
    status, res = call('POST', '/quotes', token=zhangsan, body={
        'customer_id': customer_id,
        'opportunity_id': opportunity_id,
    })
    if res.get('code') != 0:
        die('建报价', res)
    quote_id, version_id = res['data']['quote_id'], res['data']['version_id']

    status, res = call('GET', '/pricing/sku-options', token=admin)
    if res.get('code') != 0:
        die('取 SKU', res)
    if not res['data']:
        print('FAILED 系统里没有 SKU，请先在产品中心补一个带成本的 SKU')
        sys.exit(1)
    sku = res['data'][0]

    status, res = call(
        'POST',
        f'/quote-versions/{version_id}/items/batch',
        token=zhangsan,
        body=[{'sku_id': sku['id'], 'quantity': 20, 'quoted_price': 150}],
    )
    if res.get('code') != 0:
        die('加报价明细', res)
    print(f'报价已创建 quote={quote_id} version={version_id}')

    # ---- 5. 提交审批；需要审批就由李四（主管）通过 ----
    status, res = call('POST', f'/quote-versions/{version_id}/submit-approval', token=zhangsan, body={})
    if res.get('code') != 0:
        die('提交审批', res)
    if res['data'].get('approval_required'):
        status, res = call('GET', '/approvals?status=pending&page_size=50', token=lisi)
        if res.get('code') != 0:
            die('查待审批', res)
        target = next(
            (row for row in res['data']['items'] if row.get('business_id') == version_id),
            None,
        )
        if target is None:
            print('FAILED 找不到刚提交的审批单')
            sys.exit(1)
        status, res = call('POST', f'/approvals/{target["id"]}/approve', token=lisi, body={
            'comment': '演示链路：主管审批通过',
        })
        if res.get('code') != 0:
            die('审批通过', res)
        print(f'审批已由李四通过 approval={target["id"]}')
    else:
        print('报价按规则免审，直接通过')

    # ---- 6. 标记成交 → 转订单 ----
    # 虚构演示链路也按正式流程走：审批通过后登记演示发送，再成交转单。
    status, res = call('POST', f'/quote-versions/{version_id}/mark-sent', token=zhangsan, body={
        'channel': '演示登记', 'receiver': '虚构演示客户',
    })
    if res.get('code') != 0:
        die('登记演示发送', res)
    status, res = call('POST', f'/opportunities/{opportunity_id}/win', token=zhangsan, body={
        'win_quote_version_id': version_id,
    })
    if res.get('code') != 0:
        print(f'  （标记成交跳过：{res.get("message")}）')
    else:
        print('商机已标记成交')

    status, res = call('POST', f'/quote-versions/{version_id}/convert-to-order', token=admin, body={})
    if res.get('code') != 0:
        die('转订单', res)
    order_id = res['data']['order_id']
    print(f'订单已生成 id={order_id}')

    # ---- 7. 应收计划：定金 + 尾款 ----
    plans = []
    for name, amount, due in (('定金（30%）', 900, '2026-10-15'), ('尾款（70%）', 2100, '2026-11-15')):
        status, res = call('POST', '/receivables', token=admin, body={
            'order_id': order_id,
            'plan_name': name,
            'amount': amount,
            'due_date': due,
        })
        if res.get('code') != 0:
            die(f'建应收 {name}', res)
        plans.append(res['data']['id'])
    print(f'应收节点已创建 {plans}')

    # ---- 8. 回款：王五（财务）登记并确认定金全款 + 尾款部分款 ----
    for plan_id, amount, date in ((plans[0], 900, '2026-10-12'), (plans[1], 1200, '2026-11-10')):
        status, res = call('POST', '/payments', token=wangwu, body={
            'receivable_plan_id': plan_id,
            'received_date': date,
            'received_amount': amount,
            'payment_method': '电汇',
        })
        if res.get('code') != 0:
            die('登记回款', res)
        payment_id = res['data']['id']
        status, res = call('POST', f'/payments/{payment_id}/confirm', token=wangwu, body={})
        if res.get('code') != 0:
            die('确认回款', res)
    print('回款已登记并确认：定金收齐、尾款部分回款（正好演示 partial 状态）')

    print()
    print(f'CUSTOMER_ID={customer_id} OPPORTUNITY_ID={opportunity_id} '
          f'QUOTE_ID={quote_id} ORDER_ID={order_id} PLAN_IDS={plans}')


if __name__ == '__main__':
    main()
