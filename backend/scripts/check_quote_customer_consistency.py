#!/usr/bin/env python3
"""报价的**客户与商机必须同一家**（主人 2026-10-12 拍板的口径）。

背景（先写清楚，免得后来人以为在堵一个不存在的漏洞）：

    `create_quote` 里**客户以商机为准** —— 传了商机就用商机的客户覆盖请求里的
    `customer_id`。所以"把 A 的商机挂进 B 的报价"**本来就发生不了**：报价会变成 A 的。
    横向摸排的第一版把这句"返回 0"误判成了口子（probe 的期望写反了），
    核对落库数据才发现真相。

    真正的毛病是**参数互相矛盾却被当成成功**：前端选了 A 客户、又挂着 B 的商机时，
    请求里的 `customer_id` 被**静默丢掉**，报价按商机建出来，界面上看不出任何异常 ——
    直到有人发现"这单怎么记在另一家头上"。
    主人定的口径：**不一致就拒，并说清怎么办**（与"联系人不属于客户"同一套写法）。

跑法（一次性库 + 隔离后端）：
    DB=crm_check_opp_scope
    psql -h 127.0.0.1 -U postgres -d postgres -c "CREATE DATABASE $DB OWNER crm;"
    cd backend
    export DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5432/$DB
    PYTHONPATH=. .venv/bin/python -m alembic upgrade head
    PYTHONPATH=. .venv/bin/python -m scripts.seed
    PYTHONPATH=. DATABASE_URL=$DATABASE_URL WECOM_PUSH_OFF=1 DINGTALK_PUSH_OFF=1 \
      SCHEDULER_ENABLED=false .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8065 &
    API_BASE=http://127.0.0.1:8065/api/v1 PYTHONPATH=. DATABASE_URL=$DATABASE_URL \
      .venv/bin/python -u scripts/check_quote_customer_consistency.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _test_support import require_api_base, require_isolated_db  # noqa: E402

RUN = os.environ.get('CHK_RUN', 'QCC')
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


def main() -> None:
    require_isolated_db()
    admin = login('admin', 'admin123')

    print()
    print('=== 准备：两个客户 A / B，各一个商机 ===')
    _, res = call('POST', '/customers', token=admin, body={'name': f'{RUN}客户A'})
    customer_a = res['data']['id']
    _, res = call('POST', '/customers', token=admin, body={'name': f'{RUN}客户B'})
    customer_b = res['data']['id']
    _, res = call('POST', '/opportunities', token=admin, body={
        'title': f'{RUN}A的商机', 'customer_id': customer_a,
    })
    opp_a = res['data']['id']
    _, res = call('POST', '/opportunities', token=admin, body={
        'title': f'{RUN}B的商机', 'customer_id': customer_b,
    })
    opp_b = res['data']['id']
    check_true('两个客户、两个商机建好了', customer_a != customer_b and opp_a != opp_b,
               f'A={customer_a} B={customer_b} oppA={opp_a} oppB={opp_b}')

    print()
    print('=== 1. 正常路径：只给商机 → 报价属于商机的那家（不许误伤）===')
    status, res = call('POST', '/quotes', token=admin, body={
        'opportunity_id': opp_b, 'currency': 'CNY',
    })
    check('建报价成功', res.get('code'), 0)
    quote_id = res['data']['quote_id']
    _, detail = call('GET', f'/quotes/{quote_id}', token=admin)
    check('报价客户 = B（跟着商机走）', detail['data']['customer_id'], customer_b)
    check('报价商机 = B 的商机', detail['data']['opportunity_id'], opp_b)

    print()
    print('=== 2. 【核心】B 的商机 + A 的客户（参数互相矛盾）→ 必须拒，并说清怎么办 ===')
    status, res = call('POST', '/quotes', token=admin, body={
        'opportunity_id': opp_b, 'customer_id': customer_a, 'currency': 'CNY',
    })
    check('客户与商机不是同一家时被拒', res.get('code'), 40001)
    message = str(res.get('message', ''))
    check_true('拒绝理由点名了两个 id 与商机名、并给了出路',
               '不是同一家' in message and str(customer_a) in message
               and str(customer_b) in message and '以商机为准' in message,
               message[:120])
    status, res = call('POST', '/quotes', token=admin, body={
        'opportunity_id': opp_a, 'customer_id': customer_b, 'currency': 'CNY',
    })
    check('反方向（A 的商机 + B 的客户）同样被拒', res.get('code'), 40001)

    print()
    print('=== 3. 两个参数都给、且对得上 → 照常放行 ===')
    status, res = call('POST', '/quotes', token=admin, body={
        'opportunity_id': opp_b, 'customer_id': customer_b, 'currency': 'CNY',
    })
    check('一致时放行', res.get('code'), 0)

    print()
    print('=== 4. 复制报价这条路不受影响（它本来就不带商机参数）===')
    status, res = call('POST', f'/quotes/{quote_id}/clone', token=admin, body={})
    check_true('复制报价没被新判据误伤',
               not (res.get('code') == 40001 and '不是同一家' in str(res.get('message', ''))),
               f"code={res.get('code')} {res.get('message')!r}")


async def cleanup(verbose: bool = False) -> None:
    """清掉本套件造的客户/商机/报价（守门套件 check_fixture_residue 在最后跑）。"""
    from sqlalchemy import text

    from app.core.database import SessionLocal

    pattern = f'{RUN}%'
    async with SessionLocal() as session:
        quote_ids = (await session.execute(text(
            'select q.id from quotes q join customers c on c.id = q.customer_id '
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
        await session.execute(text(
            'delete from opportunity_stage_history where opportunity_id in '
            '(select o.id from opportunities o join customers c on c.id = o.customer_id '
            ' where c.name like :p)'
        ), {'p': pattern})
        await session.execute(text(
            'delete from opportunity_items where opportunity_id in '
            '(select o.id from opportunities o join customers c on c.id = o.customer_id '
            ' where c.name like :p)'
        ), {'p': pattern})
        await session.execute(text(
            'delete from opportunities where customer_id in '
            '(select c.id from customers c where c.name like :p)'
        ), {'p': pattern})
        await session.execute(text(
            'delete from customers where name like :p'
        ), {'p': pattern})
        await session.commit()
        if verbose:
            print(f'  （清掉 {len(quote_ids)} 张报价及其客户链）')


async def _driver() -> None:
    """清理与主体同一个事件循环（SQLAlchemy 异步引擎的连接池绑定创建它的循环）。"""
    print()
    print('=== 0. 先清上次残留 ===')
    await cleanup(verbose=True)
    try:
        main()
    finally:
        print()
        print('=== 收尾：清掉本套件造的夹具 ===')
        await cleanup(verbose=True)


if __name__ == '__main__':
    asyncio.run(_driver())
    print()
    if FAILURES:
        print(f'FAILED（{len(FAILURES)}）: {FAILURES}')
        sys.exit(1)
    print('全部通过')
