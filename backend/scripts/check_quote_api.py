"""报价模块接口回归测试（03-API §20 §21 §22 §34）。

跑法（后端必须先起来，默认 http://127.0.0.1:8000）：
    cd backend
    $env:PYTHONPATH="."
    .venv\\Scripts\\python.exe scripts\\check_quote_api.py

脚本自己会先清库再验，跑完把数据清回 seed 状态，可反复执行。

覆盖：
  PATCH/DELETE /quotes/{id}
  GET  /quotes/{id}/send-logs、/approval-history
  POST /quotes/{id}/clone
  GET  /quote-versions/{id}/items、/charges
  POST /quote-versions/{id}/items
  PATCH /quote-charges/{id}
  POST /quote-versions/{id}/recalculate、/copy、/expire、/generate-pdf、/send-email
  GET  /quotes/{id}/timeline、/orders/{id}/timeline、/contacts/{id}/timeline

刻意保留的**回归用例**：
  §4 「复制版本」断言"新版本明细条数 == 源版本明细条数"。
  曾经 `POST /quote-versions/{id}/copy` 复制非最新版时，service 先按最新版
  拷一遍、router 又追加目标版一遍，导致明细翻倍；这条断言就是防它回来。
"""

import asyncio
import json
import sys
import time
import urllib.error
import urllib.request

BASE = 'http://127.0.0.1:8000/api/v1'
FAILURES = []


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------
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
            return e.code, {'code': None, 'message': raw[:150]}


def login(username, password):
    return call('POST', '/auth/login', body={'username': username, 'password': password})[1][
        'data'
    ]['access_token']


# --------------------------------------------------------------------------
# 清库：跑前跑后各一次，保证可重复执行
# --------------------------------------------------------------------------
CLEAN_STATEMENTS = [
    ('报价发送记录', 'delete from quote_send_logs'),
    ('报价明细', 'delete from quote_items'),
    ('报价费用', 'delete from quote_charges'),
    ('报价版本', 'delete from quote_versions'),
    ('报价单', 'delete from quotes'),
    ('审批记录', 'delete from approval_records'),
    ('审批实例', 'delete from approval_instances'),
    ('订单明细', 'delete from sales_order_items'),
    ('订单', 'delete from sales_orders'),
    ('通知', 'delete from notifications'),
    ('编号计数器', 'delete from number_sequences'),
    (
        '用例审计',
        "delete from audit_logs where business_type in "
        "('quote','order','approval','numbering_rule')",
    ),
]


async def clean():
    from sqlalchemy import text

    from app.core.database import SessionLocal

    async with SessionLocal() as s:
        for label, sql in CLEAN_STATEMENTS:
            result = await s.execute(text(sql))
            if result.rowcount:
                print(f'  {result.rowcount:>4}  {label}')
        await s.commit()


