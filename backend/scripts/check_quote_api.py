"""报价模块接口回归测试（03-API §20 §21 §22 §34）。

跑法（后端必须先起来，默认 http://127.0.0.1:8000）：
    cd backend
    $env:PYTHONPATH="."
    .venv\\Scripts\\python.exe scripts\\check_quote_api.py

脚本自己会先清**本套件的夹具**再验，跑完再清一次，可反复执行；
**不碰任何非本套件的数据**（见下方 CLEAN_STATEMENTS 的说明）。

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
import os
import json
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get('API_BASE', 'http://127.0.0.1:8000/api/v1')
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
# 清库：跑前跑后各一次，**只清本套件自己造的夹具**
# --------------------------------------------------------------------------
#
# 历史教训（2026-09-30 修）：这里原先是**一条 WHERE 都没有的整表 delete**，
# 跑前跑后各来一遍，等于一次全量回归就把全库的报价/商机/订单/回款/审批/通知/
# 编号计数器清空两次。后果是连锁的：
#   - 种子演示商机被删 → 重跑 `seed.py` 以为"商机不存在"，把整段演示数据又插
#     一遍，跟进与任务越攒越多（实测跟进 12→13、"给宏远包装做周转箱核价"任务 4 条）；
#   - `seed_demo` 的订单号每轮从新开始（1305→1311→1332），业务数据无法留存；
#   - 通知表被清 ⇒ "从未向企微发出过消息"这条自查证据也跟着失效（看不出来了）。
#
# 本套件的夹具**全部**挂在 `CHKQ-quote-*` 商机下（见 quick_opp 调用处），
# 所以按这棵夹具树逐层收敛即可。两条纪律：
#   1. 只认这棵树，不写通配的整表 delete；
#   2. 顺序必须是"先删引用者、再删父表"——下面的锚点子查询引用 quotes /
#      quote_versions / sales_orders，父表一旦先删，后面的子查询就变空了。
_ANCHOR_OPP = "select id from opportunities where title like 'CHKQ-quote-%'"
_ANCHOR_QUOTE = f"select id from quotes where opportunity_id in ({_ANCHOR_OPP})"
_ANCHOR_VERSION = f"select id from quote_versions where quote_id in ({_ANCHOR_QUOTE})"
_ANCHOR_ORDER = f"select id from sales_orders where quote_id in ({_ANCHOR_QUOTE})"

CLEAN_STATEMENTS = [
    ('报价发送记录', f'delete from quote_send_logs where quote_version_id in ({_ANCHOR_VERSION})'),
    ('报价明细', f'delete from quote_items where quote_version_id in ({_ANCHOR_VERSION})'),
    ('报价费用', f'delete from quote_charges where quote_version_id in ({_ANCHOR_VERSION})'),
    # 通知/审计要在父表还在时清，否则锚点查不到（订单同理，见后面几行）
    (
        '通知',
        "delete from notifications where business_type in ('quote','order','sample') "
        f"and business_id in ({_ANCHOR_QUOTE} union {_ANCHOR_VERSION})",
    ),
    (
        '用例审计',
        "delete from audit_logs where business_type in "
        "('quote','quote_version','order','approval','numbering_rule') "
        f"and business_id in ({_ANCHOR_QUOTE} union {_ANCHOR_VERSION})",
    ),
    (
        '订单批次明细',
        'delete from order_shipment_batch_items where batch_id in '
        f"(select id from order_shipment_batches where order_id in ({_ANCHOR_ORDER}))",
    ),
    ('订单批次', f'delete from order_shipment_batches where order_id in ({_ANCHOR_ORDER})'),
    ('订单明细', f'delete from sales_order_items where order_id in ({_ANCHOR_ORDER})'),
    ('回款记录', f'delete from payment_records where order_id in ({_ANCHOR_ORDER})'),
    ('应收计划', f'delete from receivable_plans where order_id in ({_ANCHOR_ORDER})'),
    ('订单状态历史', f'delete from order_status_history where order_id in ({_ANCHOR_ORDER})'),
    ('跟单里程碑', f'delete from order_milestones where order_id in ({_ANCHOR_ORDER})'),
    ('ERP同步日志', f'delete from integration_logs where business_id in ({_ANCHOR_ORDER})'),
    # 注意：external_mappings 的列叫 internal_id（不是 business_id），
    # 写错列名会让整个 clean() 在第一句就抛异常、后面全不清（真踩过）。
    (
        '外部映射',
        "delete from external_mappings where business_type = 'order' "
        f"and internal_id in ({_ANCHOR_ORDER})",
    ),
    (
        '审批记录',
        "delete from approval_records where approval_instance_id in "
        "(select id from approval_instances where business_type='quote_version' "
        f"and business_id in ({_ANCHOR_VERSION}))",
    ),
    (
        '审批实例',
        "delete from approval_instances where business_type='quote_version' "
        f"and business_id in ({_ANCHOR_VERSION})",
    ),
    # 版本 → 订单 → 报价单：父表按这个顺序退场
    ('报价版本', f'delete from quote_versions where quote_id in ({_ANCHOR_QUOTE})'),
    ('订单', f'delete from sales_orders where quote_id in ({_ANCHOR_QUOTE})'),
    ('报价单', f'delete from quotes where opportunity_id in ({_ANCHOR_OPP})'),
    # 六阶段自动留痕只删挂在本套件商机上的（原来是无条件清全表 followup_type='系统'）
    ('自动跟进留痕', f'delete from followups where opportunity_id in ({_ANCHOR_OPP})'),
    ('商机需求明细', f'delete from opportunity_items where opportunity_id in ({_ANCHOR_OPP})'),
    (
        '商机阶段历史',
        f'delete from opportunity_stage_history where opportunity_id in ({_ANCHOR_OPP})',
    ),
    ('商机', "delete from opportunities where title like 'CHKQ-quote-%'"),
    # 刻意**不删** number_sequences：编号计数器是全局递增状态，清掉会让单号
    # 每轮从 1 重来（订单号 1305→1311→1332 就是这么来的）。它不是夹具。
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

    def quick_opp(token, customer_id, title):
        """D8：报价必须挂商机——用例报价前先造一条快捷商机。"""
        status, res = call('POST', '/opportunities', token=token, body={
            'customer_id': customer_id, 'title': title,
        })
        check('建快捷商机', res.get('code'), 0)
        return res['data']['id']

    print()
    print('=== 准备：造一张带明细的报价 ===')
    opp1 = quick_opp(admin, 1, 'CHKQ-quote-主用例')
    status, res = call('POST', '/quotes', token=admin, body={'opportunity_id': opp1})
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

    # D8：无商机的报价直接被拒（商机在此口径下是客户的来源，客户不存在由商机创建侧把关）
    status, res = call('POST', '/quotes', token=admin, body={'customer_id': 999999})
    check('无商机被拒(D8)', res.get('code'), 40001)
    status, res = call(
        'POST', '/quotes', token=admin, body={'opportunity_id': opp1, 'contact_id': 999999}
    )
    check('联系人不存在的被拒', res.get('code'), 40401)
    # 联系人 1 属于客户 1；把它挂到别的客户的商机的报价上应该被拒
    status, res = call('GET', '/contacts/1', token=admin)
    contact1_customer = res['data']['customer_id']
    other_customer = 2 if contact1_customer == 1 else 1
    opp_other = quick_opp(admin, other_customer, 'CHKQ-quote-联系人不匹配')
    status, res = call(
        'POST', '/quotes', token=admin, body={'opportunity_id': opp_other, 'contact_id': 1}
    )
    check('联系人-客户不匹配被拒', res.get('code'), 40001)
    status, res = call('POST', '/quotes', token=admin, body={'opportunity_id': opp1})
    check('挂商机可建', res.get('code'), 0)
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
    opp_lisi = quick_opp(lisi, 1, 'CHKQ-quote-李四审批')
    status, res = call('POST', '/quotes', token=lisi, body={'opportunity_id': opp_lisi})
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

    # A12 收紧后：send-email 也要求版本已通过审批。若该版本还没提交过审批
    # （此前用例路径没走到），这里补一次提交——正常价会直接自动通过。
    call('POST', f'/quote-versions/{version_id}/submit-approval', token=admin, body={})

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
    opp_del = quick_opp(admin, 1, 'CHKQ-quote-删除用例')
    status, res = call('POST', '/quotes', token=admin, body={'opportunity_id': opp_del})
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

    print('=== 14. 版本号唯一约束（并发撞号不再静默重复）===')
    from datetime import UTC, datetime as dt

    from sqlalchemy import select

    import threading

    import asyncpg

    from app.core.config import settings

    # main() 是同步的、外层已在事件循环里，不能 asyncio.run 也不能共用
    # SessionLocal（连接池绑主循环的坑见文件尾注释）——开独立线程自带新循环
    result: dict = {}

    def _insert_dup():
        async def _inner():
            conn = await asyncpg.connect(settings.database_url.replace('+asyncpg', ''))
            try:
                row = await conn.fetchrow(
                    'select quote_id, version_no from quote_versions '
                    'where version_no is not null order by id desc limit 1'
                )
                if row is None:
                    result['checked'] = False
                    return
                result['checked'] = True
                from datetime import UTC, datetime as dt

                try:
                    await conn.execute(
                        'insert into quote_versions (quote_id, version_no, subtotal_amount, '
                        'charge_amount, discount_amount, total_amount, currency, approval_status, '
                        'approval_required, created_by, created_at) '
                        "values ($1, $2, 0, 0, 0, 0, 'CNY', 'not_submitted', false, 1, $3)",
                        row['quote_id'], row['version_no'], dt.now(UTC),
                    )
                    result['blocked'] = False
                except asyncpg.exceptions.UniqueViolationError:
                    result['blocked'] = True
            finally:
                await conn.close()

        asyncio.run(_inner())

    thread = threading.Thread(target=_insert_dup)
    thread.start()
    thread.join()
    check_true('库里有版本可测', result.get('checked') is True, '')
    check_true('同号版本被唯一约束拦下', result.get('blocked') is True, str(result))


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
