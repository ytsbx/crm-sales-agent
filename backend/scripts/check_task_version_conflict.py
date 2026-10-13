#!/usr/bin/env python3
"""任务「客户端版本」校验的真接口回归（第五批复审返修，2026-10-12）。

## 复审报的那条

`PATCH /tasks/{id}` 里用 `replace(microsecond=0)` 截掉微秒再比版本，于是
**同一秒内的改动全部被判成"没变"**：

    库里版本   10:00:00.200000
    旧页面版本 10:00:00.100000
    → 保存成功，覆盖了同事刚改的标题

我复现时更宽：拿一个**明显过期但同秒**的版本（差 800 微秒）也能改成功。

## 为什么必须真接口

离线测试只能断言"源码里没有 `replace(microsecond=0)`"、
"比的是完整时间戳"—— 那些是**写法**，不是**行为**。
"给旧版本必须 409、给当前版本必须成功"这件事只有真接口能证。
（复审也点明了：现有任务测试只检查源码包含字段和错误码，抓不到这个反例。）

## 四个用例

1. **精度链路**：接口返回的 `updated_at` 必须带微秒 —— 否则"比完整时间戳"
   会把正常路径也弄坏（这是我当初截秒的"理由"，得先证伪它）；
2. **完全相同版本** → 保存成功（正常路径不许误伤）；
3. **过期版本（跨秒）** → 409，且**数据未变**；
4. **过期版本（同一秒内）** → 409，且**数据未变**（复审报的那条）。

跑法（一次性库 + 隔离后端）：

    DB=crm_check_task_ver
    psql -h 127.0.0.1 -U postgres -d postgres -c "CREATE DATABASE $DB OWNER crm;"
    cd backend && export DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5432/$DB
    PYTHONPATH=. .venv/bin/python -m alembic upgrade head
    PYTHONPATH=. .venv/bin/python -m scripts.seed
    PYTHONPATH=. .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8083 &
    API_BASE=http://127.0.0.1:8083/api/v1 PYTHONPATH=. DATABASE_URL=$DATABASE_URL \\
      .venv/bin/python scripts/check_task_version_conflict.py
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _test_support import require_api_base, require_isolated_db  # noqa: E402

RUN = 'TASKVER'
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
    _, res = call('POST', '/auth/login', body={'username': username, 'password': password})
    return res['data']['access_token']


def shift_micros(ts: str, delta: int) -> str:
    """把 ISO 时间戳的微秒挪 delta（用来构造"同一秒内的旧版本"）。

    只动微秒部分，**秒以上一律不动** —— 这正是复审那个反例的形状：
    两个版本落在同一秒，只差微秒。
    """
    m = re.match(r'^(.*\.\d{6})(.*)$', ts)
    if not m:
        raise AssertionError(f'时间戳不带微秒，没法构造同秒旧版本：{ts}')
    head, tail = m.groups()
    date_part, micros = head.rsplit('.', 1)
    shifted = int(micros) + delta
    if not 0 <= shifted < 1_000_000:
        raise AssertionError(f'挪 {delta} 微秒会跨秒：{ts}')
    return f'{date_part}.{shifted:06d}{tail}'


async def _set_db_version(task_id: int, delta_micros: int) -> str:
    """直接改库把 `updated_at` 挪一点（模拟"库里已经是更新的一版"）。"""
    from sqlalchemy import text

    from app.core.database import SessionLocal

    async with SessionLocal() as session:
        await session.execute(text(
            f"update tasks set updated_at = updated_at + interval '{delta_micros} microseconds' "
            'where id = :i'
        ), {'i': task_id})
        await session.commit()
        return (
            await session.execute(text('select updated_at from tasks where id = :i'), {'i': task_id})
        ).scalar_one().isoformat()


async def _cleanup() -> None:
    from sqlalchemy import text

    from app.core.database import SessionLocal

    async with SessionLocal() as session:
        await session.execute(text(
            "delete from tasks where title like :p or customer_id in "
            '(select id from customers where name like :p)'
        ), {'p': f'{RUN}%'})
        await session.execute(text('delete from customers where name like :p'), {'p': f'{RUN}%'})
        await session.commit()
        print('  （夹具已清）')


async def main() -> None:
    require_api_base()
    require_isolated_db()
    admin = login('admin', 'admin123')

    print()
    print('=== 0. 夹具 ===')
    await _cleanup()
    _, res = call('POST', '/customers', token=admin, body={'name': f'{RUN}客户'})
    customer = res['data']['id']
    _, res = call('POST', '/tasks', token=admin, body={
        'title': f'{RUN}原始标题', 'task_type': '跟进', 'customer_id': customer,
        'owner_id': 1, 'due_at': '2026-11-01T10:00:00+08:00',
    })
    check('建任务', res.get('code'), 0)
    task = res['data']
    task_id = task['id']
    v1 = task['updated_at']

    # ---------------------------------------------------------------- ① 精度
    print()
    print('=== 1. 精度链路：接口给的版本必须带微秒 ===')
    print(f'  · 建任务返回的 updated_at = {v1}')
    check_true('带微秒（否则"比完整时间戳"会误伤正常路径）',
               bool(re.match(r'^.*\.\d{6}', v1)), v1)

    # ------------------------------------------------------- ② 相同版本放行
    print()
    print('=== 2. 完全相同的版本 → 保存成功（不许误伤正常路径）===')
    status, res = call('PATCH', f'/tasks/{task_id}', token=admin,
                       body={'title': f'{RUN}正常改成A', 'expected_updated_at': v1})
    check('保存成功', res.get('code'), 0)
    v2 = res['data']['updated_at']
    print(f'  · 新版本 = {v2}')

    # ------------------------------------------------- ③ 过期版本（跨秒也拦）
    print()
    print('=== 3. 过期版本（v1）→ 409 且数据未变 ===')
    status, res = call('PATCH', f'/tasks/{task_id}', token=admin,
                       body={'title': f'{RUN}用旧版本覆盖', 'expected_updated_at': v1})
    check('返回 409（版本冲突）', res.get('code'), 40902)
    _, cur = call('GET', f'/tasks/{task_id}', token=admin)
    check('标题**没有被覆盖**', cur['data']['title'], f'{RUN}正常改成A')

    # --------------------------------------- ④ 同一秒内的旧版本（返修的那条）
    print()
    print('=== 4. ★同一秒内的旧版本 → 必须 409（复审报的反例）===')
    # 把库里版本往前挪 800 微秒：仍然落在同一秒
    moved = await _set_db_version(task_id, 800)
    print(f'  · 库里版本挪到 {moved}')
    _, cur = call('GET', f'/tasks/{task_id}', token=admin)
    now_version = cur['data']['updated_at']
    # ⚠️ **位移要自适应，不能写死 -800 微秒**（2026-10-13 修）。
    #
    # 写死 -800 时，只要当前时间戳的微秒部分 < 800（也就是"刚刚跨秒"那一下），
    # 挪完就掉到上一秒 —— 于是"构造成立"这条断言在**秒边界上随机翻车**。
    # 实测：`…25.339505` → 挪 -800 → `…24.339505`，跨秒，红了。
    # 而它红的时候，真正要验的那条（同秒旧版本必须 409）**是过的** ——
    # 一条 flaky 的构造断言，把一条正常的用例拖成了红。
    #
    # 现在：按当前微秒**取能同秒的最大位移**（至少 1 微秒，最多 999999），
    # 既保证落在同一秒，又保证确实"更旧"。
    micros = int(re.match(r'^.*\.(\d{6})', now_version).group(1))
    delta = -min(800, micros) if micros else 0
    if delta == 0:
        # 微秒正好是 0：此刻当秒刚开始，往前挪必然跨秒。
        # 不去碰时间戳（碰了就跨秒、构造不成立），改为**重新取一次**当前版本，
        # 等它落到秒中段再构造。
        for _ in range(20):
            await asyncio.sleep(0.05)
            _, cur = call('GET', f'/tasks/{task_id}', token=admin)
            now_version = cur['data']['updated_at']
            micros = int(re.match(r'^.*\.(\d{6})', now_version).group(1))
            if micros:
                delta = -min(800, micros)
                break
    stale_same_second = shift_micros(now_version, delta)
    print(f'  · 当前版本 = {now_version}')
    print(f'  · 同秒旧版本 = {stale_same_second}（只差 {abs(delta)} 微秒）')
    check_true('构造成立：同秒、且确实更旧',
               now_version.split('.')[1][:2] == stale_same_second.split('.')[1][:2]
               and stale_same_second < now_version,
               f'{now_version} vs {stale_same_second}')

    status, res = call('PATCH', f'/tasks/{task_id}', token=admin,
                       body={'title': f'{RUN}用同秒旧版本覆盖',
                             'expected_updated_at': stale_same_second})
    check('★同秒旧版本被拒（409）', res.get('code'), 40902)
    _, cur = call('GET', f'/tasks/{task_id}', token=admin)
    check('标题**没有被覆盖**', cur['data']['title'], f'{RUN}正常改成A')

    # ⑤ 再确认"当前版本仍然能改"（没有把功能锁死）
    print()
    print('=== 5. 用**当前**版本改 → 仍然成功（没把功能锁死）===')
    status, res = call('PATCH', f'/tasks/{task_id}', token=admin,
                       body={'title': f'{RUN}正常改成B', 'expected_updated_at': now_version})
    check('保存成功', res.get('code'), 0)
    _, cur = call('GET', f'/tasks/{task_id}', token=admin)
    check('标题已更新', cur['data']['title'], f'{RUN}正常改成B')

    print()
    print('=== 收尾 ===')
    await _cleanup()


if __name__ == '__main__':
    asyncio.run(main())
    print()
    print(f'通过 {PASSED} 条，失败 {len(FAILED)} 条')
    if FAILED:
        for item in FAILED:
            print(f'  ✗ {item}')
        sys.exit(1)
    print('全部通过')