def main():
    admin = login('admin', 'admin123')
    zhangsan = login('zhangsan', '123456')
    lisi = login('lisi', '123456')
    print('登录成功')

    status, res = call('GET', '/pricing/sku-options', token=admin)
    skus = [row['id'] for row in res['data'][:3]]

    print()
    print('=== 准备：造一张带明细的报价 ===')
    status, res = call('POST', '/quotes', token=admin, body={'customer_id': 1})
    check('建报价', res.get('code'), 0)
    quote_id, version_id = res['data']['quote_id'], res['data']['version_id']

    status, res = call(
        'POST',
        f'/quote-versions/{version_id}/items/batch',
        token=admin,
        body=[{'sku_id': skus[0], 'quantity': 100, 'quoted_price': 100}],
    )
    check('写明细', res.get('code'), 0)

    print()
    print('=== 1. 明细与费用列表 ===')
    status, res = call('GET', f'/quote-versions/{version_id}/items', token=admin)
    check('明细列表', res.get('code'), 0)
    check('1 条明细', len(res['data']), 1)

    status, res = call('GET', f'/quote-versions/{version_id}/charges', token=admin)
    check('费用列表', res.get('code'), 0)
    check('初始无费用', len(res['data']), 0)

    status, res = call(
        'POST',
        f'/quote-versions/{version_id}/items',
        token=admin,
        body={'sku_id': skus[1], 'quantity': 50, 'quoted_price': 80},
    )
    check('追加单条明细', res.get('code'), 0)
    status, res = call('GET', f'/quote-versions/{version_id}/items', token=admin)
    check('追加后共 2 条', len(res['data']), 2)

    print()
    print('=== 2. 附加费用（可新增可修改）===')
    status, res = call(
        'POST',
        f'/quote-versions/{version_id}/charges',
        token=admin,
        body={'charge_type': '运费', 'amount': 500},
    )
    check('加费用', res.get('code'), 0)
    charge_id = res['data']['id']

    status, res = call('PATCH', f'/quote-charges/{charge_id}', token=admin, body={'amount': 800})
    check('改费用金额', res.get('code'), 0)
    check('金额已改', res['data']['amount'], 800.0)

    # 金额不写死：单价由定价模块按阶梯算出，硬编码会随种子数据变化而假失败。
    status, res = call('GET', f'/quote-versions/{version_id}/items', token=admin)
    check('费用后明细仍 2 条', len(res['data']), 2)
    subtotal = sum(item['amount'] for item in res['data'])
    expected_total = subtotal + 800.0
    print(f'  小计 {subtotal} + 费用 800 = {expected_total}')

    status, res = call('GET', f'/quotes/{quote_id}', token=admin)
    check('报价单总额=小计+费用', res['data']['current_version_amount'], expected_total)

    status, res = call('POST', f'/quote-versions/{version_id}/recalculate', token=admin)
    check('重算后总额仍=小计+费用', res['data']['after']['total_amount'], expected_total)

    status, res = call('GET', f'/quote-versions/{version_id}/charges', token=admin)
    check('费用列表带类型标签', res['data'][0]['charge_type'], '运费')

    status, res = call('PATCH', '/quote-charges/999999', token=admin, body={'amount': 1})
    check('不存在的费用', res.get('code'), 40401)

    print()
    print('=== 3. 重算 ===')
    status, res = call('POST', f'/quote-versions/{version_id}/recalculate', token=admin)
    check('重算成功', res.get('code'), 0)
    print('  重算结果：', json.dumps(res['data'], ensure_ascii=False))
    check_true('返回 before/after 对比', {'before', 'after', 'changed'} <= set(res['data']))
    check('合计本来就对，changed=False', res['data']['changed'], False)

    print()
    print('=== 4. 复制版本 ===')
    status, res = call('POST', f'/quotes/{quote_id}/versions', token=admin)
    check('先建一版（用于验证 copy）', res.get('code'), 0)
    v2_id = res['data']['id']

    status, res = call('GET', f'/quote-versions/{v2_id}/items', token=admin)
    v2_item_count = len(res['data'])
    print(f'  V2 复制前有 {v2_item_count} 条明细')
    status, res = call('POST', f'/quote-versions/{v2_id}/copy', token=admin)
    check('复制版本', res.get('code'), 0)
    v3_id = res['data']['version_id']
    check('版本号递增', res['data']['version_no'], 3)
    status, res = call('GET', f'/quote-versions/{v3_id}/items', token=admin)
    # 复制是"整版照抄"，所以条数必须与源版本一致（不写死数字：§1 追加过明细）。
    # 回归点：曾经复制非最新版会翻倍（先拷最新版、再追加目标版）。
    check('新版本明细条数=源版本', len(res['data']), v2_item_count)

    status, res = call('POST', '/quote-versions/999999/copy', token=admin)
    check('不存在的版本', res.get('code'), 40401)

    print()
    print('=== 5. 复制整张报价 ===')
    # 先取源报价当前版本的明细数，再用它核对复制结果 ——
    # 硬编码数字会因为"脚本重跑留下旧数据"而假失败。
    status, res = call('GET', f'/quotes/{quote_id}', token=admin)
    source_version_id = res['data']['current_version_id']
    status, res = call('GET', f'/quote-versions/{source_version_id}/items', token=admin)
    source_item_count = len(res['data'])
    print(f'  源报价当前版本 {source_version_id} 有 {source_item_count} 条明细')

    status, res = call('POST', f'/quotes/{quote_id}/clone', token=admin, body={})
    check('复制报价', res.get('code'), 0)
    check_true('返回单张新报价', isinstance(res['data'], dict), type(res['data']).__name__)
    new_quote_id = res['data']['quote_id']
    new_version_id = res['data']['version_id']
    check('复制的明细数与源版本一致', res['data']['copied_items'], source_item_count)

    status, res = call('POST', f'/quotes/{quote_id}/clone', token=admin)
    check('clone 缺 body 被拒', res.get('code'), 40001)

    status, res = call('GET', f'/quote-versions/{new_version_id}/items', token=admin)
    check('新报价明细确实落库', len(res['data']), source_item_count)

    status, res = call('GET', f'/quotes/{new_quote_id}', token=admin)
    check('新报价可读', res.get('code'), 0)
    check('新报价是草稿', res['data']['status'], 'draft')
    check('新报价版本未提交', res['data']['approval_status'], 'not_submitted')

    status, res = call('POST', '/quotes', token=admin, body={'customer_id': 999999})
    check('客户不存在被拒', res.get('code'), 40401)
    status, res = call(
        'POST', '/quotes', token=admin, body={'customer_id': 1, 'contact_id': 999999}
    )
    check('联系人不存在的被拒', res.get('code'), 40401)
    # 联系人 1 属于客户 1；拿它配客户 2 应该被拒（不能把 A 的联系人挂到 B 的报价）
    status, res = call('GET', '/contacts/1', token=admin)
    contact1_customer = res['data']['customer_id']
    other_customer = 2 if contact1_customer == 1 else 1
    status, res = call(
        'POST', '/quotes', token=admin, body={'customer_id': other_customer, 'contact_id': 1}
    )
    check('联系人-客户不匹配被拒', res.get('code'), 40001)
    status, res = call('POST', '/quotes', token=admin, body={'customer_id': 1})
    check('客户存在可建', res.get('code'), 0)
    empty_quote_id, empty_version_id = res['data']['quote_id'], res['data']['version_id']
    status, res = call('GET', f'/quote-versions/{empty_version_id}/items', token=admin)
    check('没带明细', len(res['data']), 0)

    print()
    print('=== 6. 改报价单 ===')
    status, res = call(
        'PATCH', f'/quotes/{quote_id}', token=admin, body={'valid_until': '2026-12-31'}
    )
    check('改有效期与负责人', res.get('code'), 0)
    check('有效期已改', res['data']['valid_until'], '2026-12-31')

    status, res = call('PATCH', f'/quotes/{quote_id}', token=admin, body={'owner_id': 2})
    check('改负责人', res.get('code'), 0)

    status, res = call('PATCH', f'/quotes/{quote_id}', token=admin, body={'owner_id': 1})
    check('负责人改回管理员', res.get('code'), 0)

    status, res = call('PATCH', f'/quotes/{quote_id}', token=admin, body={'owner_id': 999999})
    check('负责人不存在被拒', res.get('code'), 40401)

    status, res = call(
        'PATCH', '/quotes/999999', token=admin, body={'valid_until': '2026-12-31'}
    )
    check('报价不存在', res.get('code'), 40401)

    print()
    print('=== 7. 发送记录与审批历史（报价单级）===')
    # admin 有 quote:approve，低价报价会直接通过、不生成审批实例（正确行为）。
    # 要造出审批实例必须用没有审批权的角色提交 —— 用李四（sales_manager）。
    status, res = call('POST', '/quotes', token=lisi, body={'customer_id': 1})
    check('李四建报价', res.get('code'), 0)
    lisi_quote_id, lisi_version_id = res['data']['quote_id'], res['data']['version_id']
    call(
        'POST',
        f'/quote-versions/{lisi_version_id}/items/batch',
        token=lisi,
        body=[{'sku_id': skus[0], 'quantity': 100, 'quoted_price': 1}],
    )
    status, res = call(
        'POST', f'/quote-versions/{lisi_version_id}/submit-approval', token=lisi, body={}
    )
    check('提交审批', res.get('code'), 0)
    check_true(
        '低价确实触发审批',
        res['data'].get('approval_required') is True,
        json.dumps(res.get('data'), ensure_ascii=False),
    )

    status, res = call('GET', f'/quotes/{lisi_quote_id}/approval-history', token=lisi)
    check('审批历史', res.get('code'), 0)
    check_true('至少有一版有审批', len(res['data']) >= 1, str(len(res['data'])))
    if res['data']:
        entry = res['data'][0]
        check_true('带版本号', 'version_no' in entry)
        check_true('带审批记录', 'instance' in entry and 'records' in entry['instance'])
        check('审批状态为审批中', entry['instance']['status'], 'pending')

    status, res = call('GET', f'/quotes/{lisi_quote_id}/send-logs', token=lisi)
    check('发送记录（报价级）', res.get('code'), 0)
    check('还没发过', len(res['data']), 0)

    print()
    print('=== 8. 发邮件（未配邮件服务，不能假装成功）===')
    status, res = call(
        'POST',
        f'/quote-versions/{lisi_version_id}/send-email',
        token=lisi,
        body={'channel': '邮件', 'receiver': 'buyer@example.com'},
    )
    check('审批中不能发送', res.get('code'), 42203)

    status, res = call('POST', f'/quote-versions/{lisi_version_id}/withdraw-approval', token=lisi)
    check('撤回审批', res.get('code'), 0)
    call('DELETE', f'/quotes/{lisi_quote_id}', token=lisi)

    status, res = call(
        'POST',
        f'/quote-versions/{version_id}/send-email',
        token=admin,
        body={'channel': '邮件', 'receiver': 'buyer@example.com'},
    )
    check('发邮件（登记记录）', res.get('code'), 0)
    check('明确标 delivered=False', res['data']['delivered'], False)
    check_true(
        '说明未实际投递',
        '未配置' in (res['data'].get('reason') or ''),
        res['data'].get('reason') or '',
    )

    status, res = call(
        'POST', f'/quote-versions/{version_id}/send-email', token=admin, body={'channel': '邮件'}
    )
    check('缺收件人被拒', res.get('code'), 40003)

    status, res = call('GET', f'/quotes/{quote_id}/send-logs', token=admin)
    check_true('发送记录里有一条', len(res['data']) >= 1, str(len(res['data'])))
    check('记录里渠道是邮件', res['data'][0]['channel'], '邮件')

    print()
    print('=== 9. 标记失效 ===')
    status, res = call(
        'POST',
        f'/quote-versions/{version_id}/expire',
        token=admin,
        body={'reason': '客户超期未回复'},
    )
    check('标记失效', res.get('code'), 0)
    check('状态为 expired', res['data']['status'], 'expired')

    status, res = call('POST', f'/quote-versions/{version_id}/expire', token=admin, body={})
    check('重复失效被拒', res.get('code'), 40002)

    print()
    print('=== 10. 删除报价 ===')
    status, res = call('POST', '/quotes', token=admin, body={'customer_id': 1})
    del_quote_id = res['data']['quote_id']
    status, res = call('DELETE', f'/quotes/{del_quote_id}', token=admin)
    check('删除报价', res.get('code'), 0)
    status, res = call('GET', f'/quotes/{del_quote_id}', token=admin)
    check('删除后读不到', res.get('code'), 40401)

    status, res = call('DELETE', f'/quotes/{del_quote_id}', token=admin)
    check('重复删除', res.get('code'), 40401)

    print()
    print('=== 11. PDF（两个路径同一实现）===')
    status, res = call('GET', f'/quote-versions/{v2_id}/pdf', token=admin)
    check('GET pdf', status, 200)
    check_true(
        '返回 PDF', res.get('_ctype', '').startswith('application/pdf'), str(res.get('_ctype'))
    )
    status, res = call('POST', f'/quote-versions/{v2_id}/generate-pdf', token=admin)
    check('POST generate-pdf', status, 200)
    check_true(
        '同样返回 PDF', res.get('_ctype', '').startswith('application/pdf'), str(res.get('_ctype'))
    )

    print()
    print('=== 12. 时间线（三条新路由）===')
    status, res = call('GET', f'/quotes/{quote_id}/timeline', token=admin)
    check('报价时间线', res.get('code'), 0)
    check_true('非空', len(res['data']) >= 1, str(len(res['data'])))

    status, res = call('GET', '/orders/1/timeline', token=admin)
    check_true('订单时间线（0 或 40401 均可）', res.get('code') in (0, 40401), str(res.get('code')))

    status, res = call('GET', '/contacts/1/timeline', token=admin)
    check('联系人时间线', res.get('code'), 0)

    status, res = call('GET', '/quotes/999999/timeline', token=admin)
    check('报价不存在', res.get('code'), 40401)
    status, res = call('GET', '/contacts/999999/timeline', token=admin)
    check('联系人不存在', res.get('code'), 40401)

    print()
    print('=== 13. 权限与数据范围 ===')
    auth_cases = [
        ('PATCH /quotes/{id}', 'PATCH', f'/quotes/{quote_id}', {'valid_until': '2026-12-31'}),
        ('DELETE /quotes/{id}', 'DELETE', f'/quotes/{quote_id}', None),
        ('GET /quotes/{id}/timeline', 'GET', f'/quotes/{quote_id}/timeline', None),
        ('POST recalculate', 'POST', f'/quote-versions/{v2_id}/recalculate', None),
        ('POST expire', 'POST', f'/quote-versions/{v3_id}/expire', {}),
        ('PATCH charge', 'PATCH', f'/quote-charges/{charge_id}', {'amount': 1}),
    ]
    for label, method, path, body in auth_cases:
        status, res = call(method, path, token=zhangsan, body=body)
        check(f'张三 {label} 被拒', res.get('code'), 40302)

    # 空报价留着没意义，清掉（clean() 会兜底，这里顺手）
    call('DELETE', f'/quotes/{empty_quote_id}', token=admin)


if __name__ == '__main__':
    # 全部塞进**同一个** asyncio.run：多次 run 会各自建事件循环，
    # 而 SessionLocal 的连接池绑定第一个循环，第二次 run 就会
    # "AttributeError: 'NoneType' object has no attribute 'send'"。
    async def _driver():
        print('=== 清库（跑前）===')
        await clean()
        print()
        try:
            main()
        finally:
            print()
            print('=== 清库（跑后）===')
            await clean()

    asyncio.run(_driver())
    print()
    if FAILURES:
        print(f'FAILED（{len(FAILURES)}）: {FAILURES}')
        sys.exit(1)
    print('全部通过')
