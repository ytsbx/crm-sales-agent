#!/usr/bin/env python3
"""定制需求与报价的**客户一致性**（2026-10-12 主人提问核实）。

主人问的原话：
    「定制需求下拉不按客户过滤的口子（能把 A 客户的需求挂进 B 客户的报价）。有这个问题吗」

核实结论：**有**。分三层，逐层断开都算这个问题：

1. **后端没有"需求与报价必须同一客户"的判据** ——
   `quote/service._build_custom_item_snapshot` 只判"需求存在且未删"；
   `quote/router._ensure_inquiry_visible` 只判"需求在我数据范围内"。
   所以同一个人手里的两个客户可以互相挂（A 的需求 → B 的报价），
   而且需求标题/编号会落进**快照**、跟着对客文件流出去。
2. **前端下拉根本不按客户过滤** —— `QuoteDetailPage` 拉的是
   `listCustomInquiries({ page, page_size })`，把全部需求铺出来；
   而列表接口**本来就支持 `customer_id`**（`inquiry/router.py` 有那个参数），
   是前端没传、后端也没拦。
3. **子表的客户与商机之间也没有一致性判据**（商机属于别的客户也能挂）。

这个脚本按"接口能接受什么"逐条打出来：先**复现**（当前行为），
修完之后同一套探针必须变成拒绝。跑法见 docstring 末尾。

跑法（一次性库 + 隔离后端）：
    DB=crm_check_inquiry_scope
    psql -h 127.0.0.1 -U postgres -d postgres -c "CREATE DATABASE $DB OWNER crm;"
    cd backend
    export DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5432/$DB
    PYTHONPATH=. .venv/bin/python -m alembic upgrade head
    PYTHONPATH=. .venv/bin/python -m scripts.seed
    PYTHONPATH=. DATABASE_URL=$DATABASE_URL WECOM_PUSH_OFF=1 DINGTALK_PUSH_OFF=1 \\
      SCHEDULER_ENABLED=false .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8062 &
    API_BASE=http://127.0.0.1:8062/api/v1 PYTHONPATH=. DATABASE_URL=$DATABASE_URL \\
      .venv/bin/python -u scripts/check_inquiry_quote_scope.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _test_support import require_api_base, require_isolated_db  # noqa: E402

RUN = os.environ.get('CHK_RUN', 'IQS')

FAILURES: list[str] = []


def check(label: str, actual, expected) -> None:
    good = actual == expected
    print(f'  {"OK  " if good else "FAIL"} {label}: {actual!r}（期望 {expected!r}）')
    if not good:
        FAILURES.append(label)


def check_true(label: str, condition: bool, detail: str = '') -> None:
    print(f'  {"OK  " if condition else "FAIL"} {label}{f"：{detail}" if detail else ""}')
    if not condition:
        FAILURES.append(label)


def call(method: str, path: str, token: str | None = None, body=None):
    url = f'{require_api_base()}{path}'
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header('Content-Type', 'application/json')
    if token:
        req.add_header('Authorization', f'Bearer {token}')
    try:
        with urllib.request.urlopen(req) as res:
            return res.status, json.loads(res.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def login(username: str, password: str) -> str:
    status, res = call('POST', '/auth/login', body={'username': username, 'password': password})
    assert res.get('code') == 0, res
    return res['data']['access_token']


async def cleanup(verbose: bool = False) -> None:
    """清掉本套件造的夹具（客户 / 商机 / 报价 / 需求）。

    为什么要自己清（不是"顺手"）：`ops/check_suites.txt` 的**最后一条**是
    `check_fixture_residue`，它断言"前面所有套件跑完没留下可见夹具"。
    不清就会把守门套件弄红，而红的原因还是我自己造成的。
    删的顺序按外键从子到父；`LIKE` 用的前缀与建的时候同一份（`RUN`）。
    """
    from sqlalchemy import text

    from app.core.database import SessionLocal

    pattern = f'{RUN}%'
    async with SessionLocal() as session:
        quote_ids = (await session.execute(text(
            "select q.id from quotes q join customers c on c.id = q.customer_id "
            'where c.name like :p'
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
        # 需求挂在客户上；商机由报价引用
        inquiry_ids = (await session.execute(text(
            'select i.id from custom_inquiries i join customers c on c.id = i.customer_id '
            'where c.name like :p'
        ), {'p': pattern})).scalars().all()
        for inquiry_id in inquiry_ids:
            i = {'i': int(inquiry_id)}
            await session.execute(text(
                "delete from audit_logs where business_type='custom_inquiry' and business_id = :i"
            ), i)
            await session.execute(text('delete from custom_inquiries where id = :i'), i)
        opp_ids = (await session.execute(text(
            'select o.id from opportunities o join customers c on c.id = o.customer_id '
            'where c.name like :p'
        ), {'p': pattern})).scalars().all()
        for opp_id in opp_ids:
            o = {'o': int(opp_id)}
            await session.execute(text('delete from opportunity_stage_history where opportunity_id = :o'), o)
            await session.execute(text('delete from opportunity_items where opportunity_id = :o'), o)
            await session.execute(text('delete from opportunities where id = :o'), o)
        customer_ids = (await session.execute(text(
            'select id from customers where name like :p'
        ), {'p': pattern})).scalars().all()
        for customer_id in customer_ids:
            c = {'c': int(customer_id)}
            await session.execute(text('delete from contacts where customer_id = :c'), c)
            await session.execute(text('delete from audit_logs where business_type=\'customer\' and business_id = :c'), c)
            await session.execute(text('delete from customers where id = :c'), c)
        await session.commit()
        if verbose:
            print(f'  清掉：客户 {len(customer_ids)}、商机 {len(opp_ids)}、'
                  f'报价 {len(quote_ids)}、需求 {len(inquiry_ids)}')


def main() -> None:
    require_isolated_db()
    admin = login('admin', 'admin123')


    print()
    print('=== 准备：两个客户 A / B，各自一条定制需求 ===')
    status, res = call('POST', '/customers', token=admin, body={'name': f'{RUN}客户A'})
    check('建客户 A', res.get('code'), 0)
    customer_a = res['data']['id']
    status, res = call('POST', '/customers', token=admin, body={'name': f'{RUN}客户B'})
    check('建客户 B', res.get('code'), 0)
    customer_b = res['data']['id']
    check_true('A、B 是两个客户', customer_a != customer_b, f'{customer_a} vs {customer_b}')

    status, res = call('POST', '/custom-inquiries', token=admin, body={
        'title': f'{RUN}A客户的需求', 'customer_id': customer_a, 'quantity': 10,
    })
    check('建 A 的需求', res.get('code'), 0)
    inquiry_a = res['data']['id']
    check('需求落在 A 名下', res['data']['customer_id'], customer_a)

    status, res = call('POST', '/custom-inquiries', token=admin, body={
        'title': f'{RUN}B客户的需求', 'customer_id': customer_b, 'quantity': 20,
    })
    check('建 B 的需求', res.get('code'), 0)
    inquiry_b = res['data']['id']

    print()
    print('=== 准备：给 B 客户建一张报价（草稿即可） ===')
    # 报价必须挂商机（40001「报价必须关联商机」），所以先给 B 造一个商机
    status, res = call('POST', '/opportunities', token=admin, body={
        'title': f'{RUN}B的商机', 'customer_id': customer_b,
    })
    check('建 B 的商机', res.get('code'), 0)
    opp_b = res['data']['id']
    status, res = call('POST', '/quotes', token=admin, body={
        'customer_id': customer_b, 'opportunity_id': opp_b, 'currency': 'CNY',
    })
    check('建 B 的报价', res.get('code'), 0)
    if res.get('code') != 0:
        print(f'  → 建报价失败：{res.get("message")!r}，后续无法验证')
        return
    version_b = res['data']['version_id']

    print()
    print('=== 1. 【核心】把 A 客户的需求挂进 B 客户的报价 ===')
    status, res = call('POST', f'/quote-versions/{version_b}/items', token=admin, body={
        'inquiry_id': inquiry_a,
        'item_name': f'{RUN}挂错客户的明细',
        'quantity': 1,
        'quoted_price': 100,
        'unit_cost': 60,
    })
    print(f'  → 接口返回 code={res.get("code")} message={res.get("message")!r}')
    check('跨客户挂需求被拒（这是主人问的那个口子）', res.get('code'), 40001)
    check_true('拒绝理由点名了是哪条需求、并说了怎么办',
               '属于别的客户' in str(res.get('message', '')) and '先为该客户建一条定制需求' in str(res.get('message', '')),
               str(res.get('message'))[:100])

    # 真挂上了就会留下明细 —— 有明细就是漏了
    status, res = call('GET', f'/quote-versions/{version_b}/items', token=admin)
    items = res.get('data') or []
    leaked = [i for i in items if i.get('inquiry_id') == inquiry_a]
    check_true('B 的报价里没有落进 A 的需求明细', not leaked, f'明细={items}')
    if leaked:
        print(f'     ↳ 落进去的快照：{json.dumps(leaked[0], ensure_ascii=False)[:200]}')

    print()
    print('=== 2. 同客户的正常路径不能被误伤 ===')
    status, res = call('POST', f'/quote-versions/{version_b}/items', token=admin, body={
        'inquiry_id': inquiry_b,
        'item_name': f'{RUN}B自己的需求',
        'quantity': 1,
        'quoted_price': 100,
        'unit_cost': 60,
    })
    check('B 的需求挂进 B 的报价：放行', res.get('code'), 0)
    own_item_id = res['data'].get('id') if res.get('code') == 0 else None

    print()
    print('=== 3. 需求不存在 / 已删：仍是 404（不能被上面那条改动搞混） ===')
    status, res = call('POST', f'/quote-versions/{version_b}/items', token=admin, body={
        'inquiry_id': 99999999, 'quantity': 1, 'quoted_price': 100, 'unit_cost': 60,
    })
    check('不存在的需求被拒（业务码 40401）', res.get('code'), 40401)

    print()
    print('=== 4. 批次录入（整版替换）这条路也要挡 ===')
    # ⚠️ batch 的 body 是**裸数组**（`items: list[QuoteItemInput]`），不是 {"items": [...]}
    status, res = call('POST', f'/quote-versions/{version_b}/items/batch', token=admin, body=[{
        'inquiry_id': inquiry_a, 'item_name': f'{RUN}批次挂错客户',
        'quantity': 1, 'quoted_price': 100, 'unit_cost': 60,
    }])
    print(f'  → 接口返回 code={res.get("code")} message={res.get("message")!r}')
    check('批次里跨客户挂需求也被拒', res.get('code'), 40001)

    print()
    print('=== 5. 需求列表接口本身支持按客户过滤（前端要传上） ===')
    status, res = call('GET', f'/custom-inquiries?customer_id={customer_a}&page=1&page_size=50', token=admin)
    check('按 customer_id 过滤能取到 A 的需求', res.get('code'), 0)
    ids = [r['id'] for r in res['data']['items']]
    check_true('过滤结果里只有 A 的需求', ids and all(i == inquiry_a for i in ids), str(ids))

    print()
    print('=== 6. 编辑明细这条路**结构上**改不了"挂哪条需求" ===')
    # `QuoteItemUpdate` 的字段只有 quantity / quoted_price / unit_cost / logistics_cost / remark
    # —— 入参里**根本没有 inquiry_id**。所以"新建时判"就等于"全生命周期判"（见 service 的注释）。
    # 这里证明的是：就算往 body 里塞 inquiry_id，它既不会被采纳，明细也不会改挂到别家需求上。
    if own_item_id:
        status, res = call('PATCH', f'/quote-items/{own_item_id}', token=admin, body={
            'inquiry_id': inquiry_a, 'remark': f'{RUN}试图改挂',
        })
        check('接口接受了这次改动（只改备注）', res.get('code'), 0)
        status, res = call('GET', f'/quote-versions/{version_b}/items', token=admin)
        row = next((i for i in (res.get('data') or []) if i['id'] == own_item_id), None)
        check_true('明细还挂在**自己的**需求上（没被改挂到别家）',
                   row is not None and row.get('inquiry_id') == inquiry_b,
                   f"inquiry_id={row.get('inquiry_id') if row else None}")
    else:
        check_true('（跳过）第 2 步没建出明细，无法验证', True, '见上一节的失败')


async def _driver() -> None:
    """一个事件循环跑完全程。

    ⚠️ 不能"先 asyncio.run(清理) 再 asyncio.run(主体)"：SQLAlchemy 的异步引擎
    把连接池绑在**创建它的事件循环**上，第二个 `asyncio.run` 会换一个循环，
    于是报 `attached to a different loop`（实测踩过）。
    """
    print()
    print('=== 0. 先清上一次跑到一半留下的夹具（脚本中途失败也能自愈）===')
    await cleanup(verbose=True)
    try:
        main()
    finally:
        print()
        print('=== 收尾：清掉本套件造的夹具 ===')
        await cleanup(verbose=True)


if __name__ == '__main__':
    import asyncio

    asyncio.run(_driver())
    print()
    if FAILURES:
        print(f'FAILED（{len(FAILURES)}）: {FAILURES}')
        sys.exit(1)
    print('全部通过')
