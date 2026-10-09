"""产品/SKU 导入导出 + 产品附件 + 核价权限/模拟/历史 + 报价跟进 回归测试。

跑法（后端必须先起来，默认 http://127.0.0.1:8000）：
    cd backend
    $env:PYTHONPATH="."
    .venv\\Scripts\\python.exe scripts\\check_product_pricing_api.py

脚本自带清库，可反复执行。

## 覆盖

产品/价格（03-API §14 §15 §17 §18）：
  GET    /products/import-template、/skus/import-template
  GET|POST /products/export、/skus/export
  POST   /products/import、/skus/import
  POST   /products/{id}/files
  POST   /pricing/check-permission
  POST   /pricing/simulate
  GET    /pricing/history
  PATCH  /customer-price-rules/{id}

报价（03-API §20）：
  GET /quotes/{id}/followups

## 重点

`check-permission` 与报价明细必须用同一套判定（`calculate_price`）——
两处各写一遍必然漂移：核价说能报、报价单说不能，业务就不信系统了。
测试里同时打 `/pricing/check-permission` 和 `/pricing/calculate`，
断言两者的 `approval_required` / `minimum_price` 一致。
"""

import asyncio
import csv
import io
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from _test_support import require_api_base, require_isolated_db

require_isolated_db()

BASE = require_api_base()
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


def call(method, path, token=None, body=None, raw_body=None, content_type='application/json'):
    data = raw_body if raw_body is not None else (
        json.dumps(body).encode() if body is not None else None
    )
    safe_path = urllib.parse.quote(path, safe='/?&=%')
    req = urllib.request.Request(BASE + safe_path, data=data, method=method)
    if data is not None:
        req.add_header('Content-Type', content_type)
    if token:
        req.add_header('Authorization', 'Bearer ' + token)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            ctype = resp.headers.get('content-type', '')
            if 'json' not in ctype:
                return resp.status, {
                    '_text': raw.decode('utf-8-sig', 'replace'),
                    '_ctype': ctype,
                    '_disposition': resp.headers.get('content-disposition', ''),
                }
            return resp.status, json.loads(raw.decode())
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


def csv_bytes(rows, headers):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    writer.writerows(rows)
    return buf.getvalue().encode('utf-8-sig')


def upload_csv(token, path, headers, rows):
    boundary = '----crmchk' + RUN
    body = io.BytesIO()
    body.write(f'--{boundary}\r\n'.encode())
    body.write(
        b'Content-Disposition: form-data; name="file"; filename="t.csv"\r\n'
        b'Content-Type: text/csv\r\n\r\n'
    )
    body.write(csv_bytes(rows, headers))
    body.write(f'\r\n--{boundary}--\r\n'.encode())
    return call(
        'POST', path, token=token, raw_body=body.getvalue(),
        content_type=f'multipart/form-data; boundary={boundary}',
    )


def _run_sql(sql_text, params=None, *, fetch: str | None = None):
    """在**独立线程 + 独立引擎**里跑一条 SQL，用完 dispose。

    ⚠️ 不能用 `SessionLocal`：它的连接池**绑在创建它的那个事件循环**上，
    而本套件的 `main()` 是同步的、被 `_driver` 的循环调用 —— 在另一个线程里
    复用那个池会报 "attached to a different loop"（我第一版就这么踩的，
    异常还被线程吞掉，只留下一堆看不懂的 traceback）。
    """
    import asyncio
    import threading

    from sqlalchemy import text as _t
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core.config import settings

    out: dict = {}

    def worker():
        async def go():
            engine = create_async_engine(settings.database_url)
            try:
                async with engine.begin() as conn:
                    res = await conn.execute(_t(sql_text), params or {})
                    if fetch == "scalar":
                        out["v"] = res.scalar_one()
                    elif fetch == "count":
                        out["v"] = int(res.scalar_one())
            finally:
                await engine.dispose()

        asyncio.run(go())

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    return out.get("v")


def run_db_scalar(sql_text, params=None):
    return _run_sql(sql_text, params, fetch="scalar")


def run_db_count(sql_text, params=None):
    return _run_sql(sql_text, params, fetch="count")


def run_db_exec(sql_text, params=None):
    return _run_sql(sql_text, params)


def run_db_update_holding_lock(table: str, row_id: int, values: dict) -> None:
    """在**另一个连接**里改这一行并持锁约 2 秒，制造"请求读完 → 等锁"的窗口。

    用于复现"读到旧状态 → 等锁期间别人改了 → 自己再赋同值被 ORM 吞掉"这类缺陷。
    """
    import asyncio
    import threading
    import time

    from sqlalchemy import text as _t
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core.config import settings

    sets = ", ".join(f"{k}=:{k}" for k in values)
    params = dict(values, _id=row_id)

    def worker():
        async def go():
            engine = create_async_engine(settings.database_url)
            try:
                async with engine.connect() as conn:
                    await conn.execute(_t(f"update {table} set {sets} where id=:_id"), params)
                    await asyncio.sleep(2)   # 故意不提交，先持锁
                    await conn.commit()
            finally:
                await engine.dispose()

        asyncio.run(go())

    t = threading.Thread(target=worker)
    t.start()
    time.sleep(0.4)   # 确保已持锁，主请求随后会等这把锁


def run_db_delete_holding_lock(table: str, row_id: int) -> None:
    """在**另一个连接**里删除该行并持锁约 2 秒，用来复现"编辑等锁期间记录消失"。

    必须真的持锁：只删不持锁的话，编辑请求会在删除提交**之后**才开始，
    走的是"记录本来就不存在"的路径（404 也能过），验不出并发那一条。
    """
    import asyncio
    import threading
    import time

    from sqlalchemy import text as _t
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core.config import settings

    def worker():
        async def go():
            engine = create_async_engine(settings.database_url)
            try:
                async with engine.connect() as conn:
                    await conn.execute(_t(f"delete from {table} where id=:i"), {"i": row_id})
                    # 故意不提交，先持锁一段时间
                    await asyncio.sleep(2)
                    await conn.commit()
            finally:
                await engine.dispose()

        asyncio.run(go())

    t = threading.Thread(target=worker)
    t.start()
    time.sleep(0.4)   # 确保删除已持锁，编辑请求随后会等这把锁
    return None


