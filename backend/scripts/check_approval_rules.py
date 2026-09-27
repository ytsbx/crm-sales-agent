"""审批规则引擎回归测试（设计稿 `_6` 的国内业务版）。

跑法（后端必须先起来，默认 http://127.0.0.1:8000）：
    cd backend
    PYTHONPATH=. .venv/bin/python scripts/check_approval_rules.py

脚本自带清库，可反复执行。不依赖 DEEPSEEK_API_KEY。

## 覆盖

1. 条件求值纯函数（gte/lte/eq/in、字段缺失、边界相等）——离线可验；
2. 规则 CRUD + 发布 + 版本：草稿不生效、发布才生效、发布两次出两个版本、
   未发布不能启用、has_draft_changes 标记；
3. 沙盒试算：上下文字段齐全、逐条规则给出命中明细；
4. 三类路由端到端（用 zhangsan 提交、lisi 主管批、wangwu 财务会签）：
   - 免审：高毛利 + 小额 + 无逾期 + 破保护价 → 提交即通过（auto_passed）；
   - 异常加签：低毛利（<22%）→ 主管通过后进入财务会签；财务否决 → 一票否决；
   - 极速通道：让价 ≤1000 + 毛利 ≥22%（借保护价触发审批）→ 落到第一级主管，一步批完。

## 价格构造原理

salesperson 授权利润率 15%。要让"高毛利"的单子也进审批流，用一条
`price_rules` 保护价（minimum_price 高于报价）触发 offending——这正是
免审规则的实战场景：价格破了保护价、但毛利其实很好。
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


def call(method, path, token=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header('Content-Type', 'application/json')
    if token:
        req.add_header('Authorization', 'Bearer ' + token)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        text = e.read().decode('utf-8', 'replace')
        try:
            return e.code, json.loads(text)
        except json.JSONDecodeError:
            return e.code, {'code': None, 'message': text[:200]}


def login(username, password):
    return call('POST', '/auth/login', body={'username': username, 'password': password})[1]['data']['access_token']


# ---------------------------------------------------------------- 第 1 节：纯函数（不碰库）

def test_evaluate_conditions():
    from app.modules.approval import rules_engine

    print()
    print('=== 1. 条件求值纯函数 ===')
    ctx = {'gross_margin': 26.5, 'total_amount': 80000, 'customer_level': 'A',
           'customer_has_overdue': False}
    hit, detail = rules_engine.evaluate_conditions(
        [{'field': 'gross_margin', 'op': 'gte', 'value': 25},
         {'field': 'total_amount', 'op': 'lte', 'value': 100000}], ctx)
    check('全中', hit, True)
    check('明细条数', len(detail), 2)
    check('边界相等算命中', rules_engine.evaluate_conditions(
        [{'field': 'gross_margin', 'op': 'gte', 'value': 26.5}], ctx)[0], True)
    check('一条未中即未中', rules_engine.evaluate_conditions(
        [{'field': 'gross_margin', 'op': 'gte', 'value': 25},
         {'field': 'total_amount', 'op': 'lte', 'value': 50000}], ctx)[0], False)
    check('in 操作符', rules_engine.evaluate_conditions(
        [{'field': 'customer_level', 'op': 'in', 'value': ['A', 'B']}], ctx)[0], True)
    check('bool 相等', rules_engine.evaluate_conditions(
        [{'field': 'customer_has_overdue', 'op': 'eq', 'value': False}], ctx)[0], True)
    miss, miss_detail = rules_engine.evaluate_conditions(
        [{'field': 'concession_amount', 'op': 'lte', 'value': 1000}], ctx)
    check('缺字段视为未命中', miss, False)
    check('缺字段明细带说明', '视为未命中' in (miss_detail[0].get('note') or ''), True)
    check('未知字段未命中', rules_engine.evaluate_conditions(
        [{'field': 'not_a_field', 'op': 'gte', 'value': 1}], ctx)[0], False)
    check('账期识别：账期60天', rules_engine.payment_terms_days('账期60天，月结'), 60)
    check('账期识别：无数字', rules_engine.payment_terms_days('款到发货'), 0)


# ---------------------------------------------------------------- 清库

async def clean(verbose=False):
    from sqlalchemy import text

    from app.core.database import SessionLocal

    statements = [
        ('审批记录', "delete from approval_records where approval_instance_id in "
                 "(select id from approval_instances where business_id in "
                 "(select id from quote_versions where quote_id in "
                 f"(select id from quotes where customer_id in (select id from customers where name like 'CHK{RUN}%'))))"),
        ('审批实例', "delete from approval_instances where business_id in "
                 "(select id from quote_versions where quote_id in "
                 f"(select id from quotes where customer_id in (select id from customers where name like 'CHK{RUN}%')))"),
        ('报价明细', "delete from quote_items where quote_version_id in "
                 "(select id from quote_versions where quote_id in "
                 f"(select id from quotes where customer_id in (select id from customers where name like 'CHK{RUN}%')))"),
        ('报价版本', "delete from quote_versions where quote_id in "
                 f"(select id from quotes where customer_id in (select id from customers where name like 'CHK{RUN}%'))"),
        ('报价单', f"delete from quotes where customer_id in (select id from customers where name like 'CHK{RUN}%')"),
        ('客户特殊价', f"delete from customer_price_rules where remark = 'CHK{RUN}保护价'"),
        ('测试成本', f"delete from product_costs where remark = 'CHK{RUN}成本'"),
        # 规则一律按 CHK 前缀清（都是测试产物，不可能是业务数据）；
        # 之前按时间戳后缀清，中途崩掉的运行会留下别的后缀的残留
        ('测试规则版本', "delete from approval_rule_versions where rule_id in "
                     "(select id from approval_rules where name like 'CHK%')"),
        ('测试规则', "delete from approval_rules where name like 'CHK%'"),
        ('通知', f"delete from notifications where title like '%CHK{RUN}%' or content like '%CHK{RUN}%'"),
        ('客户归属历史', f"delete from customer_owner_history where customer_id in (select id from customers where name like 'CHK{RUN}%')"),
        ('客户', f"delete from customers where name like 'CHK{RUN}%'"),
    ]
    async with SessionLocal() as s:
        for label, sql in statements:
            result = await s.execute(text(sql))
            if verbose and result.rowcount:
                print(f'  {result.rowcount:>4}  {label}')
        await s.commit()


# ---------------------------------------------------------------- 主流程

async def main():
    admin = login('admin', 'admin123')
    zhangsan = login('zhangsan', '123456')
    lisi = login('lisi', '123456')
    wangwu = login('wangwu', '123456')

    test_evaluate_conditions()

    print()
    print('=== 2. 规则目录 / 列表 / 沙盒 ===')
    status, res = call('GET', '/approval-rules/condition-fields', token=admin)
    check('条件字段目录', res.get('code'), 0)
    fields = {f['field'] for f in res['data']}
    check_true('字段目录齐', {'total_amount', 'gross_margin', 'customer_level',
                          'customer_has_overdue', 'concession_amount'} <= fields, str(fields))

    status, res = call('GET', '/approval-rules', token=admin)
    check('规则列表', res.get('code'), 0)
    seeded = {r['name']: r for r in res['data']}
    check_true('三条种子规则在', {'高毛利小额自动免审', '小额让价极速通道', '低毛利强制财务会签'} <= set(seeded),
               str(list(seeded)))
    check_true('种子规则已发布启用',
               all(seeded[n]['enabled'] and seeded[n]['published_version_no'] >= 1 for n in seeded), '')

    # 张三无 settings:manage，不能改规则；但能看（沙盒要用）
    status, res = call('POST', '/approval-rules', token=zhangsan,
                       body={'name': f'CHK{RUN}越权', 'kind': 'auto_pass', 'conditions': [
                           {'field': 'gross_margin', 'op': 'gte', 'value': 1}]})
    check('无 settings:manage 不能建规则', res.get('code'), 40301)

    print()
    print('=== 3. 规则 CRUD / 发布 / 版本 ===')
    status, res = call('POST', '/approval-rules', token=admin, body={
        'name': f'CHK{RUN}测试规则', 'kind': 'auto_pass', 'priority': 99,
        'conditions': [{'field': 'gross_margin', 'op': 'gte', 'value': 10}],
        'description': '回归测试用'})
    check('建草稿', res.get('code'), 0)
    rule_id = res['data']['id']
    check('草稿未发布', res['data']['published_version_no'], 0)

    status, res = call('PATCH', f'/approval-rules/{rule_id}/enabled', token=admin, body={'enabled': True})
    check('未发布不能启用', res.get('code'), 40002)

    status, res = call('POST', f'/approval-rules/{rule_id}/publish', token=admin)
    check('发布 V1', res['data']['published_version_no'], 1)
    status, res = call('PATCH', f'/approval-rules/{rule_id}', token=admin, body={
        'name': f'CHK{RUN}测试规则', 'kind': 'auto_pass', 'priority': 98,
        'conditions': [{'field': 'gross_margin', 'op': 'gte', 'value': 20}]})
    check_true('改草稿后标记有变更', res['data']['has_draft_changes'] is True, '')
    status, res = call('POST', f'/approval-rules/{rule_id}/publish', token=admin)
    check('发布 V2', res['data']['published_version_no'], 2)
    status, res = call('GET', f'/approval-rules/{rule_id}/versions', token=admin)
    check('版本历史两条', len(res['data']), 2)
    check_true('当前版本标记', res['data'][0]['is_current'] is True, '')

    status, res = call('POST', '/approval-rules', token=admin, body={
        'name': f'CHK{RUN}坏规则', 'kind': 'no_such_kind',
        'conditions': [{'field': 'gross_margin', 'op': 'gte', 'value': 1}]})
    check('未知 kind 被拒', res.get('code'), 40001)
    status, res = call('POST', '/approval-rules', token=admin, body={
        'name': f'CHK{RUN}坏条件', 'kind': 'auto_pass',
        'conditions': [{'field': 'gross_margin', 'op': 'in', 'value': 1}]})
    check('字段不支持的操作符被拒', res.get('code'), 40001)

    status, res = call('DELETE', f'/approval-rules/{rule_id}', token=admin)
    check('删除测试规则', res.get('code'), 0)

    # ------------------------------------------------ 准备报价材料
    print()
    print('=== 4. 造报价材料（客户/保护价/三张单）===')
    status, res = call('POST', '/customers', token=admin,
                       body={'name': f'CHK{RUN}审批规则客户', 'level': 'A', 'region': '浙江'})
    check('建 A 级客户', res.get('code'), 0)
    customer_id = res['data']['id']

    # 客户转移给张三：报价以张三身份创建，必须他自己可见
    status, res = call('GET', '/users?keyword=zhangsan', token=admin)
    zhangsan_id = res['data']['items'][0]['id']
    status, res = call('POST', f'/customers/{customer_id}/transfer', token=admin,
                       body={'to_user_id': zhangsan_id})
    check('客户转移给张三', res.get('code'), 0)

    status, res = call('GET', '/skus?page_size=1', token=admin)
    sku = res['data']['items'][0]
    sku_id = sku['id']
    check_true('有测试 SKU', bool(sku_id), str(sku.get('sku_code') or sku.get('code') or sku_id))
    # 给测试 SKU 自建成本：其他回归套件会清产品成本，不能假设第一个 SKU 有成本。
    # 成本 10 元/件 → 场景毛利可控（单 A 100 元≈89%，单 B 11 元≈-19%，单 C 100 元≈89%）。
    from datetime import date as _date

    status, res = call('POST', f'/skus/{sku_id}/costs', token=admin, body={
        'purchase_cost': 10, 'effective_from': str(_date.today()),
        'remark': f'CHK{RUN}成本'})
    check('给测试 SKU 建成本', res.get('code'), 0)

    # 保护价走**客户特殊价**：对本测试客户独占，不会被种子的 SKU 价格规则遮蔽。
    # 报价远低于 99999 → 高毛利单也"破保护价"而进审批（免审/极速的实战触发场景）。
    status, res = call('POST', '/customer-price-rules', token=admin, body={
        'customer_id': customer_id, 'sku_id': sku_id,
        'agreed_price': 100, 'minimum_price': 99999, 'remark': f'CHK{RUN}保护价'})
    check('设客户保护价', res.get('code'), 0)
    price_rule_id = res['data']['id'] if res.get('code') == 0 else None

    def make_quote(price, qty):
        """建报价：先建空单（客户路径），再整版替换明细（显式报价价）。"""
        status, res = call('POST', '/quotes', token=zhangsan, body={'customer_id': customer_id})
        if res.get('code') != 0:
            return None, res
        quote_id, version_id = res['data']['quote_id'], res['data']['version_id']
        status, res = call('POST', f'/quote-versions/{version_id}/items/batch', token=zhangsan,
                           body=[{'sku_id': sku_id, 'quantity': qty, 'quoted_price': price}])
        if res.get('code') != 0:
            return None, res
        return {'quote_id': quote_id, 'version_id': version_id}, res

    # 单 A：高毛利（约 60%）小额 → 免审；单价 100 高于成本、但破 99999 保护价
    quote_a, res_a = make_quote(100, 50)
    check_true('单 A 建好（高毛利小额）', quote_a is not None, str(res_a)[:120])
    # 单 B：低毛利（约 10%）→ 异常加签；单价 11（成本约 10）
    quote_b, res_b = make_quote(11, 100)
    check_true('单 B 建好（低毛利）', quote_b is not None, str(res_b)[:120])
    # 单 C：毛利 ≥22%（约 66%）+ 小让价 + 大额 12 万（超出免审规则的 10 万上限）
    # → 免审不中、极速通道命中；金额 >5 万也验证"跳过高层级"
    quote_c, res_c = make_quote(100, 1200)
    check_true('单 C 建好（大额高毛利）', quote_c is not None, str(res_c)[:120])

    def submit_and_fetch(version_id, reason):
        status, res = call('POST', f'/quote-versions/{version_id}/submit-approval',
                           token=zhangsan, body={'reason': reason})
        return res

    # ------------------------------------------------ 沙盒
    print()
    print('=== 5. 沙盒试算（单 B：低毛利）===')
    version_b = quote_b['version_id']
    check_true('单 B 有版本', version_b is not None, str(quote_b)[:100])
    status, res = call('POST', '/approval-rules/sandbox', token=admin,
                       body={'quote_version_id': version_b})
    check('沙盒可跑', res.get('code'), 0)
    sandbox_b = res['data']
    check_true('上下文带综合毛利率', 'gross_margin' in sandbox_b['context'],
               str(sandbox_b['context'])[:160])
    check_true('逐条规则有命中明细', all('conditions_detail' in r for r in sandbox_b['rules']), '')
    check('单 B 命中的是会签规则',
          next((r['name'] for r in sandbox_b['rules'] if r['fired']), None), '低毛利强制财务会签')

    # ------------------------------------------------ 场景 A：免审
    print()
    print('=== 6. 场景 A：免审（提交即通过）===')
    version_a = quote_a['version_id']
    check_true('单 A 有版本', version_a is not None, str(quote_a)[:100])
    res_a = submit_and_fetch(version_a, 'CHK 回归：免审')
    check('提交接口通', res_a.get('code'), 0)
    if res_a.get('code') != 0:
        print('   提交返回：', str(res_a)[:300])
    check('标记自动通过', res_a['data'].get('auto_passed'), True)
    check_true('消息说明免审规则', '免审' in res_a.get('message', ''), res_a.get('message', ''))
    status, res = call('GET', f'/quote-versions/{version_a}', token=zhangsan)
    check('版本已通过', res['data']['version']['approval_status'], 'approved')
    approval_a = res_a['data'].get('approval_id')
    status, res = call('GET', f'/approvals/{approval_a}', token=admin)
    check('闭环审批单状态', res['data']['status'], 'approved')
    check_true('留痕含规则名', '高毛利小额自动免审' in json.dumps(res['data']['summary'], ensure_ascii=False), '')
    check_true('记录有 auto_pass 动作', any(r['action'] == 'auto_pass' for r in res['data']['records']), '')

    # ------------------------------------------------ 场景 B：异常加签（两步 + 否决）
    print()
    print('=== 7. 场景 B：异常加签（主管 → 财务会签）===')
    res_b = submit_and_fetch(version_b, 'CHK 回归：会签')
    check('提交接口通', res_b.get('code'), 0)
    if res_b.get('code') != 0:
        print('   提交返回：', str(res_b)[:300])
    approval_b = res_b['data']['approval_id']
    status, res = call('GET', f'/approvals/{approval_b}', token=admin)
    check_true('带规则痕迹', res['data']['summary'].get('rule_trace', {}).get('kind') == 'exception_route',
               str(res['data']['summary'].get('rule_trace'))[:120])
    check_true('带会签配置', res['data']['summary'].get('co_sign', {}).get('role_codes') == ['finance'], '')

    # 财务不能批业务节点
    status, res = call('POST', f'/approvals/{approval_b}/approve', token=wangwu)
    check('财务不能批业务节点', res.get('code'), 40301)
    # 张三不能批自己的
    status, res = call('POST', f'/approvals/{approval_b}/approve', token=zhangsan)
    check('不能批自己提交的', res.get('code'), 40301)
    # 主管通过本级
    status, res = call('POST', f'/approvals/{approval_b}/approve', token=lisi)
    check('主管通过本级', res.get('code'), 0)
    if res.get('code') != 0:
        print('   主管通过返回：', str(res)[:300])
    check('进入会签节点', res['data'].get('current_node'), 'co_sign')
    status, res = call('GET', f'/approvals/{approval_b}', token=admin)
    check('仍在待审批', res['data']['status'], 'pending')
    check('当前节点', res['data']['current_node'], 'co_sign')
    # 财务否决（一票否决）
    from urllib.parse import quote as urlquote

    status, res = call('POST', f'/approvals/{approval_b}/reject?comment={urlquote("毛利太低")}', token=wangwu)
    check('财务会签否决', res.get('code'), 0)
    status, res = call('GET', f'/approvals/{approval_b}', token=admin)
    check('整单被拒', res['data']['status'], 'rejected')
    check_true('记录写明会签节点否决',
               any(r['node_code'] == 'co_sign' and r['action'] == 'reject' for r in res['data']['records']), '')

    # 再造一单走完整通过路径
    quote_b2, _ = make_quote(11, 80)
    version_b2 = quote_b2['version_id']
    res_b2 = submit_and_fetch(version_b2, 'CHK 回归：会签通过')
    approval_b2 = res_b2['data']['approval_id']
    status, res = call('POST', f'/approvals/{approval_b2}/approve', token=lisi)
    check('主管通过', res.get('code'), 0)
    status, res = call('POST', f'/approvals/{approval_b2}/approve', token=wangwu)
    check('财务会签通过', res.get('code'), 0)
    status, res = call('GET', f'/approvals/{approval_b2}', token=admin)
    check('整单通过', res['data']['status'], 'approved')
    status, res = call('GET', f'/quote-versions/{version_b2}', token=zhangsan)
    check('版本可发送', res['data']['version']['approval_status'], 'approved')

    # ------------------------------------------------ 场景 C：极速通道
    print()
    print('=== 8. 场景 C：极速通道（跳过高层级，主管一步批完）===')
    version_c = quote_c['version_id']
    check_true('单 C 有版本', version_c is not None, str(quote_c)[:100])
    res_c = submit_and_fetch(version_c, 'CHK 回归：极速')
    check('提交接口通', res_c.get('code'), 0)
    approval_c = res_c['data']['approval_id']
    status, res = call('GET', f'/approvals/{approval_c}', token=admin)
    trace = res['data']['summary'].get('rule_trace', {})
    check_true('命中的是极速规则', trace.get('kind') == 'express', str(trace)[:120])
    check_true('没有会签节点', res['data']['summary'].get('co_sign') is None, '')
    check_true('落在第一级（主管）', res['data']['summary'].get('node_role_codes') == ['sales_manager'],
               str(res['data']['summary'].get('node_role_codes')))
    status, res = call('POST', f'/approvals/{approval_c}/approve', token=lisi)
    check('主管一步批完（无会签）', res.get('code'), 0)
    status, res = call('GET', f'/approvals/{approval_c}', token=admin)
    check('整单通过且无会签记录', res['data']['status'], 'approved')
    check_true('记录里没有会签节点',
               all(r.get('node_code') != 'co_sign' for r in res['data']['records']), '')

    # ------------------------------------------------ 清理保护价
    if price_rule_id:
        status, res = call('DELETE', f'/customer-price-rules/{price_rule_id}', token=admin)
        check('清理客户保护价', res.get('code'), 0)


if __name__ == '__main__':
    async def _driver():
        print('=== 清库（跑前）===')
        await clean(verbose=True)
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
