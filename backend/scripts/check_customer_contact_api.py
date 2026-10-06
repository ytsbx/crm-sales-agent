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
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime

BASE = os.environ.get('API_BASE', 'http://127.0.0.1:8000/api/v1')
RUN = str(int(time.time()))[-6:]
#: 本脚本开始跑的时刻：导出告警通知不带 CHK 前缀，只能按时间窗圈定。
SCRIPT_STARTED_AT = datetime.now(UTC).isoformat()
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


def call_raw(path, token=None):
    """二进制响应（如 PDF 下载）：返回 (status, content-type, 前8字节)。"""
    req = urllib.request.Request(BASE + path)
    if token:
        req.add_header('Authorization', 'Bearer ' + token)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.headers.get('content-type', ''), resp.read(8)
    except urllib.error.HTTPError as e:
        return e.code, '', e.read(64)


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
        # 导出告警通知是个例外：它的标题是"异常批量导出提醒"、不带 CHK 前缀，
        # 所以只能按本次运行的时间窗圈（早先漏清，每跑一轮留一条）。
        (
            '用例导出告警通知',
            "delete from notifications where business_type = 'customer' "
            f"and created_at >= '{SCRIPT_STARTED_AT}'",
        ),
        ('用例导出审计记录', "delete from audit_logs where action in ('export', 'export_alert') "
                               f"and created_at >= '{SCRIPT_STARTED_AT}' "
                               "and operator_id in (select id from users where username in ('admin','zhangsan'))"),
        ('用例跟进', f"delete from followups where content like '%CHK{RUN}%'"),
        ('用例案例', f"delete from sales_cases where title like '%CHK{RUN}%'"),
        ('用例定制询价', f"delete from custom_inquiries where title like '%CHK{RUN}%'"),
        ('用例新品洞察', f"delete from product_insights where title like '%CHK{RUN}%'"),
        # 合同台账（场景14）：附件→文档→模板，顺序服从外键
        ('用例合同附件', f"delete from business_files where business_type='contract' and business_id in "
                         f"(select id from contract_documents where customer_id in (select id from customers where name like 'CHK{RUN}%'))"),
        ('用例合同文档', f"delete from contract_documents where customer_id in (select id from customers where name like 'CHK{RUN}%')"),
        ('用例合同模板', f"delete from contract_templates where name like 'CHK{RUN}%'"),
        ('用例上传文件', "delete from files where object_key like 'chk/%'"),
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
        ('用例导出探针用户角色', "delete from user_roles where role_id in "
                                f"(select id from roles where code='CHK{RUN}export_probe')"),
        ('用例导出探针权限', "delete from role_permissions where role_id in "
                              f"(select id from roles where code='CHK{RUN}export_probe')"),
        ('用例导出探针角色', f"delete from roles where code='CHK{RUN}export_probe'"),
        ('用例编号计数器', "delete from number_sequences"),
    ]
    async with SessionLocal() as s:
        for label, sql in statements:
            result = await s.execute(text(sql))
            if verbose and result.rowcount:
                print(f'  {result.rowcount:>4}  {label}')
        await s.commit()


SIGN_FILE_ID = None


async def grant_customer_export(user_id: int) -> None:
    """临时给业务员独立导出权限，保持 self 数据范围用于验收场景 19。"""
    from sqlalchemy import select

    from app.core.database import SessionLocal
    from app.modules.user.model import Permission, Role, role_permissions, user_roles

    async with SessionLocal() as session:
        role = Role(
            code=f'CHK{RUN}export_probe', name=f'CHK{RUN}导出权限探针', data_scope='self'
        )
        session.add(role)
        await session.flush()
        permission = (await session.execute(
            select(Permission).where(Permission.code == 'customer:export')
        )).scalars().one()
        await session.execute(role_permissions.insert().values(
            role_id=role.id, permission_id=permission.id
        ))
        await session.execute(user_roles.insert().values(
            user_id=user_id, role_id=role.id
        ))
        await session.commit()


async def revoke_customer_export() -> None:
    """在后续“默认无导出权限”断言前，撤销本套件临时授予。"""
    from sqlalchemy import text

    from app.core.database import SessionLocal

    async with SessionLocal() as session:
        await session.execute(text(
            "delete from user_roles where role_id in "
            "(select id from roles where code=:code)"
        ), {'code': f'CHK{RUN}export_probe'})
        await session.execute(text(
            "delete from role_permissions where role_id in "
            "(select id from roles where code=:code)"
        ), {'code': f'CHK{RUN}export_probe'})
        await session.execute(text("delete from roles where code=:code"),
                              {'code': f'CHK{RUN}export_probe'})
        await session.commit()


