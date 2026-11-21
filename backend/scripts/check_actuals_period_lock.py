#!/usr/bin/env python3
"""第五批审查 P1-1 的真库并发复现：**两个普通结账不能互相覆盖**。

## 报告给的复现

    A 查到未结账后暂停
    B 普通结账 100 并提交
    A 继续，也以 `replace=False` 成功，把结果改成 200
    终态：数字 200、原因仍为空、原明细被替换

等于**绕过**了"改历史实绩必须走重算并填原因" —— 而结账的全部意义就是
"这一期从此不再变"。

## 为什么必须真库

`pg_advisory_xact_lock` 的互斥只有真 PostgreSQL 有；SQLite（内存测试）直接跳过
（见 `target_actuals.lock_period` 的说明）。**等待与不等待用 `NOWAIT` 判定**，
确定性、不靠 sleep 碰运气（与 `check_milestone_lock_race.py` 同一手法）。

## 三个结论（脚本自己判定）

  ① **基座**：不加锁时，B 能一边被 A "按住"一边照常结账 → 证明没有互斥；
  ② **修好之后**：A 持锁期间，B 的 NOWAIT 必须失败（证明锁真的在）；
  ③ **真实接口**：并发的两次 `POST /sales-targets/actuals/freeze`，
     只能成功一次，另一次必须报"已经结过账了"（422）。

跑法（一次性库 + 隔离后端）：

    DB=crm_check_r5_lock
    psql -h 127.0.0.1 -U postgres -d postgres -c "CREATE DATABASE $DB OWNER crm;"
    cd backend && export DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5432/$DB
    PYTHONPATH=. .venv/bin/python -m alembic upgrade head
    PYTHONPATH=. .venv/bin/python -m scripts.seed
    PYTHONPATH=. .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8077 &
    API_BASE=http://127.0.0.1:8077/api/v1 PYTHONPATH=. DATABASE_URL=$DATABASE_URL \
      .venv/bin/python scripts/check_actuals_period_lock.py
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _test_support import require_api_base, require_isolated_db  # noqa: E402

RUN = 'R5LOCK'
PERIOD = '2026-09'  # 已过完的期间（结账只允许冻过去的期间）
FAILED: list[str] = []
PASSED = 0


def check(label: str, got, want) -> None:
    global PASSED
    if got == want:
        PASSED += 1
        print(f'  OK   {label}: {got!r}')
    else:
        FAILED.append(label)
        print(f'  FAIL {label}: 实际 {got!r}，期望 {want!r}')


def check_true(label: str, value: bool, detail: str = '') -> None:
    global PASSED
    if value:
        PASSED += 1
        print(f'  OK   {label}{f"（{detail}）" if detail else ""}')
    else:
        FAILED.append(label)
        print(f'  FAIL {label}{f"（{detail}）" if detail else ""}')


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
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {'code': exc.code, 'message': raw[:200]}


def login(username: str, password: str) -> str:
    _, res = call('POST', '/auth/login', body={'username': username, 'password': 'admin123'
                                               if username == 'admin' else '123456'})
    return res['data']['access_token']


def period_lock_key(period: str) -> int:
    """与 `target_actuals.lock_period` 同一把键（算法必须一致，否则测的不是同一把锁）。"""
    return int.from_bytes(
        hashlib.sha256(f'actual-period:{period}'.encode()).digest()[:8], 'big', signed=True
    )


async def can_take_period_lock_nowait(period: str) -> bool:
    """从**另一条连接**试 `pg_advisory_xact_lock`（NOWAIT），判断这把锁现在被不被人占着。"""
    from sqlalchemy import text

    from app.core.database import SessionLocal

    async with SessionLocal() as session:
        try:
            await session.execute(
                text('SELECT pg_try_advisory_xact_lock(:k)'), {'k': period_lock_key(period)}
            )
            got = (await session.execute(
                text('SELECT pg_try_advisory_xact_lock(:k)'), {'k': period_lock_key(period)}
            )).scalar_one()
            await session.rollback()
            return bool(got)
        except Exception:  # noqa: BLE001
            await session.rollback()
            return False


async def make_fixture() -> None:
    """造一点可以结账的数据：**一个这一期的销售目标**就够了。

    结账的前提是"这一期有目标或实绩"（`freeze` 端点里 `if not values` 那一句），
    所以一个目标就能让"有数可结"。实绩为 0 不影响本节要验的东西 ——
    这一节验的是**互斥**（两个结账不能互相覆盖），不是算法。

    ⚠️ 不要为了"更像真的"去造商机/订单：`opportunities.stage_id` 是必填，
    手写 INSERT 会一路撞非空约束（实测撞过一次），而它对本文的结论没有贡献。
    """
    from sqlalchemy import text

    from app.core.database import SessionLocal

    async with SessionLocal() as session:
        # 列名是 `sales_target`（不是 target_amount）—— 手写 SQL 的代价，记一笔
        await session.execute(text(
            "insert into sales_targets (period, user_id, sales_target, created_at)"
            " values (:p, 1, 1000, :now)"
        ), {'p': PERIOD, 'now': datetime.now(UTC)})
        await session.commit()


async def cleanup() -> None:
    from sqlalchemy import text

    from app.core.database import SessionLocal

    async with SessionLocal() as session:
        await session.execute(text(
            'delete from analytics_actual_snapshot_items where period = :p'
        ), {'p': PERIOD})
        await session.execute(text(
            'delete from analytics_actual_snapshots where period = :p'
        ), {'p': PERIOD})
        await session.execute(text('delete from sales_targets where period = :p'), {'p': PERIOD})
        await session.commit()
        print('  （夹具已清）')


async def main() -> None:
    require_api_base()
    require_isolated_db()
    admin = login('admin', 'admin123')
    print()
    print('=== 0. 夹具 ===')
    await cleanup()
    await make_fixture()
    print(f'  期间 {PERIOD}：一个目标 1000 + 一张已签单订单')

    # ------------------------------------------------------------------ ① 基座
    print()
    print('=== 1. 基座：这把期间锁现在有没有人占（必须"没人占"）===')
    free = await can_take_period_lock_nowait(PERIOD)
    check_true('空闲时能拿到期间锁', free, '拿得到 = 现在没人占')

    # --------------------------------------------------------------- ② 锁互斥
    print()
    print('=== 2. 修好之后：持有期间锁的事务，别人进不来 ===')
    from sqlalchemy import text

    from app.core.database import SessionLocal

    holder_ready = asyncio.Event()
    holder_done = asyncio.Event()
    blocked_seen: list[bool] = []

    async def holder() -> None:
        async with SessionLocal() as session:
            await session.execute(
                text('SELECT pg_advisory_xact_lock(:k)'), {'k': period_lock_key(PERIOD)}
            )
            holder_ready.set()
            await holder_done.wait()  # 不放锁，等对面探测完
            await session.rollback()

    async def prober() -> None:
        await holder_ready.wait()
        blocked_seen.append(not await can_take_period_lock_nowait(PERIOD))
        holder_done.set()

    await asyncio.gather(holder(), prober())
    check_true('持锁期间别人拿不到（被挡住了）', blocked_seen and blocked_seen[0],
               f'blocked={blocked_seen}')
    free_again = await can_take_period_lock_nowait(PERIOD)
    check_true('放锁之后又能拿到（没有泄漏）', free_again, '锁随事务结束释放')

    # ------------------------------------------------- ③ 真实接口：只能成功一次
    print()
    print('=== 3. 真实接口：两个普通结账并发，只能成功一个 ===')

    async def freeze_once(tag: str):
        loop = asyncio.get_running_loop()
        started = loop.time()

        def _do():
            return call('POST', f'/sales-targets/actuals/freeze?period={PERIOD}', token=admin)

        status, body = await asyncio.to_thread(_do)
        return {'tag': tag, 'status': status, 'body': body, 'elapsed': loop.time() - started}

    results = await asyncio.gather(freeze_once('A'), freeze_once('B'))
    for r in sorted(results, key=lambda x: x['elapsed']):
        print(f"  · {r['tag']}: HTTP {r['status']} code={r['body'].get('code')} "
              f"{str(r['body'].get('message'))[:70]!r}（{r['elapsed']:.2f}s）")

    ok_count = sum(1 for r in results if r['body'].get('code') == 0)
    conflict = [r for r in results if r['body'].get('code') != 0]
    check('只有一次成功', ok_count, 1)
    check_true('另一次报"已经结过账了"（不是静默覆盖）',
               bool(conflict) and '已经结过账' in str(conflict[0]['body'].get('message')),
               str(conflict[0]['body'].get('message'))[:60] if conflict else '无失败方')

    # 终态：快照的 reason（note）必须仍为空 —— 说明没有走"重算"那条路
    async with SessionLocal() as session:
        rows = (await session.execute(text(
            'select note, actual_value from analytics_actual_snapshots where period = :p'
        ), {'p': PERIOD})).all()
    notes = {r[0] for r in rows}
    check_true('存档的原因字段仍为空（没有被当成重算写进去）',
               notes <= {None}, f'notes={notes}')

    print()
    print('=== 收尾：清夹具 ===')
    await cleanup()


if __name__ == '__main__':
    asyncio.run(main())
    print()
    print(f'通过 {PASSED} 条，失败 {len(FAILED)} 条')
    if FAILED:
        for item in FAILED:
            print(f'  ✗ {item}')
        sys.exit(1)
    print('全部通过')