async def clean(verbose=False):
    from sqlalchemy import text

    from app.core.database import SessionLocal

    statements = [
        # 顺序服从外键：先删引用 sku/product 的表，最后删它们本身
        ('用例客户特殊价', "delete from customer_price_rules where sku_id in "
                       f"(select id from skus where sku_code like 'CHK{RUN}%') "
                       f"or customer_id in (select id from customers where name like 'CHK{RUN}%')"),
        ('用例跟进', f"delete from followups where content like '%CHK{RUN}%'"),
        ('用例报价明细', "delete from quote_items where quote_version_id in "
                      "(select id from quote_versions where quote_id in "
                      f"(select id from quotes where customer_id in (select id from customers where name like 'CHK{RUN}%')))"),
        ('用例报价版本', "delete from quote_versions where quote_id in "
                      f"(select id from quotes where customer_id in (select id from customers where name like 'CHK{RUN}%'))"),
        ('用例报价', f"delete from quotes where customer_id in (select id from customers where name like 'CHK{RUN}%')"),
        ('用例产品文件', "delete from business_files where business_type = 'product' and business_id in "
                      f"(select id from products where name like 'CHK{RUN}%')"),
        ('用例价格规则', "delete from price_rules where sku_id in "
                      f"(select id from skus where sku_code like 'CHK{RUN}%')"),
        ('用例成本', "delete from product_costs where sku_id in "
                   f"(select id from skus where sku_code like 'CHK{RUN}%')"),
        # 商机必须排在客户前面：opportunities.customer_id 有外键指向 customers，
        # 少了这一步，"delete from customers" 会直接抛外键错——本套件用例 9
        # 建了快捷商机，所以此前客户是真的删不掉（而且报错会把后面的清理也带走）。
        # 明细与阶段历史同属商机链路，一并按依赖顺序清。
        ('用例商机明细', "delete from opportunity_items where opportunity_id in "
                     f"(select id from opportunities where customer_id in "
                     f"(select id from customers where name like 'CHK{RUN}%'))"),
        ('用例商机阶段历史', "delete from opportunity_stage_history where opportunity_id in "
                       f"(select id from opportunities where customer_id in "
                       f"(select id from customers where name like 'CHK{RUN}%'))"),
        ('用例商机', f"delete from opportunities where customer_id in "
                  f"(select id from customers where name like 'CHK{RUN}%')"),
        # 注意这三条必须有 f 前缀：少了它 {RUN} 会原样进 SQL，like 匹配不到任何行，
        # 清理会"跑完且不报错"地把产品和客户留在库里（曾经真的这样漏了很久）。
        ('用例 SKU', f"delete from skus where sku_code like 'CHK{RUN}%'"),
        ('用例产品', f"delete from products where name like 'CHK{RUN}%'"),
        ('用例客户', f"delete from customers where name like 'CHK{RUN}%'"),
        ('用例审计', "delete from audit_logs where business_type in "
                   "('product','sku','price_rule','customer_price_rule') "
                   "and created_at > now() - interval '2 hours'"),
    ]
    async with SessionLocal() as s:
        for label, sql in statements:
            result = await s.execute(text(sql))
            if verbose and result.rowcount:
                print(f'  {result.rowcount:>4}  {label}')
        await s.commit()