async def main():
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
        'exemption_reason': 'waiting_external',
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
    status, res = call_csv('POST', '/customers/export', token=admin,
                           body={'purpose': 'business_analysis'})
    check('导出成功', status, 200)
    check_true('是 CSV', 'text/csv' in res.get('ctype', ''), str(res.get('ctype')))
    check_true('带文件名', 'customers.csv' in res.get('disposition', ''),
               str(res.get('disposition')))
    check_true('有表头', '客户名称' in res.get('text', ''), res.get('text', '')[:60])

    status, res = call_csv('POST', '/customers/export', token=admin,
                           body={'keyword': f'CHK{RUN}测试客户', 'purpose': 'business_analysis'})
    check('按关键字筛选导出', status, 200)
    body_text = res.get('text', '')
    check_true('只含目标客户', f'CHK{RUN}测试客户' in body_text, body_text[:100])
    check_true('不含另一个客户', f'CHK{RUN}张三客户' not in body_text, '关键字筛选生效')

    status, res = call_csv('POST', '/customers/export', token=admin,
                           body={'level': '不存在的等级', 'purpose': 'business_analysis'})
    check('筛不到也是 200', status, 200)

    status, res = call_csv('POST', '/customers/export', token=admin, body={})
    check('未填写导出用途被拒', status, 400)
    status, res = call_csv('POST', '/customers/export', token=admin,
                           body={'purpose': 'other'})
    check('其他未填写补充说明被拒', status, 400)

    status, res = call_csv('GET', '/customers/export?purpose=business_analysis', token=admin)
    check('GET 导出成功', status, 200)
    status, res = call_csv('GET', '/customers/export', token=admin)
    check('GET 未填写导出用途被拒', status, 400)
    status, res = call_csv('GET', '/customers/export?purpose=other', token=admin)
    check('GET 其他用途未填写补充说明被拒', status, 422)
    other_get_query = urllib.parse.urlencode({
        'purpose': 'other', 'purpose_note': '临时核对数据',
    })
    status, res = call_csv('GET', f'/customers/export?{other_get_query}', token=admin)
    check('GET 其他用途填写补充说明后可导出', status, 200)

    await grant_customer_export(zs_id)
    # 权限会编码进 JWT；刷新 token 才能反映本次临时授权。
    zhangsan = login('zhangsan', '123456')
    scope_csv = call_csv('POST', '/customers/export', token=zhangsan, body={
        'keyword': f'CHK{RUN}', 'purpose': 'business_analysis',
    })
    status, res = scope_csv
    check('获授权业务员可导出自身范围', status, 200)
    check_true('导出包含本人客户', f'CHK{RUN}张三客户' in res.get('text', ''),
               res.get('text', '')[:100])
    check_true('导出不包含他人客户', f'CHK{RUN}测试客户' not in res.get('text', ''),
               res.get('text', '')[:100])

    status, res = call_csv('POST', '/customers/export', token=zhangsan, body={
        'keyword': f'CHK{RUN}张三客户', 'purpose': 'other',
        'purpose_note': '临时核对客户资料完整性',
    })
    check('其他用途填写补充说明后可导出', status, 200)
    status, res = call('GET', f'/audit-logs?action=export&operator_id={zs_id}&page_size=20',
                       token=admin)
    export_audits = (res.get('data') or {}).get('items', [])
    scoped_audit = next((row for row in export_audits
                         if (row.get('after_data') or {}).get('purpose') == 'business_analysis'), None)
    check_true('导出审计记录责任人', scoped_audit is not None and scoped_audit['operator_id'] == zs_id,
               str(scoped_audit))
    check_true('审计保留用途、范围、筛选与数量', scoped_audit is not None and
               bool(scoped_audit.get('created_at')) and
               scoped_audit['after_data'].get('data_scope') == 'self' and
               scoped_audit['after_data'].get('filters', {}).get('keyword_applied') is True and
               scoped_audit['after_data'].get('count') == 1 and
               len(scoped_audit['after_data'].get('customer_ids') or []) == 1,
               str((scoped_audit or {}).get('after_data')))
    other_audit = next((row for row in export_audits
                        if (row.get('after_data') or {}).get('purpose') == 'other'), None)
    check_true('其他用途补充说明写入审计', other_audit is not None and
               other_audit['after_data'].get('purpose_note') == '临时核对客户资料完整性',
               str((other_audit or {}).get('after_data')))
    await revoke_customer_export()

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
    print('=== 10. 导出闸门：独立权限 + 数据范围 ===')
    # 导出闸门（§11.2/场景19）：导出是 customer:export 独立授权，
    # 销售默认没有——"能看列表"不再等于"能批量拿走客户"
    status, res = call_csv('POST', '/customers/export', token=zhangsan,
                           body={'keyword': f'CHK{RUN}测试客户', 'purpose': 'business_analysis'})
    check('张三无导出权限被拒', res.get('code'), 40301)
    status, res = call_csv('POST', '/customers/export', token=admin,
                           body={'keyword': f'CHK{RUN}测试客户', 'purpose': 'business_analysis'})
    check('管理员导出成功', status, 200)
    check_true('导出含夹具客户', f'CHK{RUN}测试客户' in res.get('text', ''),
               res.get('text', '')[:80])

    # 异常批量访问告警（§六）：阈值调低后一次导出即触发，写 export_alert 审计并推管理员
    status, res = call('PATCH', '/settings', token=admin, body={
        'key': 'export', 'value': {'limit': 5000, 'alert_rows': 1, 'alert_window_hours': 24},
    })
    check('调低告警阈值', res.get('code'), 0)
    call_csv('POST', '/customers/export', token=admin, body={
        'keyword': f'CHK{RUN}', 'purpose': 'business_analysis',
    })
    status, res = call('GET', '/audit-logs?action=export_alert&page_size=5', token=admin)
    check_true('触发异常导出告警审计',
               len(res.get('data', {}).get('items', [])) >= 1,
               str(res)[:120])
    call('PATCH', '/settings', token=admin, body={
        'key': 'export', 'value': {'limit': 5000, 'alert_rows': 20000, 'alert_window_hours': 24},
    })

    print()
    print('=== 11. 合同模板与台账（§3.6/场景14）===')
    status, res = call('POST', '/contract-templates', token=admin, body={
        'doc_type': 'contract', 'name': f'CHK{RUN}标准销售合同',
        'body': '客户 {{customer.name}}，付款方式 {{extra.付款方式}}，签约日 {{today}}。',
    })
    check('建模板', res.get('code'), 0)
    template_id = res['data']['id']
    status, res = call('POST', '/contract-templates', token=admin, body={
        'doc_type': 'contract', 'name': f'CHK{RUN}标准销售合同', 'body': '第二版 {{customer.name}}',
    })
    check('同名模板=新版本', res['data']['version'], 2)
    tpls = call('GET', '/contract-templates', token=admin)[1]['data']
    check('旧版本仍存在',
          sum(1 for t in tpls if t['name'] == f'CHK{RUN}标准销售合同'), 2)

    status, res = call('POST', '/contract-documents', token=admin, body={
        'template_id': template_id, 'customer_id': customer_id,
        # 登记签署要求有正式依据（业务口径 2026-10-05）：条款可以先备，
        # 但签署前必须挂上正式订单或已发送/已接受的报价。这份下面要签，所以现在就绑上订单。
        'order_id': order_id,
        'extra_fields': {'付款方式': '月结30天'},
    })
    check('生成合同草稿', res.get('code'), 0)
    doc_id, doc_no = res['data']['id'], res['data']['doc_no']
    check_true('编号 CT 前缀', doc_no.startswith('CT'), doc_no)
    check_true('客户名已填入', f'CHK{RUN}' in res['data']['content_snapshot'],
               res['data']['content_snapshot'][:60])
    check_true('空白项已填入', '月结30天' in res['data']['content_snapshot'], '')
    check('草稿状态', res['data']['status'], 'draft')

    status, res = call('POST', f'/contract-documents/{doc_id}/sign', token=admin,
                       body={'file_id': SIGN_FILE_ID, 'note': '线下签'})
    check('登记签署', res.get('code'), 0)
    check('状态已签署', res['data']['status'], 'signed')
    status, res = call('GET', f'/business/contract/{doc_id}/files', token=admin)
    check_true('签署件已挂载可查', res.get('code') == 0 and len(res.get('data') or []) >= 1,
               str(res)[:120])
    status, res = call('POST', f'/contract-documents/{doc_id}/sign', token=admin,
                       body={'file_id': SIGN_FILE_ID})
    check('已签文档不能重复签', res.get('code'), 40002)

    # 模板生成 → 可下载（§3.6）：下载的是 PDF，且不代表已签
    status, ctype, magic = call_raw(f'/contract-documents/{doc_id}/download', admin)
    check('合同 PDF 下载', status, 200)
    check_true('返回 PDF 字节', magic.startswith(b'%PDF'), str(magic))

    # 换负责人：新负责人按权限查看，原负责人失去访问（场景14 后半）
    status, res = call('GET', '/users?page_size=50', token=admin)
    other_user = next(
        (u for u in res['data']['items'] if u['username'] == 'lisi'),
        next((u for u in res['data']['items'] if u['id'] != 1), None),
    )
    status, res = call('GET', f'/customers/{zs_customer_id}', token=zhangsan)
    original_owner = res['data']['owner_id']
    status, res = call('POST', f'/customers/{zs_customer_id}/assign', token=admin,
                       body={'owner_id': other_user['id'], 'reason': '场景14 换负责人'})
    check('换负责人成功', res.get('code'), 0)
    status, res = call('GET', f'/contract-documents/{doc_id}', token=zhangsan)
    check('原负责人失去访问', res.get('code'), 40302)
    status, res = call('GET', f'/contract-documents/{doc_id}', token=admin)
    check('管理员仍可读', res.get('code'), 0)
    call('POST', f'/customers/{zs_customer_id}/assign', token=admin,
         body={'owner_id': original_owner, 'reason': '场景14 复原'})

    print()
    print('=== 12. 案例库：审核发布 + 脱敏分享（§3.7/场景15）===')
    lisi = login('lisi', '123456')
    case_payload = {
        # 标题带**客户全称**：这是此前漏掉的泄露源——分享版把客户字段换成代称，
        # 标题却原样返回（搜索也能按它命中）
        'title': f'CHK{RUN}张三客户 打样转返单案例',
        'customer_id': zs_customer_id,
        'customer_label': '某包装制品厂',
        'industry': '包装',
        'product_line': '彩盒',
        'stage_reached': 'repeat',
        'problem_tags': ['价格异议', '交期紧'],
        'key_actions': '产前样提前三天确认，锁定产线档期',
        'lessons': '交期异议先给生产计划表，不要空口承诺',
    }
    status, res = call('POST', '/cases', token=zhangsan, body=case_payload)
    check('建案例草稿', res.get('code'), 0)
    case_id = res['data']['id']
    status, res = call('GET', f'/cases/{case_id}', token=zhangsan)
    check_true('作者看得到真实客户', res['data']['customer_id'] == zs_customer_id, '')
    status, res = call('POST', f'/cases/{case_id}/submit', token=zhangsan, body={})
    check('提交审核', res.get('code'), 0)
    status, res = call('POST', f'/cases/{case_id}/review', token=zhangsan,
                       body={'approve': True})
    check('销售不能自己审核', res.get('code'), 40301)
    status, res = call('POST', f'/cases/{case_id}/review', token=lisi,
                       body={'approve': True, 'note': '做法可复制，通过'})
    check('主管审核发布', res.get('code'), 0)
    check('状态已发布', res['data']['status'], 'published')
    status, res = call('GET', f'/cases/{case_id}', token=zhangsan)
    check_true('作者本人可见自己案例的客户', res['data']['customer_id'] == zs_customer_id, '')
    wangwu = login('wangwu', '123456')  # 非作者、非主管：脱敏分享视角
    status, res = call('GET', f'/cases/{case_id}', token=wangwu)
    check_true('培训视角看不到真实客户ID', res['data']['customer_id'] is None,
               str(res['data']['customer_id']))
    check_true('只看到代称', res['data']['customer_label'] == '某包装制品厂', '')
    check_true('做法内容完整可学', '生产计划表' in res['data']['lessons'], '')
    check_true('分享视角标题不含客户全称',
               f'CHK{RUN}张三客户' not in (res['data'].get('title') or ''),
               str(res['data'].get('title')))
    status, res = call('GET', f'/cases/{case_id}', token=lisi)
    check_true('主管可见真实客户', res['data']['customer_id'] == zs_customer_id, '')
    status, res = call('GET', f'/cases?keyword={RUN}', token=zhangsan)
    check_true('关键词检索命中',
               any(row['id'] == case_id for row in res['data']['items']), '')

    print()
    print('=== 12.1 分享版脱敏：列表与详情同一口径，正文与审核意见也纳入（§5.1.1/§5.1.2）===')
    status, res = call('GET', f'/customers/{zs_customer_id}', token=zhangsan)
    real_name = res['data']['name']
    status, res = call('POST', '/cases', token=zhangsan, body={
        # 标题与**正文**都带客户全称：正文此前根本没做全称替换，只有标题做了
        'title': f'{real_name} 的返单打法',
        'customer_id': zs_customer_id,
        'customer_label': '某包装制品厂',
        'stage_reached': 'repeat',
        'key_actions': f'先给 {real_name} 做产前样，再锁产线档期',
        'lessons': '交期紧就把生产计划表发过去',
    })
    share_case_id = res['data']['id']
    call('POST', f'/cases/{share_case_id}/submit', token=zhangsan, body={})
    # 审核意见里塞手机号与合同号：它也是自由文本，此前完全没进脱敏
    call('POST', f'/cases/{share_case_id}/review', token=lisi,
         body={'approve': True,
               'note': f'已与 {real_name} 的 13800138000 复核，合同 HT2024-7788 已归档'})

    status, detail = call('GET', f'/cases/{share_case_id}', token=wangwu)
    # 搜**读者看得见的词**：标题里的「返单打法」与正文里的「产前样」都该命中。
    # 这同时证明"正文搜索没有被取消"——审查方明确要求案例仍要能按做法和问题检索。
    status, listing = call(
        'GET', '/cases?keyword=' + urllib.parse.quote('返单打法') + '&page_size=200',
        token=wangwu,
    )
    row = next((r for r in listing['data']['items'] if r['id'] == share_case_id), None)
    check_true('分享视角按**看得见的标题**能搜到这条', row is not None, '')
    status, listing = call(
        'GET', '/cases?keyword=' + urllib.parse.quote('产前样') + '&page_size=200',
        token=wangwu,
    )
    check_true('分享视角按**看得见的正文**能搜到这条',
               any(r['id'] == share_case_id for r in listing['data']['items']), '')

    print()
    print('=== 12.1b 搜索不能变成"探测接口"（返工单 P1-6）===')
    # 读者看见的是「某包装制品厂 的电话〔手机号〕」。那么拿**被隐藏的原文**去搜
    # 就不该命中：命中等于确认"这条案例里存在这个号码"——内容没看到，事实泄露了。
    # 判据必须与列表/详情下发时**同一套**（否则列表脱敏、搜索漏风）。
    status, listing = call('GET', f'/cases?keyword={RUN}&page_size=200', token=wangwu)
    check_true('分享视角搜客户全称搜不到（那个词他看不见）',
               not any(r['id'] == share_case_id for r in listing['data']['items']), '')
    status, listing = call('GET', f'/cases?keyword=13800138000&page_size=200', token=wangwu)
    check_true('分享视角搜被隐藏的手机号搜不到',
               not any(r['id'] == share_case_id for r in listing['data']['items']), '')
    # 原文搜索**保留给有原文权限的人**：同一个词主管照样搜得到
    status, listing = call('GET', f'/cases?keyword={RUN}&page_size=200', token=lisi)
    check_true('主管按原文仍能搜到（原文搜索没有取消）',
               any(r['id'] == share_case_id for r in listing['data']['items']), '')
    if row:
        check_true('列表标题不含客户全称（此前列表比详情松）',
                   real_name not in (row.get('title') or ''), str(row.get('title')))
        # 这条是 §5.1.1 的核心：两个视角不许各脱各的
        check('列表与详情的标题同口径', row.get('title'), detail['data'].get('title'))
        check_true('列表正文里的客户全称也被换成代称',
                   real_name not in (row.get('key_actions') or ''),
                   str(row.get('key_actions')))
    check_true('详情正文里的客户全称被换成代称（此前只做了标题）',
               real_name not in (detail['data'].get('key_actions') or ''),
               str(detail['data'].get('key_actions')))
    check_true('正文里出现的是代称',
               '某包装制品厂' in (detail['data'].get('key_actions') or ''),
               str(detail['data'].get('key_actions')))
    note = detail['data'].get('review_note') or ''
    check_true('审核意见里的手机号被脱敏', '13800138000' not in note, note)
    check_true('审核意见里的合同号被脱敏', 'HT2024-7788' not in note, note)
    # 只吃到 `HT2024`、留下 `-7788` 是**半截泄露**：要求整个号都不剩
    check_true('合同号没有留下半截（HT2024-7788 的尾段也要吃掉）',
               '7788' not in note, note)
    check_true('受控片段被换成占位符（不是整段消失）', '〔' in note, note)
    check_true('脱敏统计里能看到"客户名"这一类',
               any('客户名' in item for item in (detail['data'].get('redaction_summary') or [])),
               str(detail['data'].get('redaction_summary')))
    # 作者看原文：脱敏只作用于分享视角
    status, own = call('GET', f'/cases/{share_case_id}', token=zhangsan)
    check_true('作者视角仍是原文（脱敏不误伤作者）',
               real_name in (own['data'].get('key_actions') or ''),
               str(own['data'].get('key_actions')))

    print()
    print('=== 12.2 案例证据：显式 null 能解除、引用必须同客户且在范围内（§5.1.3/§5.1.4）===')
    # 两个客户各挂一张报价：用来验"别人的单子挂不上来"
    status, res = call('POST', '/customers', token=zhangsan,
                       body={'name': f'CHK{RUN}案例第二客户'})
    other_customer_id = res['data']['id']
    CREATED_CUSTOMER_IDS.append(other_customer_id)
    status, res = call('POST', '/opportunities', token=zhangsan,
                       body={'customer_id': zs_customer_id, 'title': f'CHK{RUN}证据商机A'})
    ev_opp_a = res['data']['id']
    status, res = call('POST', '/quotes', token=zhangsan, body={'opportunity_id': ev_opp_a})
    ev_quote_a = res['data']['quote_id']
    status, res = call('POST', '/opportunities', token=zhangsan,
                       body={'customer_id': other_customer_id, 'title': f'CHK{RUN}证据商机B'})
    ev_opp_b = res['data']['id']
    status, res = call('POST', '/quotes', token=zhangsan, body={'opportunity_id': ev_opp_b})
    ev_quote_b = res['data']['quote_id']
    check_true('两张报价都建好了（夹具前提）',
               bool(ev_quote_a) and bool(ev_quote_b), f'{ev_quote_a}/{ev_quote_b}')

    # ① 同客户的报价可以挂
    status, res = call('POST', '/cases', token=zhangsan, body={
        'title': f'CHK{RUN}证据校验用例',
        'customer_id': zs_customer_id,
        'customer_label': '某包装制品厂',
        'quote_id': ev_quote_a,
        'problem_tags': ['价格异议'],
    })
    check('挂同客户的报价可以建案例', res.get('code'), 0)
    ev_case_id = res['data']['id']
    check('报价关联已写入', res['data']['quote_id'], ev_quote_a)
    check('问题标签已写入', res['data']['problem_tags'], ['价格异议'])

    # ② 别的客户的单子挂不上来（同客户校验；前端候选筛选不是安全校验）
    status, res = call('PATCH', f'/cases/{ev_case_id}', token=zhangsan,
                       body={'quote_id': ev_quote_b})
    check('挂别的客户的报价被拒（同客户校验）', res.get('code'), 40001)

    # ③ 不存在的单据 → 404（"引用必须存在"）
    status, res = call('PATCH', f'/cases/{ev_case_id}', token=zhangsan,
                       body={'quote_id': 999999999})
    check('挂不存在的报价被拒', status, 404)

    # ④ 显式 null = 解除关联（原来 `if value is not None` 一律跳过，解除不了）
    status, res = call('PATCH', f'/cases/{ev_case_id}', token=zhangsan,
                       body={'quote_id': None})
    check('显式 null 能解除报价关联', res.get('code'), 0)
    check('报价关联确实被清掉', res['data']['quote_id'], None)
    status, res = call('PATCH', f'/cases/{ev_case_id}', token=zhangsan,
                       body={'problem_tags': []})
    check('传空数组能清掉问题标签', res['data']['problem_tags'], [])

    # ⑤ 换客户时不能把旧客户的证据留在身上（§5.1.4 说的"残留旧客户证据"）
    status, res = call('POST', '/cases', token=zhangsan, body={
        'title': f'CHK{RUN}换客户用例', 'customer_id': zs_customer_id,
        'quote_id': ev_quote_a,
    })
    swap_case = res['data']['id']
    status, res = call('PATCH', f'/cases/{swap_case}', token=zhangsan,
                       body={'customer_id': other_customer_id})
    check('换客户但留着旧客户的证据 → 被拒', res.get('code'), 40001)
    status, res = call('PATCH', f'/cases/{swap_case}', token=zhangsan,
                       body={'customer_id': other_customer_id, 'quote_id': None})
    check('换客户同时解除旧证据 → 通过', res.get('code'), 0)
    check('客户已换成第二家', res['data']['customer_id'], other_customer_id)

    print()
    print('=== 12.3 已发布案例只读 + 修订稿 + 审核历史累加（§5.1.5）===')
    # 用 12 节那条已发布案例（lisi 批过、note='做法可复制，通过'）
    status, res = call('PATCH', f'/cases/{case_id}', token=zhangsan,
                       body={'key_actions': '偷偷改关键内容'})
    check('作者改已发布案例被拒（审核批的是那一版内容）', status, 422)
    status, res = call('PATCH', f'/cases/{case_id}', token=lisi,
                       body={'key_actions': '主管也偷偷改'})
    check('主管改已发布案例也被拒（要改就开修订稿）', status, 422)

    status, res = call('POST', f'/cases/{case_id}/revise', token=zhangsan, body={})
    check('可以开修订稿', res.get('code'), 0)
    revision_id = res['data']['id']
    check('修订稿版本号 = 原版 + 1', res['data']['version'], 2)
    check('修订稿指向原版', res['data']['revision_of_id'], case_id)
    check('修订稿是草稿（不继承审核结论）', res['data']['status'], 'draft')
    check('修订稿的审核历史是空的', res['data']['review_history'], [])
    check('修订稿继承了正文内容',
          res['data']['key_actions'], '产前样提前三天确认，锁定产线档期')

    # 驳回一次再批准：审核历史必须**逐条累加**（原来只有一个 review_note 单值，
    # 下一次审核就把它覆盖了，"被驳回过几次"事后查不出来）
    call('POST', f'/cases/{revision_id}/submit', token=zhangsan, body={})
    call('POST', f'/cases/{revision_id}/review', token=lisi,
         body={'approve': False, 'note': '先补充证据'})
    status, res = call('GET', f'/cases/{revision_id}', token=zhangsan)
    check('驳回后修订稿状态', res['data']['status'], 'rejected')
    check('驳回留了一条审核历史', len(res['data']['review_history']), 1)
    check('第一条历史的结论是驳回', res['data']['review_history'][0]['approve'], False)

    call('PATCH', f'/cases/{revision_id}', token=zhangsan,
         body={'key_actions': '产前样提前三天确认，锁定产线档期（补了证据）'})
    call('POST', f'/cases/{revision_id}/submit', token=zhangsan, body={})
    status, res = call('POST', f'/cases/{revision_id}/review', token=lisi,
                       body={'approve': True, 'note': '这次可以'})
    check('修订稿批准后发布', res['data']['status'], 'published')
    check('审核历史累加到两条（不是只剩最后一条）',
          len(res['data']['review_history']), 2)
    check('两条历史分别是驳回与批准',
          [h['approve'] for h in res['data']['review_history']], [False, True])
    check('历史里带着轮次', [h['round'] for h in res['data']['review_history']], [1, 2])

    # 原版转「已被修订版取代」，但仍可读（培训不断档）
    status, res = call('GET', f'/cases/{case_id}', token=zhangsan)
    check('原版转为已被取代', res['data']['status'], 'superseded')
    check('原版标出被哪一版取代', res['data']['superseded_by'], revision_id)
    check_true('原版内容仍在（培训不断档）',
               '产前样' in (res['data'].get('key_actions') or ''),
               str(res['data'].get('key_actions')))
    check('原版保留了它自己那一轮审核历史',
          len(res['data']['review_history']), 1)

    # 列表默认只列当前版本；历史版本要显式要
    status, res = call('GET', f'/cases?keyword={RUN}&include_history=true&page_size=200',
                       token=zhangsan)
    ids_hist = [row['id'] for row in res['data']['items']]
    check('include_history=true 能看到被取代的原版', case_id in ids_hist, True)
    status, res = call('GET', f'/cases?keyword={RUN}&page_size=200', token=zhangsan)
    ids_now = [row['id'] for row in res['data']['items']]
    check('默认列表不含被取代的原版', case_id in ids_now, False)
    check('默认列表含修订版', revision_id in ids_now, True)

    print('=== 12.5 审核历史逐条脱敏（审视第 5 条：最新脱敏了、历史没有）===')
    # 审视原话：「最新的 review_note 会脱敏，但新增的 review_history 原样返回。
    # 普通培训读者仍可从历史意见得到真实客户名和手机号」。
    # 根因：serialize_case 里只有 review_note 走了 mask_text，review_history
    # 是 `case.review_history or []` 直接下发的。这里造一条两轮审核、每轮意见
    # 都写了手机号的案例，再从普通读者视角读它 —— 两轮意见都不能漏。
    cust_real_name = f'CHK{RUN}测试客户'
    real_phone = '13800138000'
    status, res = call('POST', '/cases', token=admin, body={
        # 标题**必须带 CHK 前缀**：本套件的清库按 `title like '%CHK{RUN}%'` 物理删，
        # 少这个前缀就会留下一条活着的案例，删客户时撞外键、清理整段中断，
        # 后面几个套件跟着一起红（实测踩过）。
        'title': f'CHK{RUN}脱敏历史案例', 'customer_id': customer_id,
        'industry': '食品', 'product_line': '重型',
        'key_actions': '按计划推进',
        # 手机号**写进正文**（可搜索字段），下面才能验"搜索算不算探测口"。
        # 写在审核意见里是搜不到的——测试搜索却把串放在不可搜字段上，等于没测。
        'lessons': f'提前锁产线，客户电话{real_phone}',
    })
    check('造一条待审案例', res.get('code'), 0)
    leak_id = res['data']['id']
    call('POST', f'/cases/{leak_id}/submit', token=admin, body={})
    call('POST', f'/cases/{leak_id}/review', token=lisi, body={
        'approve': False, 'note': f'第一轮：客户甲 电话{real_phone}，请补充',
    })
    call('POST', f'/cases/{leak_id}/submit', token=admin, body={})
    call('POST', f'/cases/{leak_id}/review', token=lisi, body={
        'approve': True, 'note': f'第二轮：客户甲 电话{real_phone}，通过',
    })
    # 用一个**非作者、非主管**的账号读：wangwu（财务）。对案例而言他仍是普通
    # 读者——分享版按"是否作者/主管"判，不看数据范围。
    status, res = call('GET', f'/cases/{leak_id}', token=wangwu)
    check('普通读者能读到这条案例', status, 200)
    hist = res['data'].get('review_history') or []
    check('两轮审核历史都在', len(hist), 2)
    notes = ' '.join(str(h.get('note') or '') for h in hist)
    check_true('历史意见里不能出现真手机号', real_phone not in notes, notes[:80])
    check_true('历史意见里的手机号已换成占位', '〔手机号〕' in notes, notes[:80])
    check_true('历史意见里的真实客户名也被换成代称',
               cust_real_name not in notes, notes[:80])
    check_true('最新那条意见同样脱敏（这条原来就是对的，别改坏）',
               real_phone not in str(res['data'].get('review_note') or ''),
               str(res['data'].get('review_note'))[:60])

    # 搜索口径：普通读者不能拿敏感串去"探测"某条案例是否命中。
    # 正文对他脱敏了，若 SQL 仍按原文匹配，搜索就变成了探测接口——
    # 内容看不到，但"这条案例里有这个号码"这件事泄露了。
    status, res = call('GET', f'/cases?keyword={real_phone}&page_size=50', token=wangwu)
    check_true('普通读者按手机号搜不到这条案例（探测口已封）',
               all(row['id'] != leak_id for row in res['data']['items']),
               [row['id'] for row in res['data']['items']][:5])
    # 但**拿得到原文的人**（主管）照常搜得到——别把正常检索能力一刀切掉
    status, res = call('GET', f'/cases?keyword={real_phone}&page_size=50', token=lisi)
    check_true('主管仍能按手机号搜到（检索能力没被砍）',
               any(row['id'] == leak_id for row in res['data']['items']),
               [row['id'] for row in res['data']['items']][:5])

    # 清掉这条夹具，别留给守门套件。
    # 走接口删（本套件是**纯 HTTP**、不连数据库），软删就够——
    # 守门套件看的是"活着的"记录。
    status, res = call('DELETE', f'/cases/{leak_id}', token=lisi)
    check('夹具已清理（主管可删）', res.get('code'), 0)

    print()
    print('=== 12.4 案例列表：真分页 + 完整筛选（第四批 §5.1.6）===')
    # 关键词里有中文，**必须编码**再拼进 URL：不编码的话 urllib 会在发包前
    # 拿 ascii 编 URL，直接抛 UnicodeEncodeError——看着像"请求被拒"，
    # 其实是脚本自己崩了（同一个坑在别的套件里也踩过一次）。
    PAGE_KW = urllib.parse.quote(f'{RUN}分页案例')
    # 造 3 条可筛的：两个行业、两个产品线、三个阶段、一条带问题标签。
    # 全部走发布流程，否则普通读者看不到，后面的筛选断言会空跑。
    paged_ids: list[int] = []
    for idx, (industry, line, stage, tags) in enumerate([
        ('机械', '重型包装线', 'sample', ['交期紧']),
        ('机械', '轻型包装线', 'first_order', []),
        ('食品', '重型包装线', 'repeat', []),
    ]):
        status, res = call('POST', '/cases', token=admin, body={
            # 标题必须带 CHK{RUN} 前缀：本套件的清理是按 `title like '%CHK{RUN}%'` 删的，
            # 少写这个前缀，夹具会留在库里、被守门套件当场抓住。
            'title': f'CHK{RUN}分页案例{idx}', 'industry': industry,
            'product_line': line, 'stage_reached': stage, 'problem_tags': tags,
            'key_actions': '按计划推进', 'lessons': '提前锁产线',
        })
        assert status == 200, res
        made_id = res['data']['id']
        call('POST', f'/cases/{made_id}/submit', token=admin, body={})
        call('POST', f'/cases/{made_id}/review', token=lisi,
             body={'approve': True, 'note': '分页夹具'})
        paged_ids.append(made_id)

    status, page1 = call('GET', f'/cases?keyword={PAGE_KW}&page=1&page_size=2', token=admin)
    check_true('案例列表返回分页结构（此前是裸数组、limit 200 硬顶）',
               sorted(page1['data']) == ['items', 'page', 'page_size', 'total'], sorted(page1['data']))
    check('page_size 真生效', len(page1['data']['items']), 2)
    check('total 是总数而不是本页条数', page1['data']['total'], 3)
    status, page2 = call('GET', f'/cases?keyword={PAGE_KW}&page=2&page_size=2', token=admin)
    check('第二页拿到剩下的那条', len(page2['data']['items']), 1)
    check_true('两页不重复',
               page1['data']['items'][0]['id'] not in [r['id'] for r in page2['data']['items']])

    status, by_industry = call('GET', f'/cases?keyword={PAGE_KW}&industry={urllib.parse.quote("食品")}', token=admin)
    check_true('按行业筛（包含匹配，不用打全称）',
               len(by_industry['data']['items']) == 1
               and by_industry['data']['items'][0]['industry'] == '食品',
               [r['industry'] for r in by_industry['data']['items']])

    status, by_line = call('GET', f'/cases?keyword={PAGE_KW}&product_line={urllib.parse.quote("重型")}', token=admin)
    check('按产品线筛（包含匹配，前端此前没接通）', len(by_line['data']['items']), 2)

    status, by_stage = call('GET', f'/cases?keyword={PAGE_KW}&stage=repeat', token=admin)
    check('按阶段筛', len(by_stage['data']['items']), 1)

    status, by_tag = call('GET', f'/cases?keyword={PAGE_KW}&problem_tags={urllib.parse.quote("交期紧")}', token=admin)
    check('按问题标签筛（这个筛选后端此前根本没有，交接说明把它算进「已支持」了）',
          len(by_tag['data']['items']), 1)

    print('=== 13. 定制询价修订链（§3.3：改了三次要求要能看出怎么变的）===')
    status, res = call('POST', '/custom-inquiries', token=zhangsan, body={
        'title': f'CHK{RUN}定制礼盒', 'description': '客户要天地盖礼盒，烫金',
        'customer_id': zs_customer_id, 'quantity': 1000, 'target_price': 12.5,
    })
    check('建询价 v1', res.get('code'), 0)
    check('初始版本号 1', res['data']['version'], 1)
    inquiry_v1 = res['data']['id']

    # 回归（照真实用户操作）：把自己的定制需求挂到**别人的客户**上必须被拒。
    # 原先创建路径只查"客户存在"，于是任何有报价权限的人都能把需求挂到同事的客户上，
    # 把对方的产品要求与目标价带进自己的报价单。案例库此前踩过同一个坑，
    # 那里用的是 get_visible_customer——这里必须同一口径。
    # （注：接口用例原本传的 customer_id 恰好是自己可见的，所以一直没暴露。）
    status, res = call('POST', '/custom-inquiries', token=zhangsan, body={
        'title': f'CHK{RUN}越权挂客户', 'customer_id': customer_id, 'quantity': 1,
    })
    check_denied('张三把需求挂到别人的客户上', res.get('code'))

    status, res = call('POST', f'/custom-inquiries/{inquiry_v1}/revise', token=zhangsan, body={
        'revision_note': '客户把烫金改成 UV，数量降到 800',
        'description': '天地盖礼盒，UV 工艺', 'quantity': 800,
    })
    check('修订成功', res.get('code'), 0)
    inquiry_v2 = res['data']['id']
    check('版本号 +1', res['data']['version'], 2)
    check('新一版回到待评估', res['data']['status'], 'open')

    status, res = call('GET', f'/custom-inquiries/{inquiry_v2}', token=zhangsan)
    check('新版数量已改', res['data']['quantity'], 800.0)
    check('新版保留修订说明', res['data']['revision_note'], '客户把烫金改成 UV，数量降到 800')

    print()
    print('=== 14. 新品洞察：评审通过才可转询价线索（§3.3 第三类）===')
    status, res = call('POST', '/product-insights', token=admin, body={
        'title': f'CHK{RUN}可降解餐盒',
        'source': '展会',
        'target_customer': '连锁餐饮',
        'direction': 'PLA 可降解外卖餐盒，主打环保合规',
        'selling_points': '耐油耐热、可堆肥认证',
        'price_assumption': 1.85,
        'conclusion': '认证成本待核算，先做小样',
        'owner_id': zs_id,
    })
    check('建洞察', res.get('code'), 0)
    insight_id = res['data']['id']
    check('初始为记录中', res['data']['status'], 'draft')
    status, res = call('POST', f'/product-insights/{insight_id}/convert', token=admin, body={})
    check('未评审不能转线索', res.get('code'), 40002)
    status, res = call('POST', f'/product-insights/{insight_id}/submit', token=admin, body={})
    check('提交评审', res.get('code'), 0)
    status, res = call('POST', f'/product-insights/{insight_id}/review', token=zhangsan,
                       body={'approve': True})
    check('业务员不能自己评审', res.get('code'), 40301)
    status, res = call('POST', f'/product-insights/{insight_id}/review', token=lisi,
                       body={'approve': True, 'note': '方向可以，先做小样'})
    check('主管评审通过', res.get('code'), 0)
    check('状态已通过', res['data']['status'], 'approved')
    status, res = call('POST', f'/product-insights/{insight_id}/convert', token=admin, body={})
    check('转询价线索', res.get('code'), 0)
    new_inquiry_id = res['data']['inquiry_id']
    status, res = call('GET', f'/custom-inquiries/{new_inquiry_id}', token=admin)
    check('线索已生成且在待评估', res['data']['status'], 'open')
    check_true('价格假设标注为未确认',
               '价格假设' in (res['data']['description'] or '')
               and '未确认' in (res['data']['description'] or ''),
               (res['data']['description'] or '')[:80])
    status, res = call('GET', f'/product-insights/{insight_id}', token=admin)
    check('洞察回写线索 id', res['data']['converted_inquiry_id'], new_inquiry_id)

    # 回归（照真实用户操作）：**不传 owner_id**、用**主管**账号再建一条。
    # 上面那条用例手工传了 owner_id，恰好盖住了"创建时不写归属 → 列表立刻查不到、
    # 点进去报不在范围内"这个真 bug（前端本来就不传这个字段）。所以这条什么都不传。
    status, res = call('GET', '/auth/me', token=lisi)
    lisi_id = res['data']['id']
    status, res = call('POST', '/product-insights', token=lisi, body={
        'title': f'CHK{RUN}主管自建洞察',
        'direction': '主管自己记一条，不指定负责人',
        'conclusion': '用于验证默认归属',
    })
    check('主管建洞察（不传负责人）', res.get('code'), 0)
    lisi_insight = res['data']['id']
    check('归属自动落到创建人', res['data']['owner_id'], lisi_id)
    status, res = call('GET', '/product-insights?page_size=50', token=lisi)
    check_true('建完立刻能在列表里查到',
               any(row['id'] == lisi_insight for row in res['data']['items']),
               f"共 {len(res['data']['items'])} 条")
    status, res = call('GET', f'/product-insights/{lisi_insight}', token=lisi)
    check('自己建完能打开', res.get('code'), 0)

    status, res = call('GET', f'/custom-inquiries/{inquiry_v2}/history', token=zhangsan)
    check('链条两条', len(res['data']), 2)
    check('按版本升序 v1 在前', res['data'][0]['version'], 1)
    check_true('v1 描述未被改', '烫金' in (res['data'][0]['description'] or ''),
               res['data'][0]['description'] or '')

    # 回归①：待审核案例不再全员可见——列表可见范围与详情一致（仅作者与主管）
    status, res = call('POST', '/cases', token=zhangsan, body={
        'title': f'CHK{RUN}待审核案例',
        'customer_id': zs_customer_id,
        'customer_label': '某代称',
        'key_actions': '待审动作',
        'lessons': '待审做法',
    })
    check('建待审核用例', res.get('code'), 0)
    pending_id = res['data']['id']
    status, res = call('POST', f'/cases/{pending_id}/submit', token=zhangsan, body={})
    check('待审核用例已提交', res.get('code'), 0)
    wangwu = login('wangwu', '123456')
    status, res = call('GET', '/cases', token=wangwu)
    check_true('财务列表看不到待审核案例',
               not any(row['id'] == pending_id for row in res['data']['items']),
               str([row['id'] for row in res['data']['items']][:5]))
    status, res = call('GET', f'/cases/{pending_id}', token=wangwu)
    check('财务详情也被拒（口径一致）', res.get('code'), 40301)
    status, res = call('GET', '/cases', token=lisi)
    check_true('主管列表可见待审核',
               any(row['id'] == pending_id for row in res['data']['items']), '')

    # 回归②：更新改挂数据范围外的客户被拒（创建时校验了，更新此前漏了）
    status, res = call('POST', '/customers', token=admin, body={
        'name': f'CHK{RUN}李四的客户', 'owner_id': other_user['id'],
    })
    check('造李四的客户', res.get('code'), 0)
    lisi_customer_id = res['data']['id']
    status, res = call('PATCH', f'/cases/{pending_id}', token=zhangsan,
                       body={'customer_id': lisi_customer_id})
    check('改挂他人客户被拒', res.get('code'), 40301)
    status, res = call('GET', f'/cases/{pending_id}', token=zhangsan)
    check_true('客户归属未被改动', res['data']['customer_id'] == zs_customer_id,
               str(res['data']['customer_id']))


if __name__ == '__main__':
    async def _driver():
        global SIGN_FILE_ID
        print('=== 清库（跑前）===')
        await clean(verbose=True)
        print()
        # 场景14 需要"已上传的签署件"：直接造一条文件记录（上传接口是 multipart，
        # 这里只关心 sign 端点与附件挂载的链路）
        from app.core.database import SessionLocal
        from app.modules.file.model import FileRecord

        async with SessionLocal() as s:
            record = FileRecord(
                object_key=f'chk/{RUN}/signed.pdf', file_name='signed.pdf',
                mime_type='application/pdf', size=1234, uploaded_by=1,
            )
            s.add(record)
            await s.commit()
            SIGN_FILE_ID = record.id
        print()
        try:
            await main()
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
