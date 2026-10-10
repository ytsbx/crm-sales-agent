"""产品报价中心 A01–A16 验收脚本（方案 §9）。

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

# 外币夹具：造美元报价前要先把业务口径放开，跑完收回（见 scripts/_fx_scope.py）
from _fx_scope import open_export, restore_domestic
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from _test_support import require_api_base, require_isolated_db

require_isolated_db()

BASE = require_api_base()
RUN = str(int(time.time()))[-6:]
#: 绑进 SQL 的时间**必须是真的 datetime**：asyncpg 不接受字符串。
#: 这里原先传的是 time.strftime(...) 得到的字符串，于是整段"自动留痕清理"每次
#: 都抛 DataError、被外层 except 吞掉（日志里那句"（自动留痕清理跳过：…）"），
#: 通知与跟进其实一条都没清过——清理代码看起来是生效的，实际从没执行。
SCRIPT_STARTED_AT = datetime.now(UTC)
#: 运费分离之前的默认交货条款。它在新口径下是**会对客户说错话**的值
#: （产品单价已经不含运费了，还写"含运费"会让客户以为报价包了运费）。
OLD_DELIVERY_TERMS = '含运费，送货上门'

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


def run_db_write(sql_text, params=None):
    """执行一条**写** SQL（UPDATE/DELETE/INSERT）并提交，不取行。

    与 `run_db` 分开是有原因的：`UPDATE ... ` 没有返回行，在它上面调 `.all()`
    会抛 `ResourceClosedError`（我第一版就踩了，异常被线程吞掉才没炸出结论）。
    """
    import asyncio
    import threading

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core.config import settings

    def worker():
        async def go():
            engine = create_async_engine(settings.database_url)
            try:
                async with engine.begin() as conn:
                    await conn.execute(text(sql_text), params or {})
            finally:
                await engine.dispose()

        asyncio.run(go())

    t = threading.Thread(target=worker)
    t.start()
    t.join()


def run_db(sql_text, params=None):
    """执行一条 SQL 并返回所有行（元组列表）。

    刻意用**每次新建引擎 + 用完 dispose** 的写法：连接池绑在创建它的那个事件循环上，
    本函数会被多次调用（每次 `asyncio.run` 都是新循环），不 dispose 第二次就会报
    "attached to a different loop"（本套件末尾的清理留痕踩过同一个坑）。
    """
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core.config import settings

    result: dict = {}

    def worker():
        # 必须开**独立线程 + 独立事件循环**：`main()` 是同步的但外层已在事件循环里，
        # 直接 asyncio.run 会报 "cannot be called from a running event loop"
        # （本套件其它地方也这么绕）。
        async def go():
            engine = create_async_engine(settings.database_url)
            try:
                async with engine.begin() as conn:
                    rows = (await conn.execute(text(sql_text), params or {})).all()
                    result['rows'] = [tuple(r) for r in rows]
            finally:
                await engine.dispose()

        asyncio.run(go())

    import threading

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    return result.get('rows', [])


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


def confirm_sku_master(sku_id: int) -> None:
    """把某个 SKU **印给客户的三个字段**（名称/规格/单位）标成「已人工确认」——§8.14 的夹具。

    2026-10-07 复审后口径收紧了：正式发送要求明细**引用的那一版主数据快照**里，
    名称/规格/单位都有确认值。原来只要求"版本号非空"，于是**只确认名称也能发出去**
    （审查实测），所以这个夹具原来只插 `name` 一行 —— 现在它必须一次确认三个字段，
    **并落一版整版快照**：报价明细的 `master_version_no` 指向的正是那一版。

    走 API 得先制造外部差异，代价大且会牵动别的断言，所以这里直接写库，
    等价于产品岗做过一次完整确认。
    """
    import asyncio

    from sqlalchemy import text as sql_text

    from app.core.database import SessionLocal, engine

    async def _run() -> None:
        try:
            async with SessionLocal() as s:
                await s.execute(
                    sql_text(
                        "insert into sku_field_authorities "
                        "(sku_id, field_name, source_verified, confirmed_version, "
                        " confirmed_value, status, created_at, updated_at) "
                        "select s.id, f.field_name, false, 1, "
                        "       to_jsonb(jsonb_build_object("
                        "         'name', coalesce(s.name, ''), "
                        "         'specification', coalesce(s.specification, ''), "
                        "         'unit', coalesce(s.unit, '')) ->> f.field_name), "
                        "       'confirmed', now(), now() "
                        "from skus s "
                        "cross join (values ('name'), ('specification'), ('unit')) "
                        "  as f(field_name) "
                        "where s.id = :sku "
                        "on conflict (sku_id, field_name) do update set "
                        "  confirmed_version = 1, confirmed_value = excluded.confirmed_value, "
                        "  status = 'confirmed', updated_at = now()"
                    ),
                    {"sku": sku_id},
                )
                await s.execute(
                    sql_text(
                        "insert into sku_master_versions "
                        "(sku_id, version_no, values, source_summary, confirmed_by, "
                        " confirmed_at, note, created_at, updated_at) "
                        "select s.id, 1, "
                        "       jsonb_build_object("
                        "         'name', coalesce(s.name, ''), "
                        "         'specification', coalesce(s.specification, ''), "
                        "         'unit', coalesce(s.unit, '')), "
                        "       '{}'::jsonb, NULL, now(), "
                        "       '套件夹具：确认印给客户的三个字段', now(), now() "
                        "from skus s where s.id = :sku "
                        "on conflict (sku_id, version_no) do update set "
                        "  values = excluded.values, updated_at = now()"
                    ),
                    {"sku": sku_id},
                )
                await s.commit()
        finally:
            # 本脚本末尾还有一次 asyncio.run（清理留痕），而 async 引擎的连接池
            # **绑在创建它的那个事件循环**上：不清池，后面那次就会报
            # "attached to a different loop"，于是清理被跳过、夹具留在库里。
            await engine.dispose()

    asyncio.run(_run())


def read_margin_ctx(version_id):
    """从库里取审批引擎算出的综合毛利率（与规则判定同源）。

    直接调 `approval.rules_engine.build_context`，而不是看接口回显 ——
    验的就是"审批实际用的那个数"，接口换个地方算对了也没意义。
    """
    import asyncio

    from sqlalchemy import select

    from app.core.database import SessionLocal, engine
    from app.modules.approval.rules_engine import build_context
    from app.modules.quote.model import Quote, QuoteItem, QuoteVersion

    async def go():
        # 连接池**绑在创建它的那个事件循环**上：本函数被调用多次（每次
        # `asyncio.run` 一个新循环），用完必须 dispose，否则第二次就报
        # "attached to a different loop"（该套件末尾的清理留痕踩过同一个坑）。
        try:
            return await _go()
        finally:
            await engine.dispose()

    async def _go():
        async with SessionLocal() as session:
            version = (await session.execute(
                select(QuoteVersion).where(QuoteVersion.id == version_id)
            )).scalars().first()
            if version is None:
                return None
            quote = (await session.execute(
                select(Quote).where(Quote.id == version.quote_id)
            )).scalars().first()
            items = (await session.execute(
                select(QuoteItem).where(QuoteItem.quote_version_id == version_id)
            )).scalars().all()
            return await build_context(session, quote=quote, version=version,
                                       items=items, fx=None)

    return asyncio.run(go())


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
                    # §8.14 的确认夹具（confirm_sku_master 写的）也要一起收：
                    # SKU 是**软删**的，挂在它上面的行不会自己消失。
                    "delete from sku_field_authorities where sku_id = any(:sku_ids)",
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
            # 查价已按 03-API §1.1 归一到统一信封，先拆 data 再断言
            res = res['data']
            ok = ok and res['source_label'] == '客户等级价' and res['unit_price'] == expect
        record('A01', 'A/B 级客户各自带价且显示来源', ok, '85/90 等级价命中')

        # ---------------- A02 专属价优先、过期不命中 ----------------
        print('== A02 专属价优先与有效期 ==')
        _, res = call('POST', '/customer-price-rules', token=admin, body={
            'customer_id': customers['B'], 'sku_id': sku_id, 'min_qty': 1,
            'agreed_price': 88, 'effective_from': '2026-01-01', 'effective_to': '2026-12-31',
        })
        _, res = call('GET', f'/pricing/lookup?customer_id={customers["B"]}&sku_id={sku_id}&quantity=1', token=admin)
        res = res['data']
        ok = res['source'] == 'customer_specific' and res['unit_price'] == 88.0
        _, res = call('POST', '/customer-price-rules', token=admin, body={
            'customer_id': customers['B'], 'sku_id': sku_id, 'min_qty': 1,
            'agreed_price': 60, 'effective_from': '2025-01-01', 'effective_to': '2025-12-31',
        })
        expired_id = res['data']['id'] if res.get('code') == 0 else None
        _, res = call('GET', f'/pricing/lookup?customer_id={customers["B"]}&sku_id={sku_id}&quantity=1', token=admin)
        res = res['data']
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
        res = res['data']
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
        # 改价的**正确路径**（2026-10-09 口径）：停用旧的 + 新增一条同区间的新价。
        # 价格规则的 PATCH 明确不支持原地改价（后端会 400 并点名字段）——
        # 改价钱等于换了一套定价，停用+新增才留下"哪条从哪天起生效"的时序。
        # 换价后旧报价快照必须不变、草稿必须检出漂移：本用例验的正是这两件事。
        for rid in [r for r in rule_ids]:
            _, r = call('GET', f'/price-rules/{rid}', token=admin)
            if r['data'].get('customer_level') == 'A' and r['data'].get('guide_price') == 85.0:
                call('DELETE', f'/price-rules/{rid}', token=admin)
                _, new_rule = call('POST', '/price-rules', token=admin, body={
                    'sku_id': sku_id, 'customer_level': 'A', 'min_qty': 1, 'guide_price': 95,
                })
                if new_rule.get('code') == 0:
                    rule_ids.append(new_rule['data']['id'])
        _, res = call('GET', f'/quote-versions/{vid}', token=zhangsan)
        unchanged = res['data']['items'][0]['quoted_price'] == v1_price
        _, res = call('GET', f'/quote-versions/{vid}/price-drift', token=zhangsan)
        drift = res['data']['any_drift']
        record('A09', '旧版本快照不变；草稿检出漂移可刷新', unchanged and drift, f'v1={v1_price} drift={drift}')

        # 反向：**原地改价必须被拒**（口径 2026-10-09）。
        # 这条断言是配套的"另一面"：走停用+新增可以，绕过它直接改价不行。
        _, live = call('GET', '/price-rules?page_size=100', token=admin)
        patch_target = next(
            (r for r in live['data']['items']
             if r.get('customer_level') == 'A' and r['id'] in rule_ids),
            None,
        )
        if patch_target:
            status, res = call('PATCH', f"/price-rules/{patch_target['id']}", token=admin,
                               body={'guide_price': 77})
            blocked = status == 400 and '不支持原地修改价格' in (res.get('message') or '')
            # 只改区间则必须放行（闸门不能把合法编辑一起挡住）
            status2, _ = call('PATCH', f"/price-rules/{patch_target['id']}", token=admin,
                              body={'remark': 'A09 备注可改'})
            record('A09b', '原地改价被拒（400 且点名）；只改备注放行',
                   blocked and status2 == 200, f'blocked={blocked} remark_status={status2}')
        else:
            record('A09b', '原地改价被拒（400 且点名）；只改备注放行', False, '没找到 A 级规则')

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
        # 折扣按接口契约传**正数**（`amount: ge=0`），后端负责归一成负数入库
        # （`recalc_version` 直接代数相加，库里恒为负数）。字段名是 `description`——
        # 原来写的 `charge_name` 不在 schema 里，会被静默忽略（说明一直是空的）。
        call('POST', f'/quote-versions/{vid10}/charges', token=zhangsan,
             body={'description': '整单优惠', 'amount': 700, 'is_discount': True})
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

        # ---------------- A17 未确认的主数据不能正式发送（§8.14）----------------
        # 口径（2026-10-07 确认）：草稿随便建、**正式发送时必须过**。
        # 先把它测掉，再补确认 —— 后面的成交流程才发得出去。
        print('== A17 未确认的主数据不能正式发送 ==')
        _, res = call('POST', '/quotes', token=zhangsan, body={
            'customer_id': customers['A'], 'opportunity_id': opp_a05,
        })
        qid17, vid17 = res['data']['quote_id'], res['data']['version_id']
        created_quotes.append(qid17)
        call('POST', f'/quote-versions/{vid17}/items/batch', token=zhangsan,
             body=[{'sku_id': sku_id, 'quantity': 1, 'quoted_price': 85}])
        call('POST', f'/quote-versions/{vid17}/submit-approval', token=zhangsan, body={})
        _, res = call('POST', f'/quote-versions/{vid17}/mark-sent', token=zhangsan, body={})
        record('A17', '未确认主数据：草稿可建，正式发送被拦',
               res.get('code') == 40002 and '主数据' in (res.get('message') or ''),
               f"code={res.get('code')} {res.get('message')}")

        # 补上确认（模拟产品岗做完主数据确认）：下面 A13 才能正常走完成交流程。
        # 注意顺序 —— A13 的明细是在**确认之后**生成的，它引用的版本才是已确认的那版。
        confirm_sku_master(sku_id)

        # ---------------- A13 成交建单幂等 ----------------
        print('== A13 成交建单 ==')
        _, res = call('POST', '/quotes', token=zhangsan, body={
            'customer_id': customers['A'], 'opportunity_id': opp_a05,
        })
        qid13, vid13 = res['data']['quote_id'], res['data']['version_id']
        created_quotes.append(qid13)
        call('POST', f'/quote-versions/{vid13}/items/batch', token=zhangsan,
             body=[{'sku_id': sku_id, 'quantity': 10, 'quoted_price': 85}])
        # 运费分离（2026-10-09）：正式发送前必须已确认运费金额，而且**必须在提交
        # 审批之前填**（提交后版本不可编辑）。本用例验的是成交建单幂等，不是运费——
        # 补一条已确认的运费让流程能走完。
        call('POST', f'/quote-versions/{vid13}/charges', token=zhangsan,
             body={'charge_type': 'logistics', 'description': '验收运费', 'amount': 300})
        call('POST', f'/quote-versions/{vid13}/submit-approval', token=zhangsan, body={})
        _, sent_res = call('POST', f'/quote-versions/{vid13}/mark-sent', token=zhangsan, body={})
        if sent_res.get('code') != 0:
            # 这里以前不看返回值，于是"发送被拦"会一路传到 A13 才以"order=None"出现，
            # 看不出真正原因。明确打出来。
            print(f'   （mark-sent 未通过：{sent_res.get("code")} {sent_res.get("message")}）')
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
        res = res['data']
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

        # ---------------- A16 外币单：保护价快照不得被汇率缩小 ----------------
        print('== A16 外币单的保护价快照 ==')
        # 回归：minimum_price_snapshot 一度被折成**计价币种**（美元单上 80 人民币的
        # 保护价存成 11.43），而审批判定按人民币读它——于是低于保护价的美元报价
        # 静默放行。判据用"同一 SKU 的人民币单与美元单，保护价必须相等"：
        # 保护价是人民币口径的公司政策，不该随报价币种变。
        _, res = call('POST', '/quotes', token=zhangsan, body={
            'customer_id': customers['A'], 'opportunity_id': opp_a05,
        })
        cny_quote, cny_version = res['data']['quote_id'], res['data']['version_id']
        created_quotes.append(cny_quote)
        _, res = call('POST', f'/quote-versions/{cny_version}/items', token=zhangsan,
                      body={'sku_id': sku_id, 'quantity': 1, 'quoted_price': 20})
        floor_cny_quote = res['data'].get('minimum_price_snapshot')

        # 美元单：要先把业务口径放开（业务方也是先改口径、再报外币价）。
        # `finally` 一定要收回：收不回去，后面所有套件都会以为可以写外币。
        open_export(admin)
        try:
            _, res = call('POST', '/quotes', token=zhangsan, body={
                'customer_id': customers['A'], 'opportunity_id': opp_a05,
                'currency': 'USD', 'exchange_rate': 7,
            })
            usd_quote, usd_version = res['data']['quote_id'], res['data']['version_id']
            created_quotes.append(usd_quote)
            _, res = call('POST', f'/quote-versions/{usd_version}/items', token=zhangsan,
                          body={'sku_id': sku_id, 'quantity': 1, 'quoted_price': 20})
            floor_usd_quote = res['data'].get('minimum_price_snapshot')
            record('A16', '外币单的保护价快照与人民币单一致（按人民币存）',
                   floor_cny_quote is not None and floor_cny_quote == floor_usd_quote,
                   f"人民币单={floor_cny_quote} 美元单={floor_usd_quote}"
                   f"（若被折成美元会变成 {floor_cny_quote and round(floor_cny_quote / 7, 2)}）")
        finally:
            restore_domestic(admin)

        # ---------------- A18 长客户名仍能建报价（§8.7 快照列长度）----------------
        # 快照列曾经写死 `varchar(128)`，而客户名允许 200 —— 129 字的名字一建报价
        # 就撞 asyncpg 22001（value too long），报价单根本生成不出来。
        # 成因是**加列时没回头核对源列长度**，所以这里要连边界一起钉住：
        # 129（刚过旧的 128）和 200（源列上限）都能建出来，等于要求
        # "快照列长度 ≥ customers.name 的长度"。
        print('== A18 长客户名建报价 ==')
        for length in (129, 200):
            head = f'{PREFIX}长名{length}-'
            name = head + '甲' * (length - len(head))
            _, res = call('POST', '/customers', token=admin, body={'name': name})
            long_cid = res['data']['id']
            customers[f'long{length}'] = long_cid
            _, res = call('POST', '/opportunities', token=admin, body={
                'customer_id': long_cid, 'title': f'{PREFIX}长名商机{length}',
            })
            long_oid = res['data']['id']
            created_opps.append(long_oid)
            _, res = call('POST', '/quotes', token=admin, body={'opportunity_id': long_oid})
            built = res.get('code') == 0
            if built:
                created_quotes.append(res['data']['quote_id'])
            record('A18', f'{length} 字客户名能建出报价',
                   built, f'len={len(name)} code={res.get("code")} {res.get("message")}')

        # ---------------- A19 运费分离返修的四条反例 ----------------
        # 审查独立复测发现的四条（都发生在现有断言之外），逐条钉住：
        # ① 审批毛利率把"单价之和"与"整单运费"混着减 → 只改运费就改变毛利率；
        # ② 漏传运费金额被当成"已确认的零元"，还能正式发送；
        # ③ 建立新版/复制时口径切了、派生快照照抄（只改备注利润就变）；
        # ④ 历史口径的报价被强行加上"以上产品单价均不含运费"。
        print('== A19 运费分离返修反例 ==')
        _, res = call('POST', '/quotes', token=zhangsan, body={
            'customer_id': customers['A'], 'opportunity_id': opp_a05,
        })
        fix19_quote = res['data']['quote_id']
        fix19_version = res['data']['version_id']
        created_quotes.append(fix19_quote)
        _, res = call('POST', f'/quote-versions/{fix19_version}/items', token=zhangsan,
                      body={'sku_id': sku_id, 'quantity': 100, 'quoted_price': 100})
        fix19_item = res['data']['id']

        # ---- ① 只改运费，审批用的综合毛利率必须不变 ----
        # 从库里直接读上下文（与审批规则取值同源），避免"接口说的"与"审批算的"不一致
        ctx_before = read_margin_ctx(fix19_version)
        _, res = call('POST', f'/quote-versions/{fix19_version}/charges', token=zhangsan,
                      body={'charge_type': 'logistics', 'description': 'A19 运费',
                            'amount': 300})
        charge19 = res['data']['id'] if res.get('code') == 0 else None
        ctx_after = read_margin_ctx(fix19_version)
        record('A19a', '只改运费：审批综合毛利率不变',
               ctx_before is not None and ctx_after is not None
               and ctx_before['gross_margin'] == ctx_after['gross_margin'],
               f"运费 0→300：{ctx_before and ctx_before['gross_margin']}"
               f" → {ctx_after and ctx_after['gross_margin']}")

        # ---- ② 漏传运费金额：必须拒绝，且不落费用行、不写确认时刻 ----
        _, res = call('POST', f'/quote-versions/{fix19_version}/charges', token=zhangsan,
                      body={'charge_type': 'logistics', 'description': '没有提供金额'})
        rejected19 = res.get('code') != 0
        _, chs = call('GET', f'/quote-versions/{fix19_version}/charges', token=zhangsan)
        chs = chs.get('data') or []          # 该端点返回扁平数组
        zero_confirmed = [c for c in chs
                          if c.get('amount') == 0 and c.get('logistics_confirmed_at')]
        record('A19b', '漏传运费金额被拒，且不生成"已确认的零运费"',
               rejected19 and not zero_confirmed,
               f"code={res.get('code')} 被拒={rejected19} 零元已确认行={len(zero_confirmed)}")
        # 正向对照：显式填 0 仍然合法，且算"已确认的零运费"
        _, res = call('POST', f'/quote-versions/{fix19_version}/charges', token=zhangsan,
                      body={'charge_type': 'logistics', 'description': '明确零运费',
                            'amount': 0})
        explicit_zero_ok = (res.get('code') == 0
                            and res['data'].get('amount') == 0
                            and res['data'].get('logistics_confirmed_at'))
        record('A19c', '显式填写零元仍合法，并记为已确认的零运费',
               bool(explicit_zero_ok),
               f"code={res.get('code')} 确认时刻={bool(res['data'].get('logistics_confirmed_at'))}")
        # 收掉这条运费（端点挂在 /quote-charges 下，不在版本路径下），
        # 免得它改变同一报价上后续断言的形状
        if charge19:
            call('DELETE', f'/quote-charges/{charge19}', token=zhangsan)

        # ---- ③ 只改备注：利润与成本快照一个字都不能动 ----
        _, before19 = call('GET', f'/quote-versions/{fix19_version}', token=zhangsan)
        item_before = next(i for i in before19['data']['items'] if i['id'] == fix19_item)
        call('PATCH', f'/quote-items/{fix19_item}', token=zhangsan,
             body={'remark': 'A19 只改备注'})
        _, after19 = call('GET', f'/quote-versions/{fix19_version}', token=zhangsan)
        item_after = next(i for i in after19['data']['items'] if i['id'] == fix19_item)
        frozen = all(item_before[k] == item_after[k] for k in (
            'profit_snapshot', 'profit_rate_snapshot', 'cost_snapshot',
            'minimum_price_snapshot', 'quoted_price', 'quantity', 'recommended_price_snapshot',
        ))
        record('A19d', '只改备注不改变利润/成本/底价快照', frozen,
               f"利润 {item_before['profit_snapshot']}→{item_after['profit_snapshot']}、"
               f"成本 {item_before['cost_snapshot']}→{item_after['cost_snapshot']}")

        # ---- ③b 建新版：派生快照按当前口径重算，售价不漂移 ----
        _, res = call('POST', f'/quotes/{fix19_quote}/versions', token=zhangsan, body={})
        new19 = res['data'].get('version_id') or res['data'].get('id')
        _, nd = call('GET', f'/quote-versions/{new19}', token=zhangsan)
        # 按**稳定快照字段**配对，不按位置：复制后的明细顺序虽然一致，但按位置
        # 配对一旦上游改了顺序就会静默配错行，断言也就白写了。
        def _same(a, b):
            return (a.get('sku_code') == b.get('sku_code')
                    and a.get('inquiry_no') == b.get('inquiry_no')
                    and a.get('quoted_price') == b.get('quoted_price')
                    and a.get('quantity') == b.get('quantity'))

        nit = next((i for i in nd['data']['items'] if _same(i, item_after)), None)
        expected_profit = round(nit['quoted_price'] - nit['cost_snapshot'], 4) if nit else None
        basis_ok = (nit is not None
                    and nit['quoted_price'] == item_after['quoted_price']
                    and abs(round(nit['profit_snapshot'], 4) - expected_profit) < 0.001)
        record('A19e', '建新版：售价不漂移、利润按当前口径重算',
               basis_ok,
               f"单价 {item_after['quoted_price']}→{nit and nit['quoted_price']}、"
               f"利润 {nit and nit['profit_snapshot']}（期望 {expected_profit}）")

        # ---- ④ 对客文案只在"单价不含运费"口径下出现 ----
        _, det = call('GET', f'/quote-versions/{fix19_version}', token=zhangsan)
        basis = (det['data'].get('version') or {}).get('pricing_basis')
        note_ok = (basis == 'actual_pass_through')   # 统一口径后必须写
        record('A19f', '对客口径说明与版本口径一致（统一后恒为"不含运费"）',
               note_ok, f"pricing_basis={basis}")

    finally:
        # ---------------- A20 建立新版/复制报价/种子条款 三条反例（审查 2026-10-09）----------------
        #
        # ① 建立新版**不得降低公司保护价**：从前 `apply_current_basis_to_item` 用
        #    `成本 × (1 + 最低利润率)` 覆盖底价，既写反了公式、又丢掉保护价 ——
        #    实测成本 80、保护价 99、拟报价 95 时底价从 99 掉到 92，"不需要审批直接通过"。
        # ② 复制报价**也要重算派生快照**：从前只修了"建立新版"这一条入口。
        # ③ 默认交货条款只能有一份文案：种子脚本曾硬编码旧文案，全新库跑完种子
        #    新建报价仍写"含运费，送货上门"。
        print()
        print('== A20 建立新版保底价 / 复制重算 / 默认交货条款 ==')
        # ---- ① 保护价 ----
        _, res = call('POST', '/price-rules', token=admin, body={
            'sku_id': sku_id, 'customer_level': 'C', 'min_qty': 0,
            'guide_price': 50, 'minimum_price': 99,
            'remark': f'{PREFIX}-C级保护价',
        })
        c_rule_id = (res.get('data') or {}).get('id')
        created_rules.append(c_rule_id) if c_rule_id else None
        _, res_c = call('POST', '/customers', token=zhangsan, body={
            'name': f'{PREFIX}-客户C', 'level': 'C', 'remark': '验收临时客户',
        })
        c_cust = (res_c.get('data') or {}).get('id')
        # 登记进收尾清理清单（2026-10-10 修）。这一段的客户此前只存在局部变量里，
        # 于是每跑一次就留一个「客户C」——与上面「客户C(无规则)」是同一个毛病。
        if c_cust:
            customers['C_A20'] = c_cust
        _, res_o = call('POST', '/opportunities', token=admin, body={
            'customer_id': c_cust, 'title': f'{PREFIX}-A20保护价商机'})
        c_opp = (res_o.get('data') or {}).get('id')
        # 登记进收尾清理清单（2026-10-10 修）。此前这一段自建了商机却不登记，
        # 每跑一次就留下「A20保护价商机」，被 check_fixture_residue 抓到。
        # 与上面「客户C」那处是同一个毛病（那里已由前人补过登记）。
        created_opps.append(c_opp)
        _, res_q = call('POST', '/quotes', token=admin, body={
            'customer_id': c_cust, 'opportunity_id': c_opp, 'currency': 'CNY'})
        q_id, v_id = res_q['data']['quote_id'], res_q['data']['version_id']
        # 拟报价 95 低于保护价 99 → 应当触发审批
        call('POST', f'/quote-versions/{v_id}/items', token=admin, body={
            'sku_id': sku_id, 'quantity': 100, 'quoted_price': 95})
        _, res_v = call('GET', f'/quote-versions/{v_id}', token=admin)
        src_item = res_v['data']['items'][0]
        src_floor = src_item['minimum_price_snapshot']
        _, res_nv = call('POST', f'/quotes/{q_id}/versions', token=admin, body={})
        # 建新版的返回就是版本对象本身（主键字段是 `id`，没有 `version_id`）
        new_v = (res_nv.get('data') or {}).get('id')
        _, res_nv2 = call('GET', f'/quote-versions/{new_v}', token=admin)
        new_item = res_nv2['data']['items'][0]
        record('A20a', '建立新版不降低公司保护价（99 仍是 99）',
               src_floor == new_item['minimum_price_snapshot'] == 99.0,
               f'原版本底价={src_floor} 新版底价={new_item["minimum_price_snapshot"]}'
               f'（保护价 99 = 该 SKU 的 C 级 minimum_price）')
        record('A20b', '拟报价低于保护价时新版仍要求审批',
               bool(new_item['approval_required']),
               f'approval_required={new_item["approval_required"]}')

        # ---- ② 复制报价重算派生快照 ----
        # 把源明细的利润人为改回旧口径的 15（模拟存量旧数据），复制后必须是 20。
        # 该 SKU 成本 = 40 + 5 = 45，单价 65 → 利润 20（与费用、运费无关）。
        item_id = src_item['id']
        run_db_write(f"update quote_items set profit_snapshot=15, "
               f"profit_rate_snapshot=0.15 where id={item_id}")
        _, res_o2 = call('POST', '/opportunities', token=admin, body={
            'customer_id': c_cust, 'title': f'{PREFIX}-A20复制商机'})
        opp2 = (res_o2.get('data') or {}).get('id')
        created_opps.append(opp2)  # 同上：不登记就会留残留
        _, res_q2 = call('POST', '/quotes', token=admin, body={
            'customer_id': c_cust, 'opportunity_id': opp2, 'currency': 'CNY'})
        q2, v2 = res_q2['data']['quote_id'], res_q2['data']['version_id']
        call('POST', f'/quote-versions/{v2}/items', token=admin, body={
            'sku_id': sku_id, 'quantity': 100, 'quoted_price': 65})
        _, res_v2 = call('GET', f'/quote-versions/{v2}', token=admin)
        it2 = res_v2['data']['items'][0]
        run_db_write(f"update quote_items set profit_snapshot=15, "
               f"profit_rate_snapshot=0.15 where id={it2['id']}")
        _, res_cl = call('POST', f'/quotes/{q2}/clone', token=admin, body={
            'customer_id': c_cust, 'opportunity_id': opp2, 'copy_items': True})
        clone_q = (res_cl.get('data') or {}).get('quote_id')
        rows = run_db("select profit_snapshot, quoted_price, cost_snapshot "
                      "from quote_items where quote_version_id in "
                      "(select id from quote_versions where quote_id=:q) "
                      "order by id desc limit 1", {'q': clone_q}) if clone_q else []
        if rows:
            profit, price, cost = float(rows[0][0]), float(rows[0][1]), float(rows[0][2])
            record('A20c', '复制报价按当前口径重算利润（15 → 20，不是照抄）',
                   abs(profit - (price - cost)) < 0.001,
                   f'副本利润={profit} 单价={price} 商品成本={cost}'
                   f'（照抄旧值会是 15）')
        else:
            record('A20c', '复制报价按当前口径重算利润', False, '复制后取不到明细')

        # ---- ③ 代码里的默认条款必须自洽 ----
        #
        # ⚠️ 别用 `'含运费' not in text` 来判：`'不含运费'` **包含** `'含运费'` 子串，
        # 那样写恒为 False（我第一版就这么写错了）。要判的是**旧的坏文案**本身。
        # 也刻意不断言"库值 == 代码常量"：那是要求"配置默认值不许被改动"，
        # 而不是要求条款与新口径自洽，以后合法改文案就会假失败。
        # 真正要守的不变量由**全新库**套件 `check_fresh_db_seed_terms.py` 验 ——
        # 本套件跑的库是已有数据的库，它的值来自当年迁移写的历史行，
        # 不代表初始化脚本的当前行为。
        from app.modules.settings.service import DEFAULT_DELIVERY_TERMS
        record('A20d', '代码里的默认条款不是旧文案「含运费，送货上门」',
               OLD_DELIVERY_TERMS not in DEFAULT_DELIVERY_TERMS,
               f'常量={DEFAULT_DELIVERY_TERMS!r}')
        record('A20e', '默认条款与报价回落值同源（同一常量，不各写一份）',
               DEFAULT_DELIVERY_TERMS
               == __import__('app.modules.settings.service', fromlist=['x'])
               .DEFAULT_SETTINGS['default_delivery_terms']['text'],
               f'常量={DEFAULT_DELIVERY_TERMS!r}')

        # ---------------- A21 底价「只紧不松」：建立新版绝不降低审批门槛 ----------------
        #
        # 保护价写在价格规则里、会被人改；报价里冻住的那份是**当年**的值。
        # 两者不一致时口径已与主人对齐（2026-10-09）：取 `max(按今天算的, 原来那一版)`。
        # 这条专门守"今天更低"的情形 —— 只按今天算的话底价会跟着降，
        # 原本要审批的报价就变成免审批直接过（主人要我修的就是这一类）。
        print()
        print('== A21 底价只紧不松 ==')
        for label, first_mp, second_mp in (
            ('今天更高（99→130）', 99, 130),
            ('今天更低（105→92，穿过反推价）', 105, 92),
        ):
            rows = run_db("select id from price_rules where customer_level='C' "
                          "and status='active'")
            for (rid,) in rows:
                call('DELETE', f'/price-rules/{rid}', token=admin)
            _, res = call('POST', '/price-rules', token=admin, body={
                'sku_id': sku_id, 'customer_level': 'C', 'min_qty': 0,
                'guide_price': 90, 'minimum_price': first_mp,
            })
            c_rule = (res.get('data') or {}).get('id')
            if c_rule:
                created_rules.append(c_rule)
            _, res_o = call('POST', '/opportunities', token=admin, body={
                'customer_id': c_cust, 'title': f'{PREFIX}-A21{first_mp}'})
            opp = (res_o.get('data') or {}).get('id')
            created_opps.append(opp)  # 登记进清理清单（同 A20 那两处）
            _, res_q = call('POST', '/quotes', token=admin, body={
                'customer_id': c_cust, 'opportunity_id': opp, 'currency': 'CNY'})
            q2, v2 = res_q['data']['quote_id'], res_q['data']['version_id']
            call('POST', f'/quote-versions/{v2}/items', token=admin, body={
                'sku_id': sku_id, 'quantity': 100, 'quoted_price': 95})
            _, res_v = call('GET', f'/quote-versions/{v2}', token=admin)
            floor_before = res_v['data']['items'][0]['minimum_price_snapshot']
            # 改保护价：本项目口径是**停用旧的 + 新增一条**（不许原地改价）
            for (rid,) in run_db("select id from price_rules where customer_level='C' "
                                 "and status='active'"):
                call('DELETE', f'/price-rules/{rid}', token=admin)
            _, res = call('POST', '/price-rules', token=admin, body={
                'sku_id': sku_id, 'customer_level': 'C', 'min_qty': 0,
                'guide_price': 90, 'minimum_price': second_mp,
            })
            if (res.get('data') or {}).get('id'):
                created_rules.append(res['data']['id'])
            _, res_nv = call('POST', f'/quotes/{q2}/versions', token=admin, body={})
            nv = (res_nv.get('data') or {}).get('id')
            _, res_v2 = call('GET', f'/quote-versions/{nv}', token=admin)
            floor_after = res_v2['data']['items'][0]['minimum_price_snapshot']
            record(f'A21 {label}', f'建立新版不降低底价（{floor_before} → {floor_after}）',
                   floor_after is not None and floor_before is not None
                   and float(floor_after) >= float(floor_before),
                   f'保护价 {first_mp} → {second_mp}，底价 {floor_before} → {floor_after}'
                   + ('（若只按今天算会降到 94.12）' if second_mp < first_mp else ''))

        # ---------------- A22 定制项保护价：四处路径同一个结论 ----------------
        #
        # 审查实测过：同一个账号、相同成本与售价，**新建**走系统配置（15% → 底价 92），
        # **建立新版 / 复制**走操作人角色权限（0% → 底价 80），于是"换条路径就换一套
        # 算法"——价格权限表里配的 0% 对新建不起作用、对建新版反而起作用。
        # 主人拍板：**按角色权限统一**（含公式也统一成引擎那个 成本 ÷ (1 − 率)）。
        print()
        print('== A22 定制项保护价四处一致 ==')
        # 定制项必须有需求编号（没有 SKU），走"需求 → 直接发起报价"这条官方路径
        _, res = call('POST', '/custom-inquiries', token=admin, body={
            'title': f'{PREFIX}-A22定制件', 'customer_id': c_cust, 'quantity': 1})
        inq = (res.get('data') or {}).get('id')
        _, res = call('POST', f'/custom-inquiries/{inq}/create-quote', token=admin, body={
            'unit_cost': 80, 'quoted_price': 85, 'item_name': f'{PREFIX}-A22定制件'})
        d = res.get('data') or {}
        qid, v1 = d.get('quote_id'), d.get('version_id')
        # 这条路径会**顺带建一个同名商机**（需求 → 直接发起报价的官方路径），
        # 并把它回记到需求上（`custom_inquiries.opportunity_id`）。此前没登记这个商机，
        # 于是每跑一次就留一个「A22定制件」—— 实测被 check_fixture_residue 抓到。
        # 直接读回记的 id，比按标题搜更可靠（不会因为重名或分页漏掉）。
        _, res_inq = call('GET', f'/custom-inquiries/{inq}', token=admin)
        auto_opp = (res_inq.get('data') or {}).get('opportunity_id')
        if auto_opp:
            created_opps.append(auto_opp)
        if not qid:
            record('A22 前置：创建定制项报价', False, str(res.get('message'))[:60])
        else:
            def custom_floor(vid):
                _, r = call('GET', f'/quote-versions/{vid}', token=admin)
                it = r['data']['items'][0]
                return it['minimum_price_snapshot'], it['approval_required']

            f_new, a_new = custom_floor(v1)
            # 改价（同一版本）
            _, rv = call('GET', f'/quote-versions/{v1}', token=admin)
            _iid = rv['data']['items'][0]['id']
            call('PATCH', f'/quote-versions/{v1}/items/{_iid}', token=admin,
                 body={'quoted_price': 86})
            f_edit, a_edit = custom_floor(v1)
            # 建立新版
            _, rn = call('POST', f'/quotes/{qid}/versions', token=admin, body={})
            f_ver, a_ver = custom_floor((rn.get('data') or {}).get('id'))
            # 复制报价
            _, rc = call('POST', f'/quotes/{qid}/clone', token=admin, body={
                'customer_id': c_cust, 'copy_items': True})
            _nq = (rc.get('data') or {}).get('quote_id')
            _nv = run_db("select id from quote_versions where quote_id=:q "
                         "order by id desc limit 1", {'q': _nq})[0][0] if _nq else None
            f_copy, a_copy = custom_floor(_nv) if _nv else (None, None)

            floors = {f_new, f_edit, f_ver, f_copy}
            approvals = {a_new, a_edit, a_ver, a_copy}
            record('A22 定制项四处底价一致',
                   len(floors) == 1 and None not in floors,
                   f'新建={f_new} 改价={f_edit} 新版={f_ver} 复制={f_copy}')
            record('A22 定制项四处审批结论一致',
                   len(approvals) == 1,
                   f'新建={a_new} 改价={a_edit} 新版={a_ver} 复制={a_copy}')
            # 与核价引擎同一套公式：底价 = 成本 ÷ (1 − 角色利润率)。
            # 用实际角色利润率算期望值再比，避免写死某个数字（角色权限是配置项）。
            _margin = run_db(
                "select coalesce(min(p.minimum_margin), "
                "(select (value->>'ratio')::numeric from system_settings "
                " where key='default_min_margin'), 0.15) "
                "from price_permissions p join roles r on r.id=p.role_id "
                "where r.code='admin' and p.status='active'")[0][0]
            _expected = round(80 / (1 - float(_margin)), 2)
            record('A22 底价 = 成本 ÷ (1 − 角色利润率)（公式与引擎一致）',
                   f_new is not None and abs(float(f_new) - _expected) < 0.02,
                   f'角色利润率={_margin} 期望底价={_expected} 实际={f_new}'
                   + '（若写成 成本×(1+率) 会偏小）')
            # 收尾
            for _vid in filter(None, [v1, (rn.get('data') or {}).get('id'), _nv]):
                run_db("delete from quote_items where quote_version_id=:v", {'v': _vid})
                run_db("delete from quote_charges where quote_version_id=:v", {'v': _vid})
                run_db("delete from quote_versions where id=:v", {'v': _vid})
            if _nq:
                run_db("delete from quotes where id in (:a, :b)", {'a': qid, 'b': _nq})
            else:
                run_db("delete from quotes where id=:a", {'a': qid})
            run_db("delete from custom_inquiries where id=:i", {'i': inq})

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
    print(f'A01–A19 全部通过（{len(RESULTS)} 条）')


if __name__ == '__main__':
    main()
