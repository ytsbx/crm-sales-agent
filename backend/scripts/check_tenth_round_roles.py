"""第十批组一：角色停用真正生效（10.10）+ 最后一位有效管理员的并发保护（10.11）。

跑法（后端要在隔离端口跑着；会写用户/角色，所以**必须显式给 API_BASE**）：

    cd backend
    API_BASE="http://127.0.0.1:8001/api/v1" DATABASE_URL="$(cat /tmp/crm_test_url.txt)" \\
      PYTHONPATH=. .venv/bin/python scripts/check_tenth_round_roles.py

## 守的是什么

**10.10 角色停用后权限仍然生效。**
停用一个角色，等于把「这批人不再拥有这套权限」这个决定写进库里。此前
`get_user_roles` / `get_user_permission_codes` 都不看 `roles.status`，
`resolve_data_scope` 也不知道状态 —— 于是停用只是个装饰：权限接口照旧返回、
客户列表照样进得去、配了「全部」范围的角色停了也仍然全量可见。
这里验的是**同一个登录凭据的下一次请求就体现变化**（本项目每次请求都重新查权限，
所以不需要重新登录——这本身就是要守住的性质）。

**10.11 最后一个管理员的保护存在并发漏洞。**
旧实现是「先数一遍还有几个在岗管理员，数到 0 就拒绝」，**数完到改完之间没有锁**。
互相停用是两个**不同用户**，各锁各的行谁也不挡谁 —— 都数到「对方还在」，
一起放行，最后 0 个管理员，系统彻底锁死且没有找回入口。
这里验三件事：并发互相停用/互相摘角色/混合，都至少保留一位；
停用管理员角色会被拒且**完整回滚**；以及守卫**真的在锁上**（持锁量耗时）。

## 为什么用「持锁量耗时」而不是「并发撞一下」

并发缺陷靠真撞是概率事件：撞不上时套件照样绿，你会以为修好了。
但「耗时」这类断言最容易假绿（数据库自带的行锁就会让请求变慢）——
**这条是个例外**：这里量的是 `pg_advisory_xact_lock`，
它是**应用代码自己**去取的锁，旧代码根本没有这一句，
所以「等了两秒」只可能来自新加的守卫。反向验证时应当转绿。

夹具：自建三个账号（A/B/D）与两个临时角色，跑完自己清干净（含审计行）。
会把**内置 admin 账号临时停用**（否则造不出「仅 A、B 两位管理员」的局面），
所以 finally 里用直连 SQL 硬恢复 —— SQL 里没有业务守卫，不会被自己拦下。
"""

import asyncio
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings

if not os.environ.get('API_BASE'):
    # 默认的 8000 是开发后端：本套件会建/停用户与角色，打到开发库等于拿真实数据做实验。
    raise SystemExit(
        '必须显式设置 API_BASE（默认的 http://127.0.0.1:8000 是开发后端）。\n'
        '例：API_BASE="http://127.0.0.1:8001/api/v1" PYTHONPATH=. .venv/bin/python '
        'scripts/check_tenth_round_roles.py'
    )

BASE = os.environ['API_BASE']
RUN = str(int(time.time()))[-6:]
FAILURES: list[str] = []

#: 与 `user/service._ADMIN_GUARD_LOCK_KEY` 必须一致：这边靠它把后端卡住，
#: 用来证明"请求真的去取那把锁了"。
ADMIN_GUARD_LOCK_KEY = 7301001


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
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode() or '{}')
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode() or '{}'
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {'_raw': raw}


def login(username: str, password: str) -> str:
    status, res = call('POST', '/auth/login', body={'username': username, 'password': password})
    if res.get('code') != 0:
        raise SystemExit(f'登录失败（{username}）：{res.get("message")!r}；后端在 {BASE} 跑着吗？')
    return res['data']['access_token']


# ---------------------------------------------------------------- 直连库（夹具与恢复）
#
# ⚠️ **不要复用 `app.core.database.SessionLocal`**：本脚本用多次 `asyncio.run()`
# 把同步的 HTTP 调用和异步的库操作串起来，每次 `asyncio.run()` 都是一个新事件循环；
# 而全局 engine 的连接池里那些连接是绑在**第一个**循环上的，第二次用就是
# `got Future attached to a different loop`（而且报错栈会指向无关的 asyncpg 内部，
# 看起来像代码坏了）。所以每次自己建一个 NullPool 引擎、用完就 dispose。

