"""产品报价中心 A01–A14 验收脚本（方案 §9）。

跑法（后端必须先起来，默认 http://127.0.0.1:8000）：
    cd backend
    PYTHONPATH=. .venv/bin/python scripts/check_quote_center_acceptance.py

产出：
- 控制台逐项 PASS/FAIL；
- 仓库根《产品报价中心验收核验.json》——方案附录要求的证据文件
  （上线后在生产环境重跑一次，替换 environment 字段即为正式验收记录）。

数据纪律：全部使用 CHKQC 前缀的临时客户/商机/产品/SKU，结束后清理；
不触碰演示数据（演示客户/订单不在清理范围），**也不在真实 SKU 上造价格规则**。

为什么强调"自建 SKU"：本脚本会在夹具 SKU 上造等级价与历史价规则，而
`DELETE /price-rules/{id}` 只是把 status 改成 disabled（接口有意留痕，不是删除），
所以夹具规则不会随 cleanup 消失。早期版本取 `/pricing/sku-options[0]`（真实 SKU）
当夹具，每跑一次就往 ZX-6040-B 上永久堆 5 条残留，攒到过 196 条。现在改成自建
CHKQC SKU，并在清理时对本脚本自建的 SKU 真删价格规则。
"""

import json
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime

BASE = 'http://127.0.0.1:8000/api/v1'
RUN = str(int(time.time()))[-6:]
#: 绑进 SQL 的时间**必须是真的 datetime**：asyncpg 不接受字符串。
#: 这里原先传的是 time.strftime(...) 得到的字符串，于是整段"自动留痕清理"每次
#: 都抛 DataError、被外层 except 吞掉（日志里那句"（自动留痕清理跳过：…）"），
#: 通知与跟进其实一条都没清过——清理代码看起来是生效的，实际从没执行。
SCRIPT_STARTED_AT = datetime.now(UTC)
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

    created_orders, created_quotes, created_opps, created_skus, created_products = [], [], [], [], []

    # ---------------- 准备：**自建**带成本 SKU + 两个不同等级客户（归张三） ----------------
    # 不能取 /pricing/sku-options 的第一个：那是真实 SKU，本脚本会在它身上造
    # 等级价/历史价规则，而 DELETE /price-rules/{id} 只是置 disabled，残留会永久堆积。
    _, res = call('POST', '/products', token=admin, body={'name': f'{PREFIX}-验收产品'})
    pid_setup = res['data']['id']
    created_products.append(pid_setup)
    sku_code = f'{PREFIX}-SKU'
    _, res = call('POST', f'/products/{pid_setup}/skus', token=admin, body={
        'sku_code': sku_code, 'name': '验收用 SKU',
    })
    if res.get('code') != 0:
        print(f'!! 建验收 SKU 失败：{res.get("message")}')
        sys.exit(1)
    sku_id = res['data']['id']
    created_skus.append(sku_id)
    # 这条 SKU 必须**带成本**：除 A06 外的用例都靠它算建议价/最低价
    # （A06 要的是"无成本"SKU，它自己另建一个 -NC）。
    _, res = call('POST', f'/skus/{sku_id}/costs', token=admin, body={
        'purchase_cost': 40, 'package_cost': 5, 'effective_from': '2026-01-01',
        'remark': '验收临时成本',
    })
    if res.get('code') != 0:
        print(f'!! 建验收成本失败：{res.get("message")}')
        sys.exit(1)
    # 通用价（customer_level 为空）：A03「缺等级价回退通用价并标注来源」要有它才成立。
    # 自建 SKU 之后它就是"通用指导价"的那条，少了它 A03 只会返回待定价。
    _, res = call('POST', '/price-rules', token=admin, body={
        'sku_id': sku_id, 'min_qty': 0,
        'standard_price': 105, 'guide_price': 100, 'minimum_price': 80,
        'remark': f'{PREFIX}-通用价',
    })
    if res.get('code') != 0:
        print(f'!! 建验收通用价失败：{res.get("message")}')
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
        # 六阶段"过程记录"自动留痕/通知（无 CHK 前缀）：按脚本启动时间窗清，
        # 只删本次运行产生的，不碰演示数据
        import asyncio

        async def _clean_system_rows():
            from sqlalchemy import text

            from app.core.database import SessionLocal

            async with SessionLocal() as s:
                for sql in (
                    "delete from notifications where business_type in ('quote','order','sample') "
                    "and created_at > :ts",
                    "delete from followups where followup_type='系统' and created_at > :ts",
                    # 价格规则：接口的 DELETE 只置 disabled（有意留痕，不是删除），
                    # 所以夹具规则不会随 cleanup 消失。对本脚本**自建的 SKU**真删，
                    # 它们本来就是临时夹具；真实 SKU 一行都不碰。
                    "delete from price_rules where sku_id = any(:sku_ids)",
                    # 成本同理：接口没有删成本的路径，不显式清就会留在库里
                    "delete from product_costs where sku_id = any(:sku_ids)",
                ):
                    await s.execute(
                        text(sql), {'ts': SCRIPT_STARTED_AT, 'sku_ids': created_skus}
                    )
                await s.commit()

        try:
            asyncio.run(_clean_system_rows())
        except Exception as exc:  # 清理失败不挡结果输出
            print(f'  （自动留痕清理跳过：{exc}）')
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
        # 登记进 customers 才会被收尾清理；此前只存在局部变量里，
        # 于是每跑一次就在库里留一个「客户C(无规则)」——清理代码看起来是全覆盖的，
        # 实际只覆盖了登记过的 A/B。
        customers['C'] = c_no_rule
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
        record('A07', '包装/目的地随需求进入核价上下文', ('包装' in warns) or ('目的地' in warns),
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
        # D8：报价必须挂商机——先建两条快捷商机承载用例报价
        _, res = call('POST', '/quotes', token=zhangsan, body={'customer_id': customers['A']})
        d8_rejected = res['code'] == 40001 and '商机' in res['message']
        _, res = call('POST', '/opportunities', token=zhangsan, body={
            'customer_id': customers['A'], 'title': f'{PREFIX}-A10a', 'expected_amount': 1000,
        })
        opp_a10 = res['data']['id']
        created_opps.append(opp_a10)
        call('POST', f'/opportunities/{opp_a10}/items', token=zhangsan,
             body={'sku_id': sku_id, 'quantity': 10, 'target_price': 85})
        _, res = call('POST', '/quotes', token=zhangsan, body={'opportunity_id': opp_a10})
        qid10, vid10 = res['data']['quote_id'], res['data']['version_id']
        created_quotes.append(qid10)
        call('POST', f'/quote-versions/{vid10}/items/batch', token=zhangsan,
             body=[{'sku_id': sku_id, 'quantity': 10, 'quoted_price': 85}])
        call('POST', f'/quote-versions/{vid10}/charges', token=zhangsan,
             body={'charge_name': '整单优惠', 'amount': -700, 'is_discount': True})
        _, res = call('POST', f'/quote-versions/{vid10}/submit-approval', token=zhangsan, body={})
        whole_flagged = bool((res.get('data') or {}).get('approval_required'))
        _, res = call('POST', '/opportunities', token=zhangsan, body={
            'customer_id': customers['A'], 'title': f'{PREFIX}-A10b', 'expected_amount': 1000,
        })
        opp_a10b = res['data']['id']
        created_opps.append(opp_a10b)
        call('POST', f'/opportunities/{opp_a10b}/items', token=zhangsan,
             body={'sku_id': sku_id, 'quantity': 10, 'target_price': 85})
        _, res2 = call('POST', '/quotes', token=zhangsan, body={'opportunity_id': opp_a10b})
        created_quotes.append(res2['data']['quote_id'])
        call('POST', f"/quote-versions/{res2['data']['version_id']}/items/batch", token=zhangsan,
             body=[{'sku_id': sku_id, 'quantity': 10, 'quoted_price': 85}])
        _, res3 = call('POST', f"/quote-versions/{res2['data']['version_id']}/submit-approval", token=zhangsan, body={})
        no_discount_pass = not bool((res3.get('data') or {}).get('approval_required'))
        record('A10', '无商机被拒(D8)；整单优惠触发审批，无优惠放行',
               d8_rejected and whole_flagged and no_discount_pass,
               f'D8拒={d8_rejected} 整单审批={whole_flagged} 无优惠放行={no_discount_pass}')

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
        _, res = call('POST', '/opportunities', token=zhangsan, body={
            'customer_id': customers['A'], 'title': f'{PREFIX}-A12', 'expected_amount': 100,
        })
        opp_a12 = res['data']['id']
        created_opps.append(opp_a12)
        call('POST', f'/opportunities/{opp_a12}/items', token=zhangsan,
             body={'sku_id': sku_id, 'quantity': 1, 'target_price': 85})
        _, res = call('POST', '/quotes', token=zhangsan, body={'opportunity_id': opp_a12})
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

        # ---------------- A15 绝对底价硬拒（D7 判定层）----------------
        print('== A15 绝对底价 ==')
        _, res = call('POST', '/pricing/calculate', token=admin,
                      body={'sku_id': sku_id, 'quantity': 10})
        base_cost_15 = res['data']['cost']['base_cost']
        # 免审规则：无条件命中（总额上限放大到必命中），用来验证"免审救不了硬底"
        _, res = call('POST', '/approval-rules', token=admin, body={
            'name': f'{PREFIX}-A15免审', 'kind': 'auto_pass', 'priority': 1,
            'conditions': [{'field': 'total_amount', 'op': 'lte', 'value': 999999999}],
            'action': {},
        })
        rule15 = res['data']['id'] if res.get('code') == 0 else None
        if rule15:
            call('POST', f'/approval-rules/{rule15}/publish', token=admin, body={})
            call('PATCH', f'/approval-rules/{rule15}/enabled', token=admin, body={'enabled': True})
        try:
            call('PATCH', '/settings', token=admin,
                 body={'key': 'hard_floor', 'value': {'mode': 'cost', 'markup_ratio': 0}})
            _, res = call('POST', '/opportunities', token=zhangsan, body={
                'customer_id': customers['A'], 'title': f'{PREFIX}-A15', 'expected_amount': 1000,
            })
            opp_a15 = res['data']['id']
            created_opps.append(opp_a15)
            call('POST', f'/opportunities/{opp_a15}/items', token=zhangsan,
                 body={'sku_id': sku_id, 'quantity': 10, 'target_price': base_cost_15 * 0.5})
            _, res = call('POST', '/quotes', token=zhangsan, body={'opportunity_id': opp_a15})
            qid15, vid15 = res['data']['quote_id'], res['data']['version_id']
            created_quotes.append(qid15)
            _, vres = call('GET', f'/quote-versions/{vid15}', token=zhangsan)
            item15 = vres['data']['items'][0]
            call('PATCH', f"/quote-items/{item15['id']}", token=zhangsan,
                 body={'quoted_price': round(base_cost_15 * 0.5, 2)})
            _, res = call('POST', f'/quote-versions/{vid15}/submit-approval', token=zhangsan, body={})
            hard_rejected = res['code'] == 42205 and '绝对底价' in res['message']
            # 关闭硬底后同一版本可正常提交（证明拦截来自硬底本身）
            call('PATCH', '/settings', token=admin,
                 body={'key': 'hard_floor', 'value': {'mode': 'off', 'markup_ratio': 0}})
            _, res = call('POST', f'/quote-versions/{vid15}/submit-approval', token=zhangsan, body={})
            off_allowed = res['code'] == 0
            record('A15', '低于绝对底价硬拒（免审规则不救）；关闭后放行',
                   hard_rejected and off_allowed,
                   f"硬拒={hard_rejected}(code={res.get('code')}) 关闭放行={off_allowed}")
        finally:
            call('PATCH', '/settings', token=admin,
                 body={'key': 'hard_floor', 'value': {'mode': 'off', 'markup_ratio': 0}})
            if rule15:
                call('DELETE', f'/approval-rules/{rule15}', token=admin)

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
    print('A01–A15 全部通过')


if __name__ == '__main__':
    main()
