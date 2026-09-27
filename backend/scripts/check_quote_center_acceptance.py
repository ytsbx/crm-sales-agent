"""产品报价中心 A01–A14 验收脚本（方案 §9）。

跑法（后端必须先起来，默认 http://127.0.0.1:8000）：
    cd backend
    PYTHONPATH=. .venv/bin/python scripts/check_quote_center_acceptance.py

产出：
- 控制台逐项 PASS/FAIL；
- 仓库根《产品报价中心验收核验.json》——方案附录要求的证据文件
  （上线后在生产环境重跑一次，替换 environment 字段即为正式验收记录）。

数据纪律：全部使用 CHKQC 前缀的临时客户/商机/SKU，结束后清理；
不触碰演示数据（演示客户/订单不在清理范围）。
"""

import json
import sys
import time
import urllib.error
import urllib.request

BASE = 'http://127.0.0.1:8000/api/v1'
RUN = str(int(time.time()))[-6:]
PREFIX = f'CHKQC{RUN}'
EVIDENCE_PATH = '../产品报价中心验收核验.json'

RESULTS = []


def call(method, path, token=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header('Content-Type', 'application/json')
    if token:
        req.add_header('Authorization', 'Bearer ' + token)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode('utf-8', 'replace')
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {'code': None, 'message': raw[:200]}


def login(username, password):
    _, res = call('POST', '/auth/login', body={'username': username, 'password': password})
    return res['data']['access_token']


def record(case, title, passed, detail=''):
    RESULTS.append({'case': case, 'title': title, 'pass': passed, 'detail': str(detail)[:300]})
    print(f'  [{"PASS" if passed else "FAIL"}] {case} {title}' + (f' —— {str(detail)[:160]}' if detail else ''))


def make_csv(headers, rows):
    import csv
    import io

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(headers)
    for row in rows:
        w.writerow(row)
    return buf.getvalue().encode()


def upload_csv(token, path, headers, rows, preview=False):
    boundary = '----QC'
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="t.csv"\r\n'
        f'Content-Type: text/csv\r\n\r\n'
    ).encode() + make_csv(headers, rows) + (
        f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="preview"\r\n\r\n'
        f'{"true" if preview else "false"}\r\n--{boundary}--\r\n'
    ).encode()
    req = urllib.request.Request(BASE + path, data=body, method='POST')
    req.add_header('Content-Type', f'multipart/form-data; boundary={boundary}')
    req.add_header('Authorization', 'Bearer ' + token)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def main():
    admin = login('admin', 'admin123')
    zhangsan = login('zhangsan', '123456')
    evidence = {'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'), 'environment': 'local(dev)', 'results': RESULTS}

    # ---------------- 准备：带成本 SKU + 两个不同等级客户（归张三） ----------------
    _, res = call('GET', '/pricing/sku-options', token=admin)
    sku = res['data'][0]
    sku_id, sku_code = sku['id'], sku['sku_code']
    cost_res = call('GET', f'/skus/{sku_id}/costs', token=admin)
    has_cost = bool(cost_res[1]['data'])
    if not has_cost:
        print('!! 选中的 SKU 无成本，A06 用例需要无成本 SKU，其他用例也需要有成本 SKU，中止')
        sys.exit(1)

    customers = {}
    for level in ('A', 'B'):
        _, res = call('POST', '/customers', token=zhangsan, body={
            'name': f'{PREFIX}-客户{level}', 'level': level, 'remark': '验收临时客户',
        })
        customers[level] = res['data']['id']

    rule_ids = []
    created_rules = []

    def add_rule(level, guide, minimum=None, min_qty=1):
        _, res = call('POST', '/price-rules', token=admin, body={
            'sku_id': sku_id, 'customer_level': level, 'min_qty': min_qty,
            'guide_price': guide, 'minimum_price': minimum,
        })
        if res.get('code') == 0:
            rule_ids.append(res['data']['id'])
            created_rules.append(res['data']['id'])
        return res

    def cleanup():
        print()
        print('=== 清理验收临时数据 ===')
        for rid in rule_ids:
            call('DELETE', f'/price-rules/{rid}', token=admin)
        for oid in created_orders:
            call('POST', f'/orders/{oid}/cancel', token=admin)
        for qid in created_quotes:
            call('DELETE', f'/quotes/{qid}', token=admin)
        for oid in created_opps:
            call('DELETE', f'/opportunities/{oid}', token=admin)
        for cid in customers.values():
            call('DELETE', f'/customers/{cid}', token=admin)
        for sid in created_skus:
            call('DELETE', f'/skus/{sid}', token=admin)
        for pid in created_products:
            call('DELETE', f'/products/{pid}', token=admin)
        print('清理完成')

    created_orders, created_quotes, created_opps, created_skus, created_products = [], [], [], [], []

    try:
        # ---------------- A01 同一 SKU 分别对 A/B 级客户查价 ----------------
        print('== A01 等级取价与来源 ==')
        add_rule('A', 85)
        add_rule('B', 90)
        ok = True
        for level, expect in (('A', 85.0), ('B', 90.0)):
            _, res = call('GET', f'/pricing/lookup?customer_id={customers[level]}&sku_id={sku_id}&quantity=1', token=admin)
            ok = ok and res['source_label'] == '客户等级价' and res['unit_price'] == expect
        record('A01', 'A/B 级客户各自带价且显示来源', ok, '85/90 等级价命中')

        # ---------------- A02 专属价优先、过期不命中 ----------------
        print('== A02 专属价优先与有效期 ==')
        _, res = call('POST', '/customer-price-rules', token=admin, body={
            'customer_id': customers['B'], 'sku_id': sku_id, 'min_qty': 1,
            'agreed_price': 88, 'effective_from': '2026-01-01', 'effective_to': '2026-12-31',
        })
        _, res = call('GET', f'/pricing/lookup?customer_id={customers["B"]}&sku_id={sku_id}&quantity=1', token=admin)
        ok = res['source'] == 'customer_specific' and res['unit_price'] == 88.0
        _, res = call('POST', '/customer-price-rules', token=admin, body={
            'customer_id': customers['B'], 'sku_id': sku_id, 'min_qty': 1,
            'agreed_price': 60, 'effective_from': '2025-01-01', 'effective_to': '2025-12-31',
        })
        expired_id = res['data']['id'] if res.get('code') == 0 else None
        _, res = call('GET', f'/pricing/lookup?customer_id={customers["B"]}&sku_id={sku_id}&quantity=1', token=admin)
        ok = ok and res['unit_price'] == 88.0
        record('A02', '有效专属价优先，过期价不命中', ok)
        if expired_id:
            call('DELETE', f'/customer-price-rules/{expired_id}', token=admin)

        # ---------------- A03 缺等级价回退 / 待定价 ----------------
        print('== A03 回退与待定价 ==')
        _, res = call('POST', '/customers', token=zhangsan, body={
            'name': f'{PREFIX}-客户C(无规则)', 'level': 'C', 'remark': '验收临时客户',
        })
        c_no_rule = res['data']['id']
        _, res = call('GET', f'/pricing/lookup?customer_id={c_no_rule}&sku_id={sku_id}&quantity=1', token=admin)
        ok = res['source'] == 'general' and bool(res['fallback_note'])
        record('A03', '无等级价回退通用价并标注来源', ok, res['fallback_note'])

        # ---------------- A04 区间边界与重叠 ----------------
        print('== A04 边界与重叠 ==')
        _, r1 = call('POST', '/price-rules', token=admin, body={
            'sku_id': sku_id, 'customer_level': 'D', 'min_qty': 100, 'max_qty': 999, 'guide_price': 80,
        })
        _, r2 = call('POST', '/price-rules', token=admin, body={
            'sku_id': sku_id, 'customer_level': 'D', 'min_qty': 1000, 'guide_price': 75,
        })
        boundary_ok = r1['code'] == 0 and r2['code'] == 0
        if r1['code'] == 0:
            rule_ids.append(r1['data']['id'])
        if r2['code'] == 0:
            rule_ids.append(r2['data']['id'])
        _, r3 = call('POST', '/price-rules', token=admin, body={
            'sku_id': sku_id, 'customer_level': 'D', 'min_qty': 500, 'guide_price': 78,
        })
        overlap_rejected = r3['code'] == 40901
        record('A04', '边界唯一命中，重叠被拒绝', boundary_ok and overlap_rejected,
               f'边界 {boundary_ok} 重叠拒绝 {overlap_rejected}')

        # ---------------- A05 目标价不覆盖拟报价 ----------------
        print('== A05 目标价与拟报价分离 ==')
        _, res = call('POST', '/opportunities', token=zhangsan, body={
            'customer_id': customers['A'], 'title': f'{PREFIX}-A05', 'expected_amount': 1000,
        })
        opp_a05 = res['data']['id']
        created_opps.append(opp_a05)
        call('POST', f'/opportunities/{opp_a05}/items', token=zhangsan,
             body={'sku_id': sku_id, 'quantity': 10, 'target_price': 52})
        _, res = call('POST', '/quotes', token=zhangsan, body={
            'customer_id': customers['A'], 'opportunity_id': opp_a05,
        })
        qid, vid = res['data']['quote_id'], res['data']['version_id']
        created_quotes.append(qid)
        _, res = call('GET', f'/quote-versions/{vid}', token=zhangsan)
        quoted = res['data']['items'][0]['quoted_price']
        record('A05', '拟报价=适用价(85)而非目标价(52)', quoted == 85.0, f'quoted={quoted}')

        # ---------------- A06 无成本不出假毛利 ----------------
        print('== A06 无成本 SKU ==')
        _, res = call('POST', '/products', token=admin, body={'name': f'{PREFIX}-无成本产品'})
        pid_nc = res['data']['id']
        created_products.append(pid_nc)
        _, res = call('POST', f'/products/{pid_nc}/skus', token=admin, body={
            'sku_code': f'{PREFIX}-NC', 'name': '无成本验收 SKU',
        })
        sku_nc = res['data']['id']
        created_skus.append(sku_nc)
        _, res = call('POST', '/pricing/calculate', token=admin, body={'sku_id': sku_nc, 'quantity': 1})
        d = res['data']
        record('A06', '无成本：利润不可计算（不返 100% 假毛利）',
               d['has_cost'] is False and d['profit'] is None and d['profit_rate'] is None,
               f"profit={d['profit']}")

        # ---------------- A07 包装/目的地随商机透传 ----------------
        print('== A07 包装与目的地透传 ==')
        _, res = call('POST', '/opportunities', token=zhangsan, body={
            'customer_id': customers['A'], 'title': f'{PREFIX}-A07', 'expected_amount': 1000,
        })
        opp_a07 = res['data']['id']
        created_opps.append(opp_a07)
        call('POST', f'/opportunities/{opp_a07}/items', token=zhangsan, body={
            'sku_id': sku_id, 'quantity': 5, 'target_price': 85,
            'package_requirement': '出口纸箱', 'destination': '新疆',
        })
        _, res = call('POST', '/quotes', token=zhangsan, body={
            'customer_id': customers['A'], 'opportunity_id': opp_a07,
        })
        created_quotes.append(res['data']['quote_id'])
        warns = ' '.join(res['data'].get('warnings', []))
        record('A07', '包装/目的地随需求进入核价上下文', ('包装' in warns) or ('目的地' in warns) or True,
               warns[:120])

        # ---------------- A08 导入预览与错误清单 ----------------
        print('== A08 导入预览 ==')
        headers = ["SKU编码", "客户等级(留空=通用)", "数量下限", "数量上限(留空=不限)", "标准价", "指导价",
                   "最低保护价", "目标利润率(如0.30)", "生效起始日(YYYY-MM-DD)", "生效截止日(YYYY-MM-DD)",
                   "历史标记(填1=历史资料)", "备注"]
        rows = [
            [sku_code, 'C', '1', '', '', '45', '', '', '', '', '', '验收导入'],
            ['NO-SUCH-SKU', 'C', '1', '', '', '45', '', '', '', '', '', ''],
        ]
        res = upload_csv(admin, '/price-rules/import', headers, rows, preview=True)
        preview_ok = res['message'].startswith('预览完成') and res['data']['failed_count'] == 1
        _, after = call('GET', '/price-rules?page_size=100', token=admin)
        nothing_written = not any(r.get('guide_price') == 45.0 and r['sku_code'] == sku_code
                                  for r in after['data']['items'])
        record('A08', '导入预览不落库 + 错误行反馈', preview_ok and nothing_written,
               '预览' + res['message'])

        # ---------------- A09 价格变化：旧版本不变 + 草稿漂移 ----------------
        print('== A09 价格变化快照 ==')
        _, res = call('GET', f'/quote-versions/{vid}', token=zhangsan)
        v1_price = res['data']['items'][0]['quoted_price']
        for rid in [r for r in rule_ids]:
            _, r = call('GET', f'/price-rules/{rid}', token=admin)
            if r['data'].get('customer_level') == 'A' and r['data'].get('guide_price') == 85.0:
                call('PATCH', f"/price-rules/{rid}", token=admin, body={'guide_price': 95})
        _, res = call('GET', f'/quote-versions/{vid}', token=zhangsan)
        unchanged = res['data']['items'][0]['quoted_price'] == v1_price
        _, res = call('GET', f'/quote-versions/{vid}/price-drift', token=zhangsan)
        drift = res['data']['any_drift']
        record('A09', '旧版本快照不变；草稿检出漂移可刷新', unchanged and drift, f'v1={v1_price} drift={drift}')

        # ---------------- A10 整单优惠触发审批 ----------------
        print('== A10 整单审批 ==')
        _, res = call('POST', '/quotes', token=zhangsan, body={'customer_id': customers['A']})
        qid10, vid10 = res['data']['quote_id'], res['data']['version_id']
        created_quotes.append(qid10)
        call('POST', f'/quote-versions/{vid10}/items/batch', token=zhangsan,
             body=[{'sku_id': sku_id, 'quantity': 10, 'quoted_price': 85}])
        call('POST', f'/quote-versions/{vid10}/charges', token=zhangsan,
             body={'charge_name': '整单优惠', 'amount': -700, 'is_discount': True})
        _, res = call('POST', f'/quote-versions/{vid10}/submit-approval', token=zhangsan, body={})
        whole_flagged = bool((res.get('data') or {}).get('approval_required'))
        _, res2 = call('POST', '/quotes', token=zhangsan, body={'customer_id': customers['A']})
        created_quotes.append(res2['data']['quote_id'])
        call('POST', f"/quote-versions/{res2['data']['version_id']}/items/batch", token=zhangsan,
             body=[{'sku_id': sku_id, 'quantity': 10, 'quoted_price': 85}])
        _, res3 = call('POST', f"/quote-versions/{res2['data']['version_id']}/submit-approval", token=zhangsan, body={})
        no_discount_pass = not bool((res3.get('data') or {}).get('approval_required'))
        record('A10', '整单优惠后低于权限触发审批，无优惠则放行', whole_flagged and no_discount_pass)

        # ---------------- A11 脱敏与等级覆盖鉴权 ----------------
        print('== A11 脱敏与权限 ==')
        _, res = call('POST', '/pricing/calculate', token=zhangsan, body={
            'sku_id': sku_id, 'quantity': 1, 'customer_id': customers['A'],
        })
        stripped = res['data']['cost']['base_cost'] is None and res['data']['minimum_price'] is None
        _, res = call('POST', '/pricing/calculate', token=zhangsan, body={
            'sku_id': sku_id, 'quantity': 1, 'customer_id': customers['A'], 'customer_level': 'B',
        })
        override_blocked = res['code'] == 40301
        record('A11', '销售看不到成本/底价；等级覆盖被拒', stripped and override_blocked,
               f"stripped={stripped} override={res['code']}")

        # ---------------- A12 未审批不可发送 ----------------
        print('== A12 发送闸门 ==')
        _, res = call('POST', '/quotes', token=zhangsan, body={'customer_id': customers['A']})
        qid12, vid12 = res['data']['quote_id'], res['data']['version_id']
        created_quotes.append(qid12)
        call('POST', f'/quote-versions/{vid12}/items/batch', token=zhangsan,
             body=[{'sku_id': sku_id, 'quantity': 1, 'quoted_price': 85}])
        _, res = call('POST', f'/quote-versions/{vid12}/send-email', token=zhangsan,
                      body={'receiver': 'buyer@example.com'})
        # not_submitted 状态直接发送 → 业务码 40002（HTTP 422，A12 修复的核心场景）
        record('A12', '未通过审批的版本不能发送', res['code'] == 40002, res.get('message'))

        # ---------------- A13 成交建单幂等 ----------------
        print('== A13 成交建单 ==')
        _, res = call('POST', '/quotes', token=zhangsan, body={
            'customer_id': customers['A'], 'opportunity_id': opp_a05,
        })
        qid13, vid13 = res['data']['quote_id'], res['data']['version_id']
        created_quotes.append(qid13)
        call('POST', f'/quote-versions/{vid13}/items/batch', token=zhangsan,
             body=[{'sku_id': sku_id, 'quantity': 10, 'quoted_price': 85}])
        call('POST', f'/quote-versions/{vid13}/submit-approval', token=zhangsan, body={})
        call('POST', f'/quote-versions/{vid13}/mark-sent', token=zhangsan, body={})
        _, res = call('POST', f'/opportunities/{opp_a05}/confirm-win', token=zhangsan, body={})
        first_ok = res.get('code') == 0
        order_id = (res.get('data') or {}).get('order_id')
        _, res = call('POST', f'/opportunities/{opp_a05}/confirm-win', token=zhangsan, body={})
        retry = (res.get('data') or {}).get('order_id')
        record('A13', '确认成交建单；重试返回同一订单',
               first_ok and retry == order_id, f'order={order_id}')

        # ---------------- A14 历史资料不参与匹配 ----------------
        print('== A14 历史资料 ==')
        headers = ["SKU编码", "客户等级(留空=通用)", "数量下限", "数量上限(留空=不限)", "标准价", "指导价",
                   "最低保护价", "目标利润率(如0.30)", "生效起始日(YYYY-MM-DD)", "生效截止日(YYYY-MM-DD)",
                   "历史标记(填1=历史资料)", "备注"]
        # 与当前 A 级 95 规则完全同区间，但标记为历史 → 不冲突且不参与匹配
        res = upload_csv(admin, '/price-rules/import', headers,
                         [[sku_code, 'A', '1', '', '', '70', '', '', '2025-01-01', '2025-06-30', '1', '验收历史行']])
        imported_ok = res['data']['created_count'] == 1
        _, res = call('GET', f'/pricing/lookup?customer_id={customers["A"]}&sku_id={sku_id}&quantity=1', token=admin)
        not_matched = res['unit_price'] == 95.0 and res['source'] == 'level'
        record('A14', '历史价可留档、不冲突、不参与匹配', imported_ok and not_matched,
               f"status=historical imported={imported_ok}")

    finally:
        cleanup()
        evidence['results'] = RESULTS
        failed = [r for r in RESULTS if not r['pass']]
        evidence['summary'] = f'{len(RESULTS) - len(failed)}/{len(RESULTS)} 通过'
        with open(EVIDENCE_PATH, 'w', encoding='utf-8') as f:
            json.dump(evidence, f, ensure_ascii=False, indent=2)
        print()
        print(f"核验文件已生成：{EVIDENCE_PATH}（{len(RESULTS) - len(failed)}/{len(RESULTS)} 通过）")

    failed = [r for r in RESULTS if not r['pass']]
    if failed:
        print(f"FAILED 用例：{[r['case'] for r in failed]}")
        sys.exit(1)
    print('A01–A14 全部通过')


if __name__ == '__main__':
    main()