async def _with_conn(fn):
    eng = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with eng.connect() as conn:
            return await fn(conn)
    finally:
        await eng.dispose()


async def _scalar(sql: str, params: dict | None = None):
    async def run(conn):
        return (await conn.execute(text(sql), params or {})).scalar()

    return await _with_conn(run)


async def _exec(*statements: tuple[str, dict]) -> None:
    eng = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with eng.begin() as conn:
            for sql, params in statements:
                await conn.execute(text(sql), params)
    finally:
        await eng.dispose()


COUNT_ADMINS_SQL = """
select count(distinct u.id) from users u
  join user_roles ur on ur.user_id = u.id
  join roles r on r.id = ur.role_id
 where r.code = 'admin' and r.status = 'active' and u.status = 'active'
"""


async def active_admin_count() -> int:
    return int(await _scalar(COUNT_ADMINS_SQL) or 0)


# ---------------------------------------------------------------- 并发与持锁

def fire_together(*calls):
    """两个请求尽量同时发出（Barrier 对齐），返回各自的结果。"""
    barrier = threading.Barrier(len(calls))
    results: dict[int, tuple] = {}

    def run(idx, fn):
        barrier.wait()
        try:
            results[idx] = fn()
        except Exception as exc:  # 子线程的异常必须自己接住，否则 join 后一片沉默
            results[idx] = ('ERR', repr(exc))

    threads = [threading.Thread(target=run, args=(i, fn)) for i, fn in enumerate(calls)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    return [results.get(i) for i in range(len(calls))]


def _hold_advisory_lock(seconds: float, ready: threading.Event) -> None:
    """在**独立线程 + 独立事件循环**里持有守卫锁，把后端的请求卡在外面。

    ⚠️ 不能复用应用那个全局 engine：它在主事件循环里创建，拿到子线程的
    `asyncio.run()` 里用会挂在「连接绑定到另一个 loop」，而异常在子线程里抛出、
    完全静默 —— 实测量出 0.02 秒，会得出与事实相反的结论。
    """
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    dsn = settings.database_url

    async def hold():
        eng = create_async_engine(dsn, poolclass=NullPool)
        try:
            async with eng.connect() as conn:
                await conn.execute(text(f'select pg_advisory_lock({ADMIN_GUARD_LOCK_KEY})'))
                ready.set()
                await asyncio.sleep(seconds)
                await conn.execute(text(f'select pg_advisory_unlock({ADMIN_GUARD_LOCK_KEY})'))
        finally:
            await eng.dispose()

    try:
        asyncio.run(hold())
    except Exception as exc:
        print(f'    （持锁线程异常：{exc!r}）')


# ---------------------------------------------------------------- 夹具

async def purge():
    """按前缀清掉本套件造的用户与角色，并把内置 admin / 守卫涉及的人恢复成 active。

    ⚠️ 必须**用 SQL 直改**：走业务接口会被刚立的守卫拦下（那正是被测的东西），
    恢复动作被自己的守卫挡住就再也恢复不了了。
    """
    prefix = f'chkrol{RUN}%'
    await _exec(
        ('delete from user_roles where user_id in (select id from users where username like :p)',
         {'p': prefix}),
        ('delete from audit_logs where business_type = \'user\' and business_id in '
         '(select id from users where username like :p)', {'p': prefix}),
        ('delete from audit_logs where business_type = \'role\' and business_id in '
         '(select id from roles where code like :p)', {'p': prefix}),
        ('delete from role_permissions where role_id in (select id from roles where code like :p)',
         {'p': prefix}),
        ('delete from roles where code like :p', {'p': prefix}),
        ('delete from users where username like :p', {'p': prefix}),
        # 内置 admin 账号：本套件为了造「仅两位管理员」把它临时停用了，这里硬恢复，
        # 并确保它的 admin 角色关联还在（停用账号不改关联，这是防御性补一条）。
        ("update users set status = 'active' where username = :u", {'u': ADMIN_USERNAME}),
        # 内置 admin **角色**的启用状态也要复位：反向验证时第 4 节会把这条守卫拔掉，
        # 于是「停用管理员角色」真能成功——角色被留在停用状态，后面的套件会以
        # 「admin 登进去啥也干不了」的形式一片红。
        ("update roles set status = 'active' where code = 'admin'", {}),
        ("insert into user_roles (user_id, role_id) "
         "select u.id, r.id from users u cross join roles r "
         "where u.username = :u and r.code = 'admin' "
         "and not exists (select 1 from user_roles ur where ur.user_id = u.id and ur.role_id = r.id)",
         {'u': ADMIN_USERNAME}),
    )


async def make_user(admin_token: str, suffix: str, role_ids: list[int]) -> tuple[int, str]:
    """建一个账号并返回 (id, 登录凭据)。"""
    username = f'chkrol{RUN}{suffix}'
    status, res = call('POST', '/users', token=admin_token, body={
        'name': f'CHK{RUN}账号{suffix}', 'username': username, 'password': 'chk-123456',
        'role_ids': role_ids,
    })
    if res.get('code') != 0:
        raise SystemExit(f'建账号 {username} 失败：{res}')
    return res['data']['id'], login(username, 'chk-123456')


async def make_role(admin_token: str, suffix: str, *, scope: str, perms: list[str]) -> int:
    status, res = call('POST', '/roles', token=admin_token, body={
        'code': f'chkrol{RUN}{suffix}', 'name': f'CHK{RUN}角色{suffix}',
        'data_scope': scope, 'permission_codes': perms,
    })
    if res.get('code') != 0:
        raise SystemExit(f'建角色 {suffix} 失败：{res}')
    return res['data']['id']


#: 内置管理员账号的登录名与口令（seed 里就叫 admin / admin123）。
ADMIN_USERNAME = 'admin'
ADMIN_PASSWORD = 'admin123'


# ---------------------------------------------------------------- 主流程

def main():
    admin = login(ADMIN_USERNAME, ADMIN_PASSWORD)
    status, res = call('GET', '/roles', token=admin)
    admin_role_id = next(r['id'] for r in res['data'] if r['code'] == 'admin')
    status, res = call('GET', f'/users?keyword={ADMIN_USERNAME}', token=admin)
    admin_user_id = next(u['id'] for u in res['data']['items'] if u['username'] == ADMIN_USERNAME)
    print(f'admin 角色 id={admin_role_id}，admin 账号 id={admin_user_id}')

    others = asyncio.run(active_admin_count())
    if others > 1:
        raise SystemExit(
            f'库里已有 {others} 位有效管理员。本套件要造「仅两位管理员」的局面，'
            '多人时会测不出并发互停的后果。请先清掉多余的管理员再跑。'
        )

    # ---------------- 1. 10.10 角色停用后权限真的失效 ----------------
    print()
    print('=== 1. 10.10 停用角色后，同一凭据的下一次请求就体现变化 ===')
    r_all = asyncio.run(make_role(admin, 'r1', scope='all', perms=['customer:view', 'lead:view']))
    r_self = asyncio.run(make_role(admin, 'r2', scope='self', perms=['lead:view']))
    uid_d, token_d = asyncio.run(make_user(admin, 'd', [r_all, r_self]))

    status, res = call('GET', '/customers', token=token_d)
    check('停用前：能看客户列表', res.get('code'), 0)
    status, res = call('GET', f'/users/{uid_d}/data-scope', token=admin)
    check('停用前：数据范围取最大（all）', res['data'].get('data_scope'), 'all')

    status, res = call('PATCH', f'/roles/{r_all}', token=admin, body={'status': 'disabled'})
    check('停用它「全部」范围的角色', res.get('code'), 0)

    status, res = call('GET', '/customers', token=token_d)
    check_true('停用后：同一凭据立刻失去 customer:view（403）',
               res.get('code') in (40301, 403), f'code={res.get("code")}')
    status, res = call('GET', '/leads', token=token_d)
    check('停用后：剩余有效角色的权限继续生效（还能看线索）', res.get('code'), 0)
    status, res = call('GET', f'/users/{uid_d}/data-scope', token=admin)
    check('停用后：数据范围退回剩余有效角色（self）', res['data'].get('data_scope'), 'self')

    status, res = call('PATCH', f'/roles/{r_all}', token=admin, body={'status': 'active'})
    check('重新启用该角色', res.get('code'), 0)
    status, res = call('GET', '/customers', token=token_d)
    check('恢复后：旧凭据又能看客户', res.get('code'), 0)
    status, res = call('GET', f'/users/{uid_d}/data-scope', token=admin)
    check('恢复后：数据范围回到 all', res['data'].get('data_scope'), 'all')

    # ---------------- 2. 10.10 没有任何有效角色 = 没有任何权限 ----------------
    print()
    print('=== 2. 10.10 唯一的角色被停用后，账号不再拥有任何权限 ===')
    uid_e, token_e = asyncio.run(make_user(admin, 'e', [r_all]))
    status, res = call('GET', '/customers', token=token_e)
    check('停用前：能看客户', res.get('code'), 0)
    status, res = call('GET', '/leads', token=token_e)
    check('停用前：能看线索', res.get('code'), 0)
    call('PATCH', f'/roles/{r_all}', token=admin, body={'status': 'disabled'})
    status, res = call('GET', '/customers', token=token_e)
    check_true('停用唯一角色后：无任何角色 -> 无权限（403）',
               res.get('code') in (40301, 403), f'code={res.get("code")}')
    # ⚠️ 这条必须选一个"停用前**确实**有权限"的动作，否则它在新旧代码下都会绿
    # （账号本来就没这个权限时，403 与角色状态无关——假绿）。
    status, res = call('GET', '/leads', token=token_e)
    check_true('停用唯一角色后：线索也不可看（403）',
               res.get('code') in (40301, 403), f'code={res.get("code")}')
    call('PATCH', f'/roles/{r_all}', token=admin, body={'status': 'active'})

    # ---------------- 3. 10.10 管理页仍能看到停用角色的关联 ----------------
    print()
    print('=== 3. 10.10 管理页保留关联并显示停用状态 ===')
    call('PATCH', f'/roles/{r_all}', token=admin, body={'status': 'disabled'})
    status, res = call('GET', f'/users/{uid_d}/roles', token=admin)
    rows = {r['code']: r for r in res['data']}
    check_true('停用角色仍挂在用户身上（关联没被删）',
               f'chkrol{RUN}r1' in rows, str(list(rows)))
    check('管理视图带出该角色的 status=disabled',
          rows.get(f'chkrol{RUN}r1', {}).get('status'), 'disabled')
    status, res = call('GET', '/roles', token=admin)
    listed = {r['code']: r for r in res['data']}
    check('角色列表带出停用状态', listed.get(f'chkrol{RUN}r1', {}).get('status'), 'disabled')
    call('PATCH', f'/roles/{r_all}', token=admin, body={'status': 'active'})

    # ---------------- 4. 10.11 停用管理员角色被拒且完整回滚 ----------------
    print()
    print('=== 4. 10.11 停用管理员角色会清空有效管理员 -> 拒绝并完整回滚 ===')
    status, res = call('PATCH', f'/roles/{admin_role_id}', token=admin, body={'status': 'disabled'})
    check_true('停用管理员角色被拒（4xx）', status in (400, 403, 422), f'HTTP {status}')
    check_true('提示说清是"会没有有效管理员"',
               '管理员' in (res.get('message') or ''), res.get('message') or '')
    status, res = call('GET', '/roles', token=admin)
    still = next(r['status'] for r in res['data'] if r['id'] == admin_role_id)
    check('被拒之后角色状态一个字段都没改（完整回滚）', still, 'active')

    # ---------------- 5. 10.11 并发互停 / 互摘角色 / 混合 ----------------
    print()
    print('=== 5. 10.11 并发减少有效管理员：每轮都至少剩一位 ===')
    uid_a, token_a = asyncio.run(make_user(admin, 'a', [admin_role_id]))
    uid_b, token_b = asyncio.run(make_user(admin, 'b', [admin_role_id]))

    # 只留 A、B 两位有效管理员：用 A 的凭据停掉内置 admin（此时还有 3 位，允许）。
    status, res = call('POST', f'/users/{admin_user_id}/disable', token=token_a)
    check('停用内置 admin 账号（腾出"仅两位"的局面）', res.get('code'), 0)
    left = asyncio.run(active_admin_count())
    check('此刻有效管理员恰好是 A、B 两位', left, 2)

    async def reset_pair():
        await _exec(
            ("update users set status = 'active' where id in (:a, :b)", {'a': uid_a, 'b': uid_b}),
            ('delete from user_roles where user_id in (:a, :b)', {'a': uid_a, 'b': uid_b}),
            ('insert into user_roles (user_id, role_id) values (:a, :r), (:b, :r)',
             {'a': uid_a, 'b': uid_b, 'r': admin_role_id}),
        )

    # 5.1 / 5.2 / 5.3 三种并发形态
    #
    # ⚠️ 并发缺陷本身是**概率性**的：两个请求恰好串行了，旧代码也能"看起来没问题"
    # （反向验证实测：互相停用/互相摘角色那两轮恰好串行通过，只有"停用+摘角色"
    # 那轮真的把管理员打到 0 位）。所以每个形态跑 **3 轮**，任一轮出事就记红。
    # 真正**确定性**的判据是下面第 6 节的"持锁量耗时"。
    async def probe(build_calls, label: str) -> None:
        both_succeeded, min_left = 0, 99
        for _ in range(3):
            await reset_pair()
            got = fire_together(*build_calls())
            oks = sum(1 for r in got if r and r[1].get('code') == 0)
            if oks > 1:
                both_succeeded += 1
            min_left = min(min_left, await active_admin_count())
        check_true(f'{label}：没有任何一轮是两个都成功',
                   both_succeeded == 0, f'{both_succeeded}/3 轮两个都成功')
        check_true(f'{label}：每一轮都至少剩一位有效管理员',
                   min_left >= 1, f'最少剩 {min_left} 位')

    asyncio.run(probe(
        lambda: (
            (lambda: call('POST', f'/users/{uid_b}/disable', token=token_a)),
            (lambda: call('POST', f'/users/{uid_a}/disable', token=token_b)),
        ),
        '互相停用',
    ))
    asyncio.run(probe(
        lambda: (
            (lambda: call('PUT', f'/users/{uid_b}/roles', token=token_a, body={'role_ids': []})),
            (lambda: call('PUT', f'/users/{uid_a}/roles', token=token_b, body={'role_ids': []})),
        ),
        '互相摘角色',
    ))
    asyncio.run(probe(
        lambda: (
            (lambda: call('POST', f'/users/{uid_b}/disable', token=token_a)),
            (lambda: call('PUT', f'/users/{uid_a}/roles', token=token_b, body={'role_ids': []})),
        ),
        '一边停用一边摘角色',
    ))

    # 5.4 有多位管理员时，正常调整仍能完成（守卫不能把正常操作也挡掉）
    asyncio.run(reset_pair())
    status, res = call('POST', f'/users/{uid_b}/disable', token=token_a)
    check('两人在手时，停用其中一个是允许的', res.get('code'), 0)
    status, res = call('POST', f'/users/{uid_b}/enable', token=token_a)
    check('再启用回来', res.get('code'), 0)

    # ---------------- 6. 10.11 守卫真的在锁上（持锁量耗时） ----------------
    print()
    print('=== 6. 10.11 守卫真的取了一把全局锁（持锁量耗时）===')
    asyncio.run(reset_pair())
    ready = threading.Event()
    holder = threading.Thread(target=_hold_advisory_lock, args=(2.5, ready))
    holder.start()
    ready.wait(10)
    started = time.monotonic()
    status, res = call('POST', f'/users/{uid_b}/disable', token=token_a)
    elapsed = time.monotonic() - started
    holder.join(15)
    check_true('请求被守卫锁挡住（等锁 ≈ 持锁时长，不是"秒回"）',
               elapsed >= 1.0, f'耗时 {elapsed:.2f}s')
    check('等锁期间 A 仍是有效的操作者（请求最终成功）', res.get('code'), 0)

    print()
    print('=== 7. 收尾：有效管理员仍然 >= 1 ===')
    check_true('跑完仍至少有一位有效管理员', asyncio.run(active_admin_count()) >= 1,
               f'剩 {asyncio.run(active_admin_count())} 位')


async def _cleanup():
    await purge()


if __name__ == '__main__':
    try:
        main()
    finally:
        # 恢复动作必须无条件执行：本套件会临时停用内置 admin，
        # 万一中途抛异常没恢复，后面所有套件都会以「admin 登不进去」的形式变红。
        asyncio.run(_cleanup())
        print()
        if FAILURES:
            print(f'FAILED（{len(FAILURES)} 项）：' + '；'.join(FAILURES))
            sys.exit(1)
        print('全部通过 ✓')
