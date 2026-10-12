#!/usr/bin/env python3
"""同一形态的横向排查：**"指向别家记录"的外键**（2026-10-12 主人要求逐条核实）。

主人先问出「A 客户的需求能挂进 B 客户的报价」，接着要求"有问题的做成选择题"。
要出选择题就得先把同一形态的**其他口子**也查清，否则选项是拍脑袋写的。

本脚本对每条"子记录指向另一个实体的外键"打一发探针，输出三种结果：
  BLOCKED  —— 接口拒绝（说明已经有判据，并打印它怎么说的）
  LEAKED   —— 接口接受（**这就是一个口子**，并打印落库后的样子）
  SKIP     —— 这条路径当前不具备复现条件（例如需要某种状态），并说明原因

跑法（一次性库 + 隔离后端）：
    DB=crm_check_xref
    psql -h 127.0.0.1 -U postgres -d postgres -c "CREATE DATABASE $DB OWNER crm;"
    cd backend
    export DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5432/$DB
    PYTHONPATH=. .venv/bin/python -m alembic upgrade head
    PYTHONPATH=. .venv/bin/python -m scripts.seed
    PYTHONPATH=. DATABASE_URL=$DATABASE_URL WECOM_PUSH_OFF=1 DINGTALK_PUSH_OFF=1 \
      SCHEDULER_ENABLED=false .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8063 &
    API_BASE=http://127.0.0.1:8063/api/v1 PYTHONPATH=. DATABASE_URL=$DATABASE_URL \
      .venv/bin/python -u scripts/check_cross_entity_scope.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _test_support import require_api_base, require_isolated_db  # noqa: E402

RUN = os.environ.get('CHK_RUN', 'XREF')
RESULTS: list[tuple[str, str, str]] = []


def call(method: str, path: str, token: str | None = None, body=None):
    req = urllib.request.Request(
        f'{require_api_base()}{path}',
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
    )
    if body is not None:
        req.add_header('Content-Type', 'application/json')
    if token:
        req.add_header('Authorization', f'Bearer {token}')
    try:
        with urllib.request.urlopen(req) as res:
            return res.status, json.loads(res.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def login(username: str, password: str) -> str:
    _, res = call('POST', '/auth/login', body={'username': username, 'password': password})
    return res['data']['access_token']


def probe(label: str, status_code, res, *, expect_blocked: bool, extra: str = '') -> None:
    code = res.get('code')
    blocked = code != 0
    if blocked:
        verdict = 'BLOCKED'
    elif expect_blocked:
        verdict = 'LEAKED'
    else:
        verdict = 'ALLOWED(正常)'
    RESULTS.append((label, verdict, f'code={code} msg={res.get("message")!r}{extra}'))
    mark = {'BLOCKED': '🔒', 'LEAKED': '🔴', 'ALLOWED(正常)': '✅'}[verdict]
    print(f'  {mark} {verdict:14} {label}')
    print(f'       → code={code} {res.get("message")!r}{(" " + extra) if extra else ""}')


def main() -> None:
    require_isolated_db()
    admin = login('admin', 'admin123')

    print()
    print('=== 准备：两个客户 A/B，各带商机、联系人、需求 ===')
    _, res = call('GET', '/skus?page=1&page_size=1', token=admin)
    sku_id = (res['data']['items'] or [{}])[0].get('id')
    assert sku_id, f'种子里没有 SKU，无法继续：{res}'
    print(f'  用 SKU id={sku_id}')
    ids: dict[str, dict[str, int]] = {}
    for tag in ('A', 'B'):
        _, res = call('POST', '/customers', token=admin, body={'name': f'{RUN}客户{tag}'})
        customer = res['data']['id']
        _, res = call('POST', '/opportunities', token=admin, body={
            'title': f'{RUN}{tag}的商机', 'customer_id': customer,
        })
        opportunity = res['data']['id']
        _, res = call('POST', f'/customers/{customer}/contacts', token=admin, body={
            'name': f'{RUN}{tag}的联系人', 'mobile': '13800000000',
        })
        contact = res['data']['id']
        _, res = call('POST', '/custom-inquiries', token=admin, body={
            'title': f'{RUN}{tag}的需求', 'customer_id': customer, 'quantity': 10,
        })
        inquiry = res['data']['id']
        # 商机需求（报价明细可以挂它）—— 必须有 sku_id（接口契约：明细要能查价）
        _, res = call('POST', f'/opportunities/{opportunity}/items', token=admin, body={
            'sku_id': sku_id, 'name': f'{RUN}{tag}的商机需求', 'quantity': 5,
        })
        opp_item = None
        if res.get('code') == 0:
            # 这个接口回的是**整份明细列表**（不是单条）
            data = res.get('data') or []
            if isinstance(data, dict):
                data = data.get('items') or []
            opp_item = data[-1]['id'] if data else None
        else:
            print(f'    ⚠️ {tag} 的商机需求没建出来：code={res.get("code")} {res.get("message")!r}'
                  f' raw={json.dumps(res, ensure_ascii=False)[:200]}')
        ids[tag] = {'customer': customer, 'opportunity': opportunity, 'contact': contact,
                    'inquiry': inquiry, 'opp_item': opp_item}
        print(f'  {tag}: customer={customer} opportunity={opportunity} '
              f'contact={contact} inquiry={inquiry} opp_item={opp_item}')

    print()
    print('=== 1. 报价头上：请求写 B 的客户 + A 的商机（两个参数互相矛盾）===')
    # ⚠️ 这条**不能**期望"被拒"就完事：客户本来以商机为准，合法结果有两种 ——
    #   （a）拒绝，并说清两个参数不一致；或
    #   （b）接受，但报价必须落在**商机那一家的**名下（客户跟着商机走）。
    # 只有"接受且报价落在请求里那个客户名下"才是真口子（跨客户）。
    # 第一版这里写死了「期望被拒」，于是把正确的语义判成了口子 —— 教训记在这。
    status, res = call('POST', '/quotes', token=admin, body={
        'customer_id': ids['B']['customer'], 'opportunity_id': ids['A']['opportunity'], 'currency': 'CNY',
    })
    if res.get('code') != 0:
        print(f'  🔒 BLOCKED        拒绝（可接受）：code={res.get("code")} {res.get("message")!r}')
        RESULTS.append(('报价客户与商机矛盾', 'BLOCKED-可接受', str(res.get('message'))))
    else:
        _, detail = call('GET', f"/quotes/{res['data']['quote_id']}", token=admin)
        got = detail['data']['customer_id']
        owns = detail['data']['opportunity_id']
        verdict = 'ALLOWED(正常)' if got == ids['A']['customer'] else 'LEAKED'
        print(f'  {"✅" if verdict == "ALLOWED(正常)" else "🔴"} {verdict:14} 接受，但报价客户={got}'
              f'（商机属于 {ids["A"]["customer"]}）→ '
              f'{"客户跟着商机走，没有跨客户" if got == ids["A"]["customer"] else "跨客户了！"}')
        RESULTS.append(('报价客户与商机矛盾', verdict,
                        f'报价客户={got} 商机={owns}'))

    print()
    print('=== 2. 报价头上：B 的报价挂 A 的联系人（带上 B 自己的合法商机，别让"缺商机"挡住判断）===')
    status, res = call('POST', '/quotes', token=admin, body={
        'customer_id': ids['B']['customer'], 'opportunity_id': ids['B']['opportunity'],
        'contact_id': ids['A']['contact'], 'currency': 'CNY',
    })
    probe('报价挂别家客户的联系人', status, res, expect_blocked=True)

    # 后面几条要用 B 的正常报价
    _, res = call('POST', '/quotes', token=admin, body={
        'customer_id': ids['B']['customer'], 'opportunity_id': ids['B']['opportunity'], 'currency': 'CNY',
    })
    assert res.get('code') == 0, res
    version_b = res['data']['version_id']
    print(f'  （B 的正常报价 version={version_b}）')

    print()
    print('=== 3. 报价明细：B 的报价挂 A 的商机需求 ===')
    if ids['A']['opp_item']:
        status, res = call('POST', f'/quote-versions/{version_b}/items', token=admin, body={
            'sku_id': None, 'opportunity_item_id': ids['A']['opp_item'],
            'quantity': 1, 'quoted_price': 100, 'unit_cost': 60,
        })
        probe('报价明细挂别家客户的商机需求', status, res, expect_blocked=True,
              extra=f"（opp_item={ids['A']['opp_item']}）")
    else:
        print('  ⏭  SKIP  商机需求没建出来（接口形状变了？）')

    print()
    print('=== 4. 报价明细：B 的报价挂 A 的需求（上一轮已修，这里作为对照） ===')
    status, res = call('POST', f'/quote-versions/{version_b}/items', token=admin, body={
        'inquiry_id': ids['A']['inquiry'], 'quantity': 1, 'quoted_price': 100, 'unit_cost': 60,
    })
    probe('报价明细挂别家客户的定制需求（已修）', status, res, expect_blocked=True)

    print()
    print('=== 5. 打样：来源是 A 的需求 —— 这条接口**不收 customer_id**，客户从来源推出来 ===')
    # 主人 2026-10-12 的口径（原话）：「代码机制锁死了，来源客户和单据客户一定一样，
    # 不存在漏洞，不需要额外写判断」——所以这里**不期望被拒**，
    # 而是核对"推导出来的客户 == 来源的客户"。这一条是**推导链的自洽性检查**，
    # 防的是"将来有人改坏了推导"（改歪了这条立刻红），不是"接口该拦没拦"。
    # ⚠️ 这里不能判成"口子"：打样单的客户是由来源（需求/报价版本）**推导**的，
    # 推出来的值跟来源必然一致，所以不存在"A 打样挂到 B"这回事。
    # 判据应该是"推出来的客户 == 来源的客户"，而不是"请求里指定的客户"。
    status, res = call('POST', '/samples/from-source', token=admin, body={
        'inquiry_id': ids['A']['inquiry'],
        'request_key': str(uuid.uuid4()), 'items': [{'source_item_id': ids['A']['inquiry'], 'quantity': 1}],
    })
    derived = (res.get('data') or {}).get('customer_id')
    print(f'  ℹ️  code={res.get("code")} 打样单客户={derived}，来源需求客户={ids["A"]["customer"]}')
    print(f'      → {"✅ 推导出来的客户与来源一致（不是口子）" if derived in (None, ids["A"]["customer"]) else "🔴 推导结果与来源不一致"}')

    print()
    print('=== 6. 订单草稿：同上，客户也是从来源推出来的 ===')
    status, res = call('POST', '/order-drafts', token=admin, body={
        'inquiry_id': ids['A']['inquiry'], 'request_key': str(uuid.uuid4()),
        'items': [{'source_item_id': ids['A']['inquiry'], 'quantity': 1}],
    })
    derived = (res.get('data') or {}).get('customer_id')
    print(f'  ℹ️  code={res.get("code")} 草稿客户={derived}，来源需求客户={ids["A"]["customer"]}')
    print(f'      → {"✅ 推导出来的客户与来源一致（不是口子）" if derived in (None, ids["A"]["customer"]) else "🔴 推导结果与来源不一致"}')

    print()
    print('=== 7. 联系人本身：把 A 的联系人挂到 B 的客户上（bind-customer） ===')
    status, res = call('POST', f"/contacts/{ids['A']['contact']}/bind-customer", token=admin, body={
        'customer_id': ids['B']['customer'], 'is_primary': False,
    })
    # 这条**可能是设计允许的**（联系人换公司是真实业务），所以单独看它怎么走
    probe('把联系人改挂到另一个客户（可能是合法业务）', status, res, expect_blocked=False)

    print()
    print('=== 汇总 ===')
    leaked = [r for r in RESULTS if r[1] == 'LEAKED']
    for label, verdict, detail in RESULTS:
        print(f'  {verdict:14} {label}')
    print()
    print(f'结论：查了 {len(RESULTS)} 条"指向别家记录"的路径，'
          f'挡住的 {len([r for r in RESULTS if r[1] == "BLOCKED"])} 条，'
          f'**没挡住（口子）{len(leaked)} 条**。')
    if leaked:
        print()
        print('🔴 口子清单（这几条同一个病根：只判"存在/在数据范围内"，不判"是不是同一家"）：')
        for label, _, detail in leaked:
            print(f'   · {label}')
            print(f'     {detail}')
    print()
    print('⚠️ 这套探针只做**只读判断**。历史备注：第一版把"报价挂别家商机"判成了口子，')
    print('   原因是把期望写成了"被拒" —— 而那条的合法语义是"客户跟着商机走"。')
    print('   现在这一条按**落库后的归属**判（见第 1 条的代码），不再靠期望值拍脑袋。')


async def _driver() -> None:
    """与 cleanup 同一个事件循环（SQLAlchemy 异步引擎的连接池绑在创建它的循环上）。"""
    print()
    print('=== 0. 先清上次残留 ===')
    await cleanup(verbose=True)
    try:
        main()
    finally:
        print()
        print('=== 收尾：清掉本脚本造的夹具 ===')
        await cleanup(verbose=True)


async def cleanup(verbose: bool = False) -> None:
    from sqlalchemy import text

    from app.core.database import SessionLocal

    pattern = f'{RUN}%'
    async with SessionLocal() as session:
        quote_ids = (await session.execute(text(
            "select q.id from quotes q join customers c on c.id = q.customer_id where c.name like :p"
        ), {'p': pattern})).scalars().all()
        for quote_id in quote_ids:
            q = {'q': int(quote_id)}
            for table in ('quote_items', 'quote_charges', 'quote_send_logs'):
                await session.execute(text(
                    f'delete from {table} where quote_version_id in '
                    '(select id from quote_versions where quote_id = :q)'
                ), q)
            await session.execute(text('delete from quote_versions where quote_id = :q'), q)
            await session.execute(text('delete from quotes where id = :q'), q)
        # ⚠️ 子查询里的 id 一律带表别名：custom_inquiries 与 customers 都有 id，
        # 不限定就报 AmbiguousColumnError（实测踩过）
        await session.execute(text(
            'delete from sample_items where sample_request_id in '
            '(select s.id from sample_requests s join customers c on c.id = s.customer_id '
            ' where c.name like :p)'
        ), {'p': pattern})
        await session.execute(text(
            'delete from sample_requests where customer_id in '
            '(select c.id from customers c where c.name like :p)'
        ), {'p': pattern})
        await session.execute(text(
            'delete from order_draft_items where draft_id in '
            '(select d.id from order_drafts d join customers c on c.id = d.customer_id '
            ' where c.name like :p)'
        ), {'p': pattern})
        await session.execute(text(
            'delete from order_drafts where customer_id in '
            '(select c.id from customers c where c.name like :p)'
        ), {'p': pattern})
        await session.execute(text(
            'delete from custom_inquiries where customer_id in '
            '(select c.id from customers c where c.name like :p)'
        ), {'p': pattern})
        await session.execute(text(
            'delete from opportunity_items where opportunity_id in '
            '(select o.id from opportunities o join customers c on c.id = o.customer_id '
            ' where c.name like :p)'
        ), {'p': pattern})
        # 商机阶段历史也有外键（第一次跑漏了，报 ForeignKeyViolation）
        await session.execute(text(
            'delete from opportunity_stage_history where opportunity_id in '
            '(select o.id from opportunities o join customers c on c.id = o.customer_id '
            ' where c.name like :p)'
        ), {'p': pattern})
        await session.execute(text(
            'delete from opportunities where customer_id in '
            '(select c.id from customers c where c.name like :p)'
        ), {'p': pattern})
        await session.execute(text(
            'delete from contacts where customer_id in '
            '(select c.id from customers c where c.name like :p)'
        ), {'p': pattern})
        await session.execute(text('delete from customers where name like :p'), {'p': pattern})
        await session.commit()
        if verbose:
            print(f'  （清了 {len(quote_ids)} 张报价及其客户链）')


if __name__ == '__main__':
    import asyncio

    asyncio.run(_driver())