async def main():
    admin = login('admin', 'admin123')
    zhangsan = login('zhangsan', '123456')

    print()
    print('=== 1. 产品导入导出 ===')
    status, res = call('GET', '/products/import-template', token=admin)
    check('产品模板', status, 200)
    check_true('表头含产品名称', '产品名称' in res.get('_text', ''), res.get('_text', '')[:60])

    headers = ['产品名称', '产品线', '分类', '品牌', '描述']
    rows = [
        [f'CHK{RUN}产品甲', '纸箱', '包装', '宏远', '测试用'],
        [f'CHK{RUN}产品乙', '纸箱', '包装', '宏远', ''],
        [f'CHK{RUN}产品甲', '纸箱', '包装', '宏远', '重复的'],
    ]
    status, res = upload_csv(admin, '/products/import', headers, rows)
    check('导入产品', res.get('code'), 0)
    check('成功 2 条', res['data']['created_count'], 2)
    check('跳过 1 条重复', res['data']['skipped_count'], 1)

    status, res = call('GET', f'/products/export?keyword=CHK{RUN}', token=admin)
    check('GET 导出产品', status, 200)
    text = res.get('_text', '')
    check_true('含导入的产品', f'CHK{RUN}产品甲' in text, text[:80])

    status, res = call('POST', '/products/export', token=admin,
                       body={'keyword': f'CHK{RUN}产品乙'})
    check('POST 筛选导出', status, 200)
    check_true('只含产品乙', f'CHK{RUN}产品乙' in res.get('_text', ''), '筛选生效')
    check_true('不含产品甲', f'CHK{RUN}产品甲' not in res.get('_text', ''), '筛选生效')

    print()
    print('=== 2. SKU 导入导出 ===')
    status, res = call('GET', '/skus/import-template', token=admin)
    check('SKU 模板', status, 200)
    check_true('表头含 SKU编码', 'SKU编码' in res.get('_text', ''), res.get('_text', '')[:60])

    sku_headers = ['SKU编码', '产品名称', '规格名称', '规格', '单位', 'MOQ']
    sku_rows = [
        [f'CHK{RUN}-S1', f'CHK{RUN}产品甲', '中号', '400x300x200', '个', '100'],
        [f'CHK{RUN}-S2', f'CHK{RUN}产品甲', '大号', '500x400x300', '个', '50'],
        [f'CHK{RUN}-S1', f'CHK{RUN}产品甲', '重复编码', '', '个', ''],
        [f'CHK{RUN}-S9', f'不存在的产品XYZ', '孤儿', '', '个', ''],
    ]
    status, res = upload_csv(admin, '/skus/import', sku_headers, sku_rows)
    check('导入 SKU', res.get('code'), 0)
    check('成功 2 条', res['data']['created_count'], 2)
    check('跳过 1 条重复编码', res['data']['skipped_count'], 1)
    check('失败 1 条（产品不存在）', res['data']['failed_count'], 1)
    check_true('失败原因说清是找不到产品',
               '找不到产品' in (res['data']['failed'][0]['reason'] if res['data']['failed'] else ''),
               str(res['data']['failed'][:1]))

    status, res = call('GET', f'/skus/export?keyword=CHK{RUN}', token=admin)
    check('导出 SKU', status, 200)
    check_true('含 SKU', f'CHK{RUN}-S1' in res.get('_text', ''), res.get('_text', '')[:80])

    status, res = call('GET', f'/skus?keyword=CHK{RUN}', token=admin)
    sku_ids = [r['id'] for r in res['data']['items']]
    check_true('查到导入的 SKU', len(sku_ids) >= 2, str(sku_ids))

    print()
    print('=== 3. 产品附件（POST /products/{id}/files）===')
    status, res = call('GET', f'/products?keyword=CHK{RUN}产品甲', token=admin)
    product_id = res['data']['items'][0]['id']

    status, res = call('POST', f'/products/{product_id}/files?file_id=999999', token=admin)
    check('文件不存在被拒', res.get('code'), 40401)
    status, res = call('POST', '/products/999999/files?file_id=1', token=admin)
    check('产品不存在被拒', res.get('code'), 40401)

    print()
    print('=== 4. 核价权限校验（POST /pricing/check-permission）===')
    # 必须用**有成本记录**的 SKU：没有成本时成本为 0，
    # 报 0.01 元也会被算成"利润 100%"，压根测不出审批逻辑。
    status, res = call('GET', '/skus?page_size=50', token=admin)
    priced_sku = None
    for row in res['data']['items']:
        probe = call('POST', '/pricing/calculate', token=admin,
                     body={'sku_id': row['id'], 'quantity': 100})[1]
        if probe.get('code') == 0 and probe['data']['standard_price']:
            priced_sku = row
            break
    check_true('找到有成本记录的 SKU', priced_sku is not None,
               str(priced_sku and priced_sku['id']))
    price_sku_id = priced_sku['id']
    base = {'sku_id': price_sku_id, 'quantity': 100}

    status, res = call('POST', '/pricing/check-permission', token=admin,
                       body={**base, 'quoted_price': 100000})
    check('高价 -> 允许', res.get('code'), 0)
    check('allowed=True', res['data']['allowed'], True)
    check('不需要审批', res['data']['approval_required'], False)
    check('reasons 为空', res['data']['reasons'], [])
    del res

    status, res = call('POST', '/pricing/check-permission', token=admin,
                       body={**base, 'quoted_price': 0.01})
    check('白菜价 -> 需要审批', res['data']['approval_required'], True)
    check('allowed=False', res['data']['allowed'], False)
    check_true('给了原因', len(res['data']['reasons']) >= 1, str(res['data']['reasons']))

    status, res = call('POST', '/pricing/check-permission', token=admin, body=base)
    check('缺 quoted_price 被拒', res.get('code'), 40001)

    print()
    print('=== 5. 与 /pricing/calculate 判定必须一致 ===')
    for price, label in ((100000, '高价'), (0.01, '白菜价')):
        _, c = call('POST', '/pricing/calculate', token=admin,
                    body={**base, 'quoted_price': price})
        _, p = call('POST', '/pricing/check-permission', token=admin,
                    body={**base, 'quoted_price': price})
        check_true(f'{label}：approval_required 一致',
                   c['data']['approval_required'] == p['data']['approval_required'],
                   f"calculate={c['data']['approval_required']} check={p['data']['approval_required']}")
        check_true(f'{label}：minimum_price 一致',
                   c['data']['minimum_price'] == p['data']['minimum_price'],
                   f"{c['data']['minimum_price']} vs {p['data']['minimum_price']}")
        check_true(f'{label}：protection_price 一致',
                   c['data']['protection_price'] == p['data']['protection_price'],
                   f"{c['data']['protection_price']} vs {p['data']['protection_price']}")

    print()
    print('=== 6. 报价模拟（POST /pricing/simulate）===')
    status, res = call('POST', '/pricing/simulate', token=admin,
                       body={'base': base, 'candidates': [100000, 500, 0.01]})
    check('模拟成功', res.get('code'), 0)
    check('3 个档位', len(res['data']['scenarios']), 3)
    prices = [s['quoted_price'] for s in res['data']['scenarios']]
    check('档位顺序保持', prices, [100000.0, 500.0, 0.01])
    check('高价档不需要审批', res['data']['scenarios'][0]['approval_required'], False)
    check('低价档需要审批', res['data']['scenarios'][2]['approval_required'], True)
    check_true('带总额', res['data']['scenarios'][0]['amount'] == 100000.0 * 100,
               str(res['data']['scenarios'][0]['amount']))
    check_true('带利润', res['data']['scenarios'][0]['profit'] is not None)

    status, res = call('POST', '/pricing/simulate', token=admin,
                       body={'base': base, 'margins': [0.1, 0.2, 0.3]})
    check('按利润率模拟', res.get('code'), 0)
    check('3 个档位', len(res['data']['scenarios']), 3)
    rates = [s['profit_rate'] for s in res['data']['scenarios']]
    check_true('利润率递增', rates == sorted(rates), str(rates))

    status, res = call('POST', '/pricing/simulate', token=admin, body={'base': base})
    check('不传候选 -> 用关键点位', res.get('code'), 0)
    check_true('至少 1 个档位', len(res['data']['scenarios']) >= 1,
               str(len(res['data']['scenarios'])))

    status, res = call('POST', '/pricing/simulate', token=admin,
                       body={'base': base, 'candidates': [100, 100, 100]})
    check('重复候选去重', len(res['data']['scenarios']), 1)

    print()
    print('=== 7. 核价历史（GET /pricing/history）===')
    status, res = call('GET', '/pricing/history', token=admin)
    check('历史可读', res.get('code'), 0)
    check_true('分页结构', 'items' in res['data'] and 'total' in res['data'])

    status, res = call('GET', f'/pricing/history?sku_id={price_sku_id}', token=admin)
    check('按 SKU 过滤可读', res.get('code'), 0)

    status, res = call('GET', '/pricing/history?page=1&page_size=2', token=admin)
    check('分页生效', res['data']['page_size'], 2)

    print()
    print('=== 8. 客户特殊价 PATCH ===')
    # 自建夹具客户：抓"列表第一个客户"会撞上库里已有特殊价（40901 冲突），数据依赖型脆弱用例
    status, res = call('POST', '/customers', token=admin, body={'name': f'CHK{RUN}特殊价客户'})
    check('建夹具客户', res.get('code'), 0)
    customer_id = res['data']['id']
    status, res = call('POST', '/customer-price-rules', token=admin, body={
        'customer_id': customer_id, 'sku_id': price_sku_id,
        'min_qty': 1, 'max_qty': 100, 'agreed_price': 800, 'remark': f'CHK{RUN}',
    })
    check('建客户特殊价', res.get('code'), 0)
    rule_id = res['data']['id']

    # 取价来源会写成 `customer_specific`（17 字符），而这张明细列以前只有
    # varchar(16)：**客户有专属价时，给他建报价明细直接 500**
    # （asyncpg: value too long for type character varying(16)）。这里钉住"建得出来"。
    status, res = call('POST', '/opportunities', token=admin,
                       body={'customer_id': customer_id, 'title': f'CHK{RUN}特殊价商机'})
    check('建夹具商机（专属价客户）', res.get('code'), 0)
    status, res = call('POST', '/quotes', token=admin,
                       body={'opportunity_id': res['data']['id']})
    check('建夹具报价（专属价客户）', res.get('code'), 0)
    special_version_id = res['data']['version_id']
    status, res = call('POST', f'/quote-versions/{special_version_id}/items', token=admin,
                       body={'sku_id': price_sku_id, 'quantity': 1})
    check('有专属价的客户也能建报价明细（取价来源不再溢出）', res.get('code'), 0)
    # 价格确实来自专属价规则（800）：验明"专属价这条路真的走通了"，
    # 而不只是"没报错"。price_source 在报价页直接录明细这条路径上按
    # "手工价"口径留空（漂移检测据此跳过，不自动覆盖），与核价页选品下单
    # 那条路（会带规则来源）是两个入口，别混。
    check('取到了客户专属价', res['data'].get('quoted_price'), 800.0)

    status, res = call('PATCH', f'/customer-price-rules/{rule_id}', token=admin,
                       body={'agreed_price': 750})
    check('只改协议价', res.get('code'), 0)
    check('价格已改', res['data']['agreed_price'], 750.0)
    check('起订量没被动', res['data']['min_qty'], 1.0)
    check('备注没被动', res['data']['remark'], f'CHK{RUN}')

    status, res = call('PATCH', f'/customer-price-rules/{rule_id}', token=admin,
                       body={'min_qty': 200})
    check('起订量 > 上限被拒', res.get('code'), 40001)
    # 2026-10-07：区间文案与导入侧统一成"不能大于数量上限"（原先一边叫"上限"、
    # 一边叫"数量上限"，两套判据两套话术）。
    check_true('说明原因', '不能大于数量上限' in (res.get('message') or ''),
               res.get('message') or '')

    status, res = call('PATCH', f'/customer-price-rules/{rule_id}', token=admin,
                       body={'min_qty': 50, 'max_qty': 500, 'remark': None})
    check('区间整体调整', res.get('code'), 0)
    check('上限已改', res['data']['max_qty'], 500.0)
    check('备注被清空', res['data']['remark'], None)

    status, res = call('PATCH', '/customer-price-rules/999999', token=admin,
                       body={'agreed_price': 1})
    check('规则不存在', res.get('code'), 40401)

    print()
    print('=== 8b. 客户特殊价的数据范围（审查 2026-10-07 第二轮）===')
    # 上面的夹具客户是 admin 建的、负责人就是 admin；张三只有自己的范围。
    # 上一轮只给"新增/修改"接了范围判据，删除和列表漏了 —— 这里把四处都钉住。
    status, res = call('GET', f'/customer-price-rules?customer_id={customer_id}',
                       token=zhangsan)
    check('范围外客户：指定编号也读不到', res.get('code'), 40302)

    status, res = call('GET', '/customer-price-rules?page=1&page_size=200', token=zhangsan)
    check('范围外客户：列表接口能读', res.get('code'), 0)
    listed = {item['id'] for item in (res['data'].get('items') or [])}
    check_true('别人的专属价不该出现在列表里', rule_id not in listed,
               f'列表里出现了 {rule_id}')

    # 张三没有 price:manage，会先被权限挡下（40301）；范围判据本身（有权限但范围外）
    # 由单元测试直接覆盖 —— 本项目 seed 里**所有有改价权的账号都在同一个部门**
    # （admin / 李四 / 张三 的 department_id 都是 1），端到端造不出
    # "有权限但看不到这个客户"的组合。无论走哪一道，结论都必须是"拒绝 + 原规则还在"。
    status, res = call('DELETE', f'/customer-price-rules/{rule_id}', token=zhangsan)
    check_true('范围外客户：删除被拒', res.get('code') in (40301, 40302), str(res.get('code')))
    # 越权被拒之后原规则必须还在（不能"先删掉再判权限"）
    status, res = call('GET', f'/customer-price-rules?customer_id={customer_id}', token=admin)
    survivors = [item for item in (res['data'].get('items') or []) if item['id'] == rule_id]
    check_true('越权删除后原规则仍在', len(survivors) == 1, f'剩 {len(survivors)} 条')
    # 保留这条规则：后面的用例还要用它（别在这里删掉把别人测挂了）

    print()
    print('=== 9. 报价跟进（GET /quotes/{id}/followups）===')
    status, res = call('POST', '/customers', token=admin, body={'name': f'CHK{RUN}客户'})
    q_customer = res['data']['id']
    # D8：报价必须挂商机
    status, res = call('POST', '/opportunities', token=admin, body={
        'customer_id': q_customer, 'title': f'CHK{RUN}报价商机',
    })
    check('建快捷商机', res.get('code'), 0)
    pp_opp_id = res['data']['id']
    status, res = call('POST', '/quotes', token=admin, body={'opportunity_id': pp_opp_id})
    quote_id = res['data']['quote_id']

    status, res = call('GET', f'/quotes/{quote_id}/followups', token=admin)
    check('报价跟进列表可读', res.get('code'), 0)
    check('初始 0 条', res['data']['total'], 0)

    status, res = call('POST', '/followups', token=admin, body={
        'exemption_reason': 'waiting_external',
        'customer_id': q_customer, 'quote_id': quote_id,
        'content': f'CHK{RUN}客户说价格再谈谈',
    })
    check('建带报价的跟进', res.get('code'), 0)

    status, res = call('GET', f'/quotes/{quote_id}/followups', token=admin)
    check('现在 1 条', res['data']['total'], 1)
    check_true('内容是那条', f'CHK{RUN}' in res['data']['items'][0]['content'], '命中')

    status, res = call('GET', '/quotes/999999/followups', token=admin)
    check('报价不存在', res.get('code'), 40401)

    print()
    print('=== 9b. 折扣上限参与审批（discount_limit 此前只存不用）===')
    status, res = call('GET', '/roles', token=admin)
    sales_role_id = next(r['id'] for r in res['data'] if r['code'] == 'salesperson')
    status, res = call('GET', '/price-permissions', token=admin)
    saved_perm = next((p for p in res['data'] if p['role_id'] == sales_role_id), None)
    # 给销售角色配一个很紧的折扣上限 10%（库里 0-1 比例口径）
    status, res = call('PUT', f'/price-permissions/{sales_role_id}', token=admin,
                       body={'minimum_margin': 0.05, 'discount_limit': 0.1, 'can_approve': True})
    check('配置折扣上限', res.get('code'), 0)
    zhangsan = login('zhangsan', '123456')
    std_probe = call('POST', '/pricing/calculate', token=admin,
                     body={'sku_id': price_sku_id, 'quantity': 100})
    standard_price = float(std_probe[1]['data']['standard_price'])
    body = call('POST', '/pricing/calculate', token=zhangsan,
                body={'sku_id': price_sku_id, 'quantity': 100,
                      'quoted_price': standard_price * 0.5})[1]
    check('张三半价核价成功', body.get('code'), 0)
    if body.get('code') != 0:
        print('   返回：', str(body)[:300])
    else:
        check_true('让利 50% 触发审批', body['data']['approval_required'] is True,
                   str(body['data'].get('warnings'))[:120])
        check_true('原因指明折扣超上限',
                   any('折扣' in w for w in body['data'].get('warnings', [])),
                   str(body['data'].get('warnings'))[:160])
    # 恢复原权限，不污染其他用例
    if saved_perm:
        call('PUT', f'/price-permissions/{sales_role_id}', token=admin, body={
            'minimum_margin': saved_perm['minimum_margin'],
            'discount_limit': saved_perm['discount_limit'],
            'can_approve': saved_perm['can_approve'],
            'remark': saved_perm.get('remark'),
        })

    print('=== 9c. 核价运费接体积计费（复用物流模块实现）===')

    def all_rates() -> list[dict]:
        return call('GET', '/logistics/rates', token=admin)[1].get('data') or []

    def own_rates() -> list[dict]:
        """**本套件自己建的**费率（其余是种子数据与别的套件留下的）。

        ⚠️ `GET /logistics/rates` **签名里没有 keyword 参数** —— 传了也不生效，
        它无条件返回**全部**费率。从前这里写的是
        `call('GET', '/logistics/rates?keyword=CHK')` 然后把自己"以为筛出来的"
        每一条都删掉，实际是**把整张费率表删空**（2026-10-08 实测）。
        而且那句"清理测试费率"的断言写成"删后查不到了"就必然成立 —— 全删了当然
        查不到，是**假绿**。所以：**在客户端按前缀筛**，并额外断言"别人的一条没动"。
        """
        return [
            row for row in all_rates()
            if str(row.get('provider') or '').startswith(f'CHK{RUN}')
        ]

    def drop_own_rates() -> None:
        for row in own_rates():
            call('DELETE', f"/logistics/rates/{row['id']}", token=admin)

    # 先清掉自己上一轮留下的测试费率（destination 也是 CHK 开头，会精确匹配干扰断言）
    drop_own_rates()
    foreign_before = sorted(
        row['id'] for row in all_rates()
        if not str(row.get('provider') or '').startswith(f'CHK{RUN}')
    )
    status, res = call('POST', '/logistics/rates', token=admin, body={
        'provider': f'CHK{RUN}物流', 'origin_region': '华东', 'destination_region': 'CHK华北测试区',
        'shipping_method': '陆运', 'unit_price_per_kg': 1, 'unit_price_per_volume': 200,
        'min_charge': 0})
    check('建含体积价的费率', res.get('code'), 0)
    rate_id = res['data']['id']
    status, res = call('GET', f'/skus/{price_sku_id}', token=admin)
    old_carton = res['data'].get('carton_volume')
    old_carton_qty = res['data'].get('carton_qty')
    # 箱规数量一并固定为 6：体积计价依赖 carton_volume/carton_qty 两个字段，
    # 只固定体积会随所选 SKU 的 carton_qty 漂移（数据依赖型脆弱用例）
    call('PATCH', f'/skus/{price_sku_id}', token=admin, body={'carton_volume': 1, 'carton_qty': 6})
    probe = call('POST', '/pricing/calculate', token=admin,
                 body={'sku_id': price_sku_id, 'quantity': 10, 'quoted_price': 100,
                       'country': 'CHK华北测试区', 'shipping_method': '陆运'})
    if probe[1].get('code') != 0 or 'logistics' not in (probe[1].get('data', {}).get('cost') or {}):
        data = probe[1].get('data') or {}
    logistics = probe[1]['data']['cost']['logistics_cost']
    # 10 件 × 0.1667m³/件（箱规 1m³ ÷ 6 件）= 1.6667m³；体积计价 1.6667 × 200 = 333.34，
    # 摊到单件 33.334 —— 旧算法按重量只有 3.5 元/件，抛货被严重低估，这正是接体积的意义
    check('抛货按体积计价（33.334 元/件）', float(logistics), 33.334)
    # 回落验证：体积数据真的清空（置 null，而不是恢复回可能是 1.0 的原值），
    # 应退回按实重计费——证明没录体积的 SKU 行为与旧算法完全一致
    call('PATCH', f'/skus/{price_sku_id}', token=admin, body={'carton_volume': None})
    status, res = call('GET', f'/skus/{price_sku_id}', token=admin)
    unit_weight = float(res['data'].get('weight') or 0)
    probe = call('POST', '/pricing/calculate', token=admin,
                 body={'sku_id': price_sku_id, 'quantity': 10, 'quoted_price': 100,
                       'country': 'CHK华北测试区', 'shipping_method': '陆运'})
    check('无体积数据回落重量计价', float(probe[1]['data']['cost']['logistics_cost']),
          round(unit_weight * 10 * 1 / 10, 4))
    # 恢复原箱规体积与箱规数量
    call('PATCH', f'/skus/{price_sku_id}', token=admin,
         body={'carton_volume': old_carton, 'carton_qty': old_carton_qty})
    # 清理测试费率：**按 provider 前缀只删自己的**，并钉住"别人的一条没动"。
    # 单 id 依赖不可靠（失败路径下可能多建了几条），所以按范围删。
    deleted = len(own_rates())
    drop_own_rates()
    check_true('清理测试费率（只删自己的）', deleted >= 1 and not own_rates(),
               f"删前 {deleted} 条，删后 {len(own_rates())} 条")
    # ⚠️ 这条才是真正的守卫：从前那种"按关键字兜底删"会把种子费率与别的套件的
    # 费率一起删掉，而断言照样绿 —— 现在把"别人的"当成不变量钉住。
    check('别人的费率一条没动', sorted(
        row['id'] for row in all_rates()
        if not str(row.get('provider') or '').startswith(f'CHK{RUN}')
    ), foreign_before)

    print('=== 9b. 价格规则 R01/R04/R05/R06/R07 反例 ===')
    # 并发断言用：本块内多处需要（R05、R05b）
    from concurrent.futures import ThreadPoolExecutor as _TPE

    # 三条都是审查 2026-10-09 独立复测挖到的（正常场景测不出来），逐条钉住。
    # 用的等级名带 RUN 前缀，收尾清理按"本用例 SKU"删除，不会留下常驻数据。
    # 等级用一个演示数据没占用的短值：`price_rules.customer_level` 是 varchar(8)，
    # 夹具不能拿 RUN 去拼长名字（会撞列长度 → 500，实测踩到）
    lvl = 'Q'
    # 开局先清自己的残留（本轮如果中途失败，下面这些规则会留在演示 SKU 上，
    # 下一次跑的"正好 3 位小数放行"就会被上一轮的 1.000~1.235 判成区间重叠 ——
    # 实测踩到：单独跑通过、连跑第二遍 40901）。清理只认 `customer_level='Q'`
    # 这一个标记，不碰别的数据。
    from sqlalchemy import text as _sql_text

    from app.core.database import SessionLocal as _SessionLocal
    # `main()` 已改成 async（它内部只用同步 HTTP，安全），所以这里直接 await —— 
    # 套 asyncio.run 会报 "cannot be called from a running event loop"（实测踩到）。
    async with _SessionLocal() as _s:
        await _s.execute(_sql_text("delete from price_rules where customer_level = 'Q'"))
        await _s.commit()

    # ---- R04：数量小数位超 3 位必须被拒（从前静默舍入：1.2349 → 1.235）----
    status, res = call('POST', '/price-rules', token=admin, body={
        'sku_id': price_sku_id, 'customer_level': lvl,
        'min_qty': 1, 'max_qty': 1.2349, 'guide_price': 50,
    })
    check('R04 数量上限超 3 位小数被拒', res.get('code'), 40001)
    check_true('R04 提示点名"小数位"',
               '小数位' in str(res.get('message')), str(res.get('message'))[:60])
    # 正向对照：正好 3 位必须放行
    status, res = call('POST', '/price-rules', token=admin, body={
        'sku_id': price_sku_id, 'customer_level': lvl,
        'min_qty': 1, 'max_qty': 1.235, 'guide_price': 50,
    })
    check('R04 正好 3 位小数放行', res.get('code'), 0)

    # ---- R06：停用规则改备注不该被判区间重叠；重新启用遇冲突仍要拦 ----
    status, res = call('POST', '/price-rules', token=admin, body={
        'sku_id': price_sku_id, 'customer_level': lvl,
        'min_qty': 50000, 'max_qty': 50999, 'guide_price': 60,
    })
    r06_a = res['data']['id']
    call('DELETE', f'/price-rules/{r06_a}', token=admin)          # 停用（软删）
    status, res = call('POST', '/price-rules', token=admin, body={
        'sku_id': price_sku_id, 'customer_level': lvl,
        'min_qty': 50000, 'max_qty': 50999, 'guide_price': 70,
    })
    check('R06 停用后同区间可新建启用规则', res.get('code'), 0)
    status, res = call('PATCH', f'/price-rules/{r06_a}', token=admin,
                       body={'remark': f'CHK{RUN} 只改停用规则的备注'})
    check('R06 只改停用规则的备注被放行', res.get('code'), 0)
    status, res = call('PATCH', f'/price-rules/{r06_a}', token=admin,
                       body={'status': 'active'})
    check('R06 重新启用遇冲突仍被拦', res.get('code'), 40901)

    # ---- R07：备注超长必须 400 且点名，不能 500 ----
    status, res = call('POST', '/price-rules', token=admin, body={
        'sku_id': price_sku_id, 'customer_level': lvl,
        'min_qty': 60000, 'max_qty': 60999, 'guide_price': 10,
        'remark': '丙' * 300,
    })
    check('R07 价格规则备注超长 → 400（不是 500）', status, 400)
    check_true('R07 提示点名备注或长度',
               any(w in str(res.get('message')) for w in ('备注', 'remark', '太长', '最长')),
               str(res.get('message'))[:60])

    # ---- R01：原指导价 NULL 时，PATCH 改成 0 必须被拒 ----
    # 这是审查第二次复现挖到的：我修的第一版把 `None` 与 `0` 都归一成 0 再比，
    # 于是"原值 NULL、传 0"被判成"没变"而放行。而两者业务含义不同 ——
    # `NULL` = 这条规则没维护指导价（取价继续回退通用价），
    # `0`    = 明确零元（取价到此为止），实测该客户查价从回退价直接变成 0。
    status, res = call('POST', '/price-rules', token=admin, body={
        'sku_id': price_sku_id, 'customer_level': lvl,
        'min_qty': 70000, 'max_qty': 70999, 'guide_price': None,
    })
    check('R01 指导价可以留空（NULL = 未维护）', res.get('code'), 0)
    r01_id = res['data']['id']
    check('R01 留空后读回来仍是 NULL（序列化不把 NULL 变成 0）',
          res['data'].get('guide_price'), None)

    status, res = call('PATCH', f'/price-rules/{r01_id}', token=admin,
                       body={'guide_price': 0})
    check('R01 原值 NULL → 传 0 被拒（不能绕过价格锁定）', res.get('code'), 40001)
    _, after = call('GET', f'/price-rules/{r01_id}', token=admin)
    check('R01 被拒后库里仍是 NULL（回退语义没被改成零元）',
          after['data'].get('guide_price'), None)

    # 反向：NULL → 66 也是改价，同样要拦（别只堵 0 这一个值）
    status, res = call('PATCH', f'/price-rules/{r01_id}', token=admin,
                       body={'guide_price': 66})
    check('R01 原值 NULL → 传 66 也被拒', res.get('code'), 40001)

    # 合法操作不能被误拦：NULL 原样回传、只改区间、只改备注都要放行
    for body, label in (
        ({'guide_price': None}, 'NULL 原样回传'),
        ({'min_qty': 70000, 'max_qty': 70998}, '只改数量上限'),
        ({'remark': f'CHK{RUN} 只改备注'}, '只改备注'),
    ):
        status, res = call('PATCH', f'/price-rules/{r01_id}', token=admin, body=body)
        check(f'R01 合法操作放行：{label}', res.get('code'), 0)

    # ---- R05b：**同一条规则**并发改下限/上限，不许落成倒置区间 ----
    # 与 R05 不同：R05 是"两条新规则并发新增"，这里是"同一条规则被两个请求
    # 同时改不同字段"。审查实测过：原区间 0~10，同时改下限为 9、上限为 5，
    # **两个都 200、最终落库 9~5**（倒置）。根因是 `rule` 在**加锁之前**读入内存，
    # 后到的请求拿旧快照算"合并结果"（9+旧的10 校验通过），落库时又把旧的 10 写成 5。
    # 修法：拿到锁之后 `populate_existing` 重读该规则，用库里的**当前值**做合并校验。
    # 区间用 100~110：本块前面 R04/R06/R07 已经占掉 1~1.235、50000~50999、
    # 70000~70999，0~10 会与 1~1.235 重叠而被 40901 拒（实测踩到）
    status, res = call('POST', '/price-rules', token=admin, body={
        'sku_id': price_sku_id, 'customer_level': lvl,
        'min_qty': 100, 'max_qty': 110, 'guide_price': 50,
    })
    check('R05b 前置：建一条区间 100~110 的规则', res.get('code'), 0)
    r05b_id = (res.get('data') or {}).get('id')
    with _TPE(max_workers=2) as _ex:
        _futs = [
            _ex.submit(lambda: call('PATCH', f'/price-rules/{r05b_id}', token=admin,
                                    body={'min_qty': 109})),
            _ex.submit(lambda: call('PATCH', f'/price-rules/{r05b_id}', token=admin,
                                    body={'max_qty': 101})),
        ]
        _res2 = [f.result() for f in _futs]
    _ok2 = sum(1 for _, r in _res2 if r.get('code') == 0)
    _, after2 = call('GET', f'/price-rules/{r05b_id}', token=admin)
    _mn, _mx = after2['data'].get('min_qty'), after2['data'].get('max_qty')
    check('R05b 同一条规则并发改区间只成功一条', _ok2, 1)
    check_true('R05b 落库区间没有倒置（下限 <= 上限）',
               _mn is not None and (_mx is None or float(_mn) <= float(_mx)),
               f'最终 {_mn} ~ {_mx}')

    # ---- R05c：**同一条客户特殊价**并发改下限/上限，不许落成倒置区间 ----
    # 与 R05b 同型，但入口不同：我给价格规则补了"锁后重读"，**漏了客户特殊价这一处**
    # （审查 2026-10-09 第二次指出）。实测原区间 0~10、并发改下限 9 / 上限 5 →
    # 两个都 200、落库 `9~5`。所以两个入口都要有这条断言，防止再漏一个。
    status, res = call('POST', '/customer-price-rules', token=admin, body={
        'customer_id': 3, 'sku_id': 1,
        'min_qty': 0, 'max_qty': 10, 'agreed_price': 55,
    })
    check('R05c 前置：建一条客户特殊价（区间 0~10）', res.get('code'), 0)
    cp_race_id = (res.get('data') or {}).get('id')
    with _TPE(max_workers=2) as _ex:
        _futs = [
            _ex.submit(lambda: call('PATCH', f'/customer-price-rules/{cp_race_id}',
                                    token=admin, body={'min_qty': 9})),
            _ex.submit(lambda: call('PATCH', f'/customer-price-rules/{cp_race_id}',
                                    token=admin, body={'max_qty': 5})),
        ]
        _res3 = [f.result() for f in _futs]
    _ok3 = sum(1 for _, r in _res3 if r.get('code') == 0)
    _rows3 = call('GET', '/customer-price-rules?customer_id=3&page_size=200', token=admin)[1]['data']
    _rows3 = _rows3.get('items') if isinstance(_rows3, dict) else _rows3
    _row3 = next((x for x in _rows3 if x['id'] == cp_race_id), None)
    check('R05c 同一条客户特殊价并发改区间只成功一条', _ok3, 1)
    check_true('R05c 落库区间没有倒置（下限 <= 上限）',
               _row3 is not None and float(_row3['min_qty']) <= float(_row3['max_qty']),
               f"最终 {_row3 and _row3['min_qty']} ~ {_row3 and _row3['max_qty']}")
    call('DELETE', f'/customer-price-rules/{cp_race_id}', token=admin)

    # ---- R07b：客户特殊价的约定价不许被"显式传 null"静默跳过 ----
    # 审查实测：前端把空格转成 null 提交 → 后端 `if changes.get(...) is not None`
    # 把它当成"这个字段不改"，返回 200「已保存」而价格一个字没动。
    # 用**空闲的客户+SKU 组合**：演示数据里有的客户在该 SKU 上已有 3000~∞ 的规则，
    # 直接建 2002~2998 会撞区间（实测踩到）。这里固定用 (客户 3, SKU 1)，
    # 并用 2002~2998 这个只被自己占用的区间。
    _cust2, _sku2 = 3, 1
    status, res = call('POST', '/customer-price-rules', token=admin, body={
        'customer_id': _cust2, 'sku_id': _sku2,
        'min_qty': 2002, 'max_qty': 2998, 'agreed_price': 55,
    })
    check('R07b 前置：建一条约定价 55 的客户特殊价', res.get('code'), 0)
    cp_id = (res.get('data') or {}).get('id')
    if cp_id:
        status, res = call('PATCH', f'/customer-price-rules/{cp_id}', token=admin,
                           body={'agreed_price': None})
        check('R07b 约定价显式传 null → 400（不是静默 200）', res.get('code'), 40001)
        status, res = call('PATCH', f'/customer-price-rules/{cp_id}', token=admin,
                           body={'agreed_price': 0})
        check('R07b 约定价传 0 → 400（必填价钱必须为正）', res.get('code'), 40001)
        _, _lst = call('GET', f'/customer-price-rules?customer_id={_cust2}&page_size=200',
                       token=admin)
        _rows = _lst['data'].get('items') if isinstance(_lst['data'], dict) else _lst['data']
        _row = next((x for x in _rows if x['id'] == cp_id), None)
        check('R07b 两次被拒后约定价未被改动', _row and float(_row['agreed_price']), 55.0)
        # 正向对照：传正数仍可改
        status, res = call('PATCH', f'/customer-price-rules/{cp_id}', token=admin,
                           body={'agreed_price': 66})
        check('R07b 传正数仍可正常改价', res.get('code'), 0)
        call('DELETE', f'/customer-price-rules/{cp_id}', token=admin)

    # ---- R10：停用必须真的落库（锁后重读，别被"同值赋值"吞掉）----
    # 审查实测：规则原本 disabled，停用请求读到它后在等锁期间被"恢复启用"抢先提交，
    # 停用请求继续时把内存对象**再赋一次同值** —— ORM 认为没变化、不发 UPDATE，
    # 接口照样回 200「价格规则已停用」，**而库里仍是 active**。
    # 根因是 `delete_price_rule` 只拿锁、没走"锁后重读"。
    status, res = call('POST', '/price-rules', token=admin, body={
        'sku_id': price_sku_id, 'customer_level': lvl,
        'min_qty': 600, 'max_qty': 610, 'guide_price': 60,
    })
    check('R10 前置：建一条规则', res.get('code'), 0)
    r10_id = (res.get('data') or {}).get('id')
    call('DELETE', f'/price-rules/{r10_id}', token=admin)
    check('R10 前置：先停用它', run_db_scalar(
        "select status from price_rules where id=:i", {'i': r10_id}), 'disabled')

    # 制造"停用请求等锁、期间别人把它恢复启用"的窗口
    run_db_update_holding_lock("price_rules", r10_id, {"status": "active"})
    status, res = call('DELETE', f'/price-rules/{r10_id}', token=admin)
    final = run_db_scalar("select status from price_rules where id=:i", {'i': r10_id})
    check('R10 停用接口返回成功', res.get('code'), 0)
    check('R10 库里**确实**变成 disabled（不是"回了成功却仍启用"）', final, 'disabled')
    run_db_exec("delete from price_rules where id=:i", {'i': r10_id})

    # ---- R08：导入也必须参与同一把 SKU 锁（审查 P1）----
    # 从前导入只"查一遍冲突"而不拿锁：与页面新增并发时两边都查到"没有冲突"、
    # 都插入，同一 SKU 同一区间留下**两条启用规则**（实测指导价 66 与 55 并存）。
    # 两个导入入口都要验，只修一个等于没修。
    status, res = call('POST', '/price-rules', token=admin, body={
        'sku_id': price_sku_id, 'customer_level': lvl,
        'min_qty': 400, 'max_qty': 410, 'guide_price': 41,
    })
    check('R08 前置：建一条 400~410 的规则', res.get('code'), 0)
    _csv_rows = [
        {'SKU编码': run_db_scalar(
            "select sku_code from skus where id=:i", {'i': price_sku_id}),
         '客户等级(留空=通用)': lvl,
         '数量下限': '400', '数量上限(留空=不限)': '410', '指导价': '42'},
    ]
    # 先删掉前置规则，再让"导入"与"页面新增"并发抢同一区间
    call('DELETE', f"/price-rules/{(res.get('data') or {}).get('id')}", token=admin)
    with _TPE(max_workers=2) as _ex:
        _futs = [
            _ex.submit(lambda: call('POST', '/price-rules', token=admin, body={
                'sku_id': price_sku_id, 'customer_level': lvl,
                'min_qty': 400, 'max_qty': 410, 'guide_price': 41})),
            _ex.submit(lambda: upload_csv(admin, '/price-rules/import', [
                'SKU编码', '客户等级(留空=通用)', '数量下限', '数量上限(留空=不限)', '指导价'],
                _csv_rows)),
        ]
        [f.result() for f in _futs]
    _live = run_db_count(
        "select count(*) from price_rules where sku_id=:s and customer_level=:l "
        "and status='active' and min_qty=400", {'s': price_sku_id, 'l': lvl})
    check('R08 导入与新增并发后只落一条启用规则', _live, 1)
    # 收尾：把这批 400~410 的规则清掉（接口 DELETE 只置停用，会占着区间）
    run_db_exec("delete from price_rules where sku_id=:s and customer_level=:l "
                "and min_qty=400", {'s': price_sku_id, 'l': lvl})

    # ---- R09：编辑与删除并发 → 404，不是 500（审查 P2）----
    # 编辑请求读到记录后、在等锁期间记录被删：从前锁后重读用 `scalar_one()` 抛异常，
    # 加上"建议锁不阻塞读行"，flush 时还会撞 `StaleDataError` → 500。
    # 现在重读带**行锁**（等删除事务结束、按最新快照重算），取不到就给 404。
    status, res = call('POST', '/customer-price-rules', token=admin, body={
        'customer_id': 3, 'sku_id': 1,
        'min_qty': 2400, 'max_qty': 2410, 'agreed_price': 55,
    })
    check('R09 前置：建一条客户特殊价', res.get('code'), 0)
    _cp_id = (res.get('data') or {}).get('id')
    # 用原生连接"先删并持锁"，再让编辑去等锁 —— 精确复现"编辑等锁期间记录消失"
    run_db_delete_holding_lock("customer_price_rules", _cp_id)
    status, res = call('PATCH', f'/customer-price-rules/{_cp_id}', token=admin,
                       body={'agreed_price': 66})
    check('R09 记录已被删时编辑返回 404（不是 500）', res.get('code'), 40401)
    check_true('R09 提示说清是"刚被删除"', '删除' in str(res.get('message') or ''),
               str(res.get('message'))[:50])
    run_db_exec("delete from customer_price_rules where min_qty=2400", {})

    # ---- R05：并发提交重叠区间，必须只成功一条 ----
    # 从前两个并发请求各自读到"没有冲突"（对方还没提交），各写一条 →
    # 库里留下重叠区间 → 取价静默二选一。加了按 SKU 的 advisory 事务锁之后，
    # 后到的那个在锁上等，等到时先到者已提交，重新查冲突就查得到。
    # 用线程并发发两个请求；断言"恰好一条成功"——不管谁先拿到锁都成立，
    # 所以不是靠时序碰运气。
    from sqlalchemy import text as _t2

    from app.core.database import SessionLocal as _SL2
    async with _SL2() as _s:
        await _s.execute(_t2("delete from price_rules where customer_level = 'Q'"))
        await _s.commit()

    def _post(body):
        return call('POST', '/price-rules', token=admin, body=body)

    with _TPE(max_workers=2) as _ex:
        _futs = [
            _ex.submit(_post, {'sku_id': price_sku_id, 'customer_level': lvl,
                               'min_qty': 0, 'max_qty': 19, 'guide_price': 50}),
            _ex.submit(_post, {'sku_id': price_sku_id, 'customer_level': lvl,
                               'min_qty': 11, 'max_qty': 30, 'guide_price': 60}),
        ]
        _results = [f.result() for f in _futs]
    _ok = sum(1 for _, r in _results if r.get('code') == 0)
    _codes = [r.get('code') for _, r in _results]
    check('R05 并发提交重叠区间只成功一条', _ok, 1)
    check_true('R05 落败的那条是区间冲突（40901）而不是 500',
               sorted(_codes)[0] == 0 and 40901 in _codes, f'codes={_codes}')

    # 客户特殊价同一把锁：同客户同 SKU 并发也不能双双落库
    # 取一个可见客户（该套件没有现成的客户变量，直接查一个）
    _, _cr = call('GET', '/customers?page_size=1', token=admin)
    _items = _cr['data'].get('items') if isinstance(_cr['data'], dict) else _cr['data']
    _cust_id = _items[0]['id']

    async with _SL2() as _s:
        await _s.execute(
            _t2(f"delete from customer_price_rules where customer_id = {_cust_id} "
                f"and min_qty in (0, 11)"))
        await _s.commit()

    def _post_cp(body):
        return call('POST', '/customer-price-rules', token=admin, body=body)

    with _TPE(max_workers=2) as _ex:
        _futs = [
            _ex.submit(_post_cp, {'customer_id': _cust_id, 'sku_id': price_sku_id,
                                  'min_qty': 0, 'max_qty': 19, 'agreed_price': 50}),
            _ex.submit(_post_cp, {'customer_id': _cust_id, 'sku_id': price_sku_id,
                                  'min_qty': 11, 'max_qty': 30, 'agreed_price': 60}),
        ]
        _results2 = [f.result() for f in _futs]
    _ok2 = sum(1 for _, r in _results2 if r.get('code') == 0)
    check('R05 客户特殊价并发也只成功一条', _ok2, 1)
    async with _SL2() as _s:
        await _s.execute(
            _t2(f"delete from customer_price_rules where customer_id = {_cust_id} "
                f"and min_qty in (0, 11)"))
        await _s.commit()

    # 收尾：本块建的规则挂在**演示 SKU**（`CR-500-3`）上，而脚本末尾的
    # `clean()` 只删"本轮新建的 SKU"下的数据，删不到这些 —— 所以必须自己收。
    # 不收的后果实测过：下一轮 `1.000~1.235` 会被上一轮的同区间判成重叠（40901），
    # 于是"单独跑通过、连跑第二遍失败"。
    async with _SessionLocal() as _s:
        await _s.execute(_sql_text("delete from price_rules where customer_level = 'Q'"))
        await _s.commit()

    print('=== 10. 权限门槛 ===')
    for label, method, path, body in [
        ('产品导入', 'POST', '/products/import', None),
        ('SKU 导入', 'POST', '/skus/import', None),
        ('改客户特殊价', 'PATCH', f'/customer-price-rules/{rule_id}', {'agreed_price': 1}),
    ]:
        if body is None:
            # 导入是 multipart，这里只验权限：空 body 也应先被权限挡下
            status, res = call(method, path, token=zhangsan)
        else:
            status, res = call(method, path, token=zhangsan, body=body)
        check(f'张三{label}被拒', res.get('code'), 40301)

    status, res = call('GET', '/pricing/history', token=zhangsan)
    check('张三可以看核价历史（product:view）', res.get('code'), 0)


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
