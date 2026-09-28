"""客户子资源 / 联系人子资源 接口回归测试（03-API §7 §8，PRD §5.4）。

跑法（后端必须先起来，默认 http://127.0.0.1:8000）：
    cd backend
    $env:PYTHONPATH="."
    .venv\\Scripts\\python.exe scripts\\check_customer_contact_api.py

脚本自带清库，可反复执行。

## 新增接口

  GET  /customers/{id}/opportunities
  GET  /customers/{id}/quotes
  GET  /customers/{id}/orders
  POST /customers/{id}/assign         主管分配（与 transfer 同一实现，不同权限点）
  POST /customers/export              按筛选条件导出 CSV
  GET  /contacts/{id}/followups
  GET  /contacts/{id}/wecom           企微外部联系人绑定关系
  POST /contacts/deduplicate          联系人查重（PRD §5.4 转化第 2 步）

## 重点：数据范围

客户子资源不是"客户可见就能看"—— 商机/报价/订单各自还有自己的数据范围。
测试里张三看自己客户的商机列表必须为空（商机 owner 是别人）。
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
CREATED_CUSTOMER_IDS: list[int] = []
CREATED_CONTACT_IDS: list[int] = []


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
    good = code in (40301, 40302)
    print(f'  {"OK  " if good else "FAIL"} {label}: {code!r}（期望 40301/40302）')
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
                return resp.status, {
                    '_binary': len(raw),
                    '_ctype': ctype,
                    '_text': raw.decode('utf-8-sig', 'replace'),
                }
            return resp.status, json.loads(raw.decode())
    except urllib.error.HTTPError as e:
        raw = e.read()
        text = raw.decode('utf-8', 'replace')
        try:
            return e.code, json.loads(text)
        except json.JSONDecodeError:
            return e.code, {'code': None, 'message': text[:200]}


def call_csv(method, path, token=None, body=None):
    """导出接口返回 CSV，需要拿到响应头里的 Content-Disposition。"""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header('Content-Type', 'application/json')
    if token:
        req.add_header('Authorization', 'Bearer ' + token)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return resp.status, {
                'ctype': resp.headers.get('content-type', ''),
                'disposition': resp.headers.get('content-disposition', ''),
                'text': raw.decode('utf-8-sig', 'replace'),
            }
    except urllib.error.HTTPError as e:
        text = e.read().decode('utf-8', 'replace')
        try:
            return e.code, json.loads(text)
        except json.JSONDecodeError:
            return e.code, {'code': None, 'message': text[:200]}


def login(username, password):
    return call('POST', '/auth/login', body={'username': username, 'password': password})[1][
        'data'
    ]['access_token']


async def clean(verbose=False):
    from sqlalchemy import text

    from app.core.database import SessionLocal

    statements = [
        # 只删本脚本造的数据（名字带 RUN 后缀）。顺序要服从外键：
        # 先删引用了客户的表，最后才删客户本身。
        ('用例跟进', f"delete from followups where content like '%CHK{RUN}%'"),
        ('用例订单回款', "delete from payment_records where order_id in "
                      "(select id from sales_orders where customer_id in "
                      f"(select id from customers where name like 'CHK{RUN}%'))"),
        ('用例订单应收', "delete from receivable_plans where order_id in "
                      "(select id from sales_orders where customer_id in "
                      f"(select id from customers where name like 'CHK{RUN}%'))"),
        ('用例订单历史', "delete from order_status_history where order_id in "
                      "(select id from sales_orders where customer_id in "
                      f"(select id from customers where name like 'CHK{RUN}%'))"),
        ('用例订单明细', "delete from sales_order_items where order_id in "
                      "(select id from sales_orders where customer_id in "
                      f"(select id from customers where name like 'CHK{RUN}%'))"),
        ('用例订单', f"delete from sales_orders where customer_id in "
                   f"(select id from customers where name like 'CHK{RUN}%')"),
        ('用例报价明细', "delete from quote_items where quote_version_id in "
                      "(select id from quote_versions where quote_id in "
                      "(select id from quotes where customer_id in "
                      f"(select id from customers where name like 'CHK{RUN}%')))"),
        ('用例报价版本', "delete from quote_versions where quote_id in "
                      "(select id from quotes where customer_id in "
                      f"(select id from customers where name like 'CHK{RUN}%'))"),
        ('用例报价', f"delete from quotes where customer_id in "
                   f"(select id from customers where name like 'CHK{RUN}%')"),
        ('用例商机明细', "delete from opportunity_items where opportunity_id in "
                      "(select id from opportunities where customer_id in "
                      f"(select id from customers where name like 'CHK{RUN}%'))"),
        ('用例商机阶段历史', "delete from opportunity_stage_history where opportunity_id in "
                          "(select id from opportunities where customer_id in "
                          f"(select id from customers where name like 'CHK{RUN}%'))"),
        ('用例商机', f"delete from opportunities where customer_id in "
                   f"(select id from customers where name like 'CHK{RUN}%')"),
        ('用例联系人', f"delete from contacts where name like 'CHK{RUN}%'"),
        ('用例合并日志', "delete from customer_merge_logs where target_customer_id in "
                      f"(select id from customers where name like 'CHK{RUN}%') or "
                      "source_customer_id in "
                      f"(select id from customers where name like 'CHK{RUN}%')"),
        ('用例客户归属历史', "delete from customer_owner_history where customer_id in "
                          f"(select id from customers where name like 'CHK{RUN}%')"),
        ('用例客户标签', "delete from customer_tags where customer_id in "
                      f"(select id from customers where name like 'CHK{RUN}%')"),
        ('用例客户', f"delete from customers where name like 'CHK{RUN}%'"),
        ('用例编号计数器', "delete from number_sequences"),
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

    status, res = call('GET', '/auth/me', token=admin)
    admin_id = res['data']['id']
    status, res = call('GET', '/auth/me', token=zhangsan)
    zs_id = res['data']['id']

    print()
    print('=== 准备：造一个客户 + 两个联系人 + 一条跟进 ===')
    status, res = call('POST', '/customers', token=admin, body={
        'name': f'CHK{RUN}测试客户',
        'region': '浙江',
        'source': '展会',
    })
    check('建客户', res.get('code'), 0)
    customer_id = res['data']['id']
    CREATED_CUSTOMER_IDS.append(customer_id)
    # 回归：不指定负责人时，客户必须归创建人自己。
    # 曾经 `setdefault("owner_id", user.id)` 因为 model_dump 带了 owner_id=None
    # 而失效，导致**任何人新建的客户都直接掉进公海**（谁都能看、谁都能领）。
    check('默认负责人是创建人自己', res['data']['owner_id'], admin_id)
    check('不是公海', res['data']['pool_status'], 'private')

    status, res = call('POST', '/customers', token=zhangsan, body={
        'name': f'CHK{RUN}张三客户',
        'region': '江苏',
    })
    check('张三建客户', res.get('code'), 0)
    zs_customer_id = res['data']['id']
    CREATED_CUSTOMER_IDS.append(zs_customer_id)

    status, res = call('POST', f'/customers/{customer_id}/contacts', token=admin, body={
        'name': f'CHK{RUN}王经理',
        'mobile': f'139{RUN}01',
        'email': f'wang{RUN}@example.com',
    })
    check('建联系人 1', res.get('code'), 0)
    contact1_id = res['data']['id']
    CREATED_CONTACT_IDS.append(contact1_id)

    status, res = call('POST', f'/customers/{customer_id}/contacts', token=admin, body={
        'name': f'CHK{RUN}李助理',
        'mobile': f'139{RUN}02',
    })
    check('建联系人 2', res.get('code'), 0)
    contact2_id = res['data']['id']
    CREATED_CONTACT_IDS.append(contact2_id)

    status, res = call('POST', '/followups', token=admin, body={
        'customer_id': customer_id,
        'contact_id': contact1_id,
        'content': f'CHK{RUN}电话沟通了报价细节',
        'followup_type': '电话',
    })
    check('建带联系人的跟进', res.get('code'), 0)

    print()
    print('=== 1. GET /customers/{id}/opportunities ===')
    status, res = call('GET', f'/customers/{customer_id}/opportunities', token=admin)
    check('客户商机列表可读', res.get('code'), 0)
    check('新客户初始无商机', res['data']['total'], 0)

    # 给这个客户建一条商机（owner 是 admin）
    stages = call('GET', '/opportunity-stages', token=admin)[1]['data']
    status, res = call('POST', '/opportunities', token=admin, body={
        'customer_id': customer_id,
        'title': f'CHK{RUN}商机',
        'expected_amount': 10000,
        'stage_id': stages[0]['id'],
    })
    check('建商机', res.get('code'), 0)
    opp_id = res['data']['id']

    status, res = call('GET', f'/customers/{customer_id}/opportunities', token=admin)
    check('商机列表 1 条', res['data']['total'], 1)
    check('返回商机标题', res['data']['items'][0]['title'], f'CHK{RUN}商机')
    check_true('带阶段名', res['data']['items'][0].get('stage_name') is not None)

    status, res = call('GET', f'/customers/{customer_id}/opportunities?status=open', token=admin)
    check('status 过滤可用', res.get('code'), 0)

    print()
    print('=== 2. GET /customers/{id}/quotes ===')
    status, res = call('GET', f'/customers/{customer_id}/quotes', token=admin)
    check('客户报价列表可读', res.get('code'), 0)
    check('初始无报价', res['data']['total'], 0)

    # D8：报价必须挂商机——先建一条快捷商机承载用例报价
    status, res = call('POST', '/opportunities', token=admin, body={
        'customer_id': customer_id, 'title': f'CHK{RUN}快捷商机',
    })
    check('建快捷商机', res.get('code'), 0)
    cc_opp_id = res['data']['id']
    status, res = call('POST', '/quotes', token=admin, body={'opportunity_id': cc_opp_id})
    check('建报价', res.get('code'), 0)
    quote_id = res['data']['quote_id']

    status, res = call('GET', f'/customers/{customer_id}/quotes', token=admin)
    check('报价列表 1 条', res['data']['total'], 1)
    check('返回报价号', res['data']['items'][0]['id'], quote_id)

    print()
    print('=== 3. GET /customers/{id}/orders ===')
    status, res = call('GET', f'/customers/{customer_id}/orders', token=admin)
    check('客户订单列表可读', res.get('code'), 0)
    check('初始无订单', res['data']['total'], 0)

    skus = [r['id'] for r in call('GET', '/pricing/sku-options', token=admin)[1]['data'][:1]]
    status, res = call('POST', '/orders', token=admin, body={
        'customer_id': customer_id,
        'items': [{'sku_id': skus[0], 'quantity': 3, 'unit_price': 50}],
    })
    check('建订单', res.get('code'), 0)
    order_id = res['data']['order_id']

    status, res = call('GET', f'/customers/{customer_id}/orders', token=admin)
    check('订单列表 1 条', res['data']['total'], 1)
    check('返回订单号', res['data']['items'][0]['id'], order_id)
    check('带客户名', res['data']['items'][0]['customer_name'], f'CHK{RUN}测试客户')
    check_true('带明细数', res['data']['items'][0]['item_count'] == 1)

    print()
    print('=== 4. GET /contacts/{id}/followups ===')
    status, res = call('GET', f'/contacts/{contact1_id}/followups', token=admin)
    check(f'联系人{contact1_id}跟进列表', res.get('code'), 0)
    check(f'联系人{contact1_id}共 1 条跟进', res['data']['total'], 1)
    check('内容是那条', f'CHK{RUN}' in res['data']['items'][0]['content'], True)

    status, res = call('GET', f'/contacts/{contact2_id}/followups', token=admin)
    check(f'联系人{contact2_id}无跟进', res['data']['total'], 0)

    status, res = call('GET', '/contacts/999999/followups', token=admin)
    check('联系人不存在', res.get('code'), 40401)

    print()
    print('=== 5. GET /contacts/{id}/wecom（未绑定是正常状态，不是 404）===')
    status, res = call('GET', f'/contacts/{contact1_id}/wecom', token=admin)
    check('可读', res.get('code'), 0)
    check('bound=False', res['data']['bound'], False)
    check('回显 contact_id', res['data']['contact_id'], contact1_id)
    check('externals 为空数组', res['data']['externals'], [])

    status, res = call('GET', '/contacts/999999/wecom', token=admin)
    check('联系人不存在', res.get('code'), 40401)

    print()
    print('=== 6. POST /contacts/deduplicate（PRD §5.4）===')
    status, res = call('POST', '/contacts/deduplicate', token=admin,
                       body={'mobile': f'139{RUN}01'})
    check('按手机号查重', res.get('code'), 0)
    check_true('命中 1 条', res['data']['count'] >= 1, str(res['data']['count']))
    if res['data']['matches']:
        top = res['data']['matches'][0]
        check('命中的就是那个联系人', top['id'], contact1_id)
        check_true('给出原因', '手机号一致' in top['reasons'], str(top['reasons']))

    status, res = call('POST', '/contacts/deduplicate', token=admin,
                       body={'name': f'CHK{RUN}王经理'})
    check('按姓名查重', res.get('code'), 0)
    check_true('姓名能命中', res['data']['count'] >= 1, str(res['data']['count']))

    status, res = call('POST', '/contacts/deduplicate', token=admin, body={})
    check('什么都不传被拒', res.get('code'), 40003)

    # 编辑场景：传自己的 id 应该把自己排除掉
    status, res = call('POST', '/contacts/deduplicate', token=admin,
                       body={'contact_id': contact1_id})
    check('传 contact_id 查重', res.get('code'), 0)
    check_true('不把自己算成重复', all(
        m['id'] != contact1_id for m in res['data']['matches']
    ), str([m['id'] for m in res['data']['matches']]))

    print()
    print('=== 7. POST /customers/{id}/assign ===')
    status, res = call('POST', f'/customers/{customer_id}/assign', token=admin,
                       body={'owner_id': zs_id, 'reason': f'CHK{RUN} 分配测试'})
    check('分配给张三', res.get('code'), 0)
    check('负责人已变', res['data']['owner_id'], zs_id)

    status, res = call('POST', f'/customers/{customer_id}/assign', token=admin,
                       body={'owner_id': 999999})
    check('负责人不存在被拒', res.get('code'), 40401)

    # 分回来，别影响后面的数据范围用例
    status, res = call('POST', f'/customers/{customer_id}/assign', token=admin,
                       body={'owner_id': admin_id})
    check('分配回管理员', res.get('code'), 0)

    print()
    print('=== 8. POST /customers/export（筛选导出 CSV）===')
    status, res = call_csv('POST', '/customers/export', token=admin, body={})
    check('导出成功', status, 200)
    check_true('是 CSV', 'text/csv' in res.get('ctype', ''), str(res.get('ctype')))
    check_true('带文件名', 'customers.csv' in res.get('disposition', ''),
               str(res.get('disposition')))
    check_true('有表头', '客户名称' in res.get('text', ''), res.get('text', '')[:60])

    status, res = call_csv('POST', '/customers/export', token=admin,
                           body={'keyword': f'CHK{RUN}测试客户'})
    check('按关键字筛选导出', status, 200)
    body_text = res.get('text', '')
    check_true('只含目标客户', f'CHK{RUN}测试客户' in body_text, body_text[:100])
    check_true('不含另一个客户', f'CHK{RUN}张三客户' not in body_text, '关键字筛选生效')

    status, res = call_csv('POST', '/customers/export', token=admin,
                           body={'level': '不存在的等级'})
    check('筛不到也是 200', status, 200)

    print()
    print('=== 9. 数据范围：张三看不见别人的客户子资源 ===')
    # 张三那个客户的商机/报价/订单都是 admin 的
    # 显式把商机负责人设成 admin：这条用例要验证的是"客户可见 ≠ 名下商机可见"，
    # 所以必须让商机的负责人**确定在张三范围外**。
    # （不指定 owner_id 时商机继承客户负责人，修好客户归属后那就是张三自己，
    #   测不出数据范围。）
    status, res = call('POST', '/opportunities', token=admin, body={
        'customer_id': zs_customer_id,
        'title': f'CHK{RUN}张三客户商机',
        'expected_amount': 5000,
        'stage_id': stages[0]['id'],
        'owner_id': admin_id,
    })
    check('给张三客户建商机（owner=admin）', res.get('code'), 0)
    check('商机负责人确实是 admin', res['data']['owner_id'], admin_id)

    status, res = call('GET', f'/customers/{zs_customer_id}/opportunities', token=zhangsan)
    check('张三看自己客户的商机', res.get('code'), 0)
    check('客户可见不等于名下商机可见', res['data']['total'], 0)

    status, res = call('GET', f'/customers/{zs_customer_id}/opportunities', token=admin)
    check('管理员能看到', res['data']['total'], 1)

    # 张三看别人的客户 -> 40302
    for path, label in [
        (f'/customers/{customer_id}/opportunities', '商机列表'),
        (f'/customers/{customer_id}/quotes', '报价列表'),
        (f'/customers/{customer_id}/orders', '订单列表'),
    ]:
        status, res = call('GET', path, token=zhangsan)
        check_denied(f'张三读别人客户的{label}', res.get('code'))

    status, res = call('GET', f'/contacts/{contact1_id}/followups', token=zhangsan)
    check_denied('张三读别人客户的联系人跟进', res.get('code'))
    status, res = call('GET', f'/contacts/{contact1_id}/wecom', token=zhangsan)
    check_denied('张三读别人客户的联系人企微', res.get('code'))

    # 联系人查重也只在自己范围内
    status, res = call('POST', '/contacts/deduplicate', token=zhangsan,
                       body={'mobile': f'139{RUN}01'})
    check('张三查重可执行', res.get('code'), 0)
    check('查不到别人客户下的联系人', res['data']['count'], 0)

    # 张三没有 customer:assign，分配接口应被拒
    status, res = call('POST', f'/customers/{zs_customer_id}/assign', token=zhangsan,
                       body={'owner_id': zs_id})
    check_denied('张三无分配权限', res.get('code'))

    print()
    print('=== 10. 导出也受数据范围约束 ===')
    status, res = call_csv('POST', '/customers/export', token=zhangsan,
                           body={'keyword': f'CHK{RUN}测试客户'})
    check('张三导出请求成功', status, 200)
    check_true('拿不到别人的客户', f'CHK{RUN}测试客户' not in res.get('text', ''),
               res.get('text', '')[:80])


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
