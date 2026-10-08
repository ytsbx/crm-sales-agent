"""第十批组一之二：登录会话失效机制（10.12）。

跑法（后端要在隔离端口跑着；会建账号、改密码，所以**必须显式给 API_BASE**）：

    cd backend
    API_BASE="http://127.0.0.1:8001/api/v1" DATABASE_URL="$(cat /tmp/crm_test_url.txt)" \\
      PYTHONPATH=. .venv/bin/python scripts/check_tenth_round_sessions.py

## 守的是什么

**登出与改密码此前只影响前端，服务端收不回已经发出去的凭据。**
JWT 无状态：签名对、没过期就一路放行。于是

- 「登出」其实只是前端把 token 丢掉，服务端只留一条审计；
- 「改 / 重置密码」只换 `password_hash`，**发出去的令牌一张都没作废**。

泄漏出去的凭据在过期前（默认 12 小时）一直是有效通行证 ——
而这恰恰是「账号疑似泄漏、赶紧改密码」最需要它立刻生效的时刻。

现在每次登录在 `login_sessions` 里留一行、令牌带 `sid`，鉴权与续期走
**同一个**判断（`load_active_session`）。本套件逐条验：

1. 登录建会话；两次登录是两个**不同**的会话；
2. **登出只作废本次会话** —— 手机退了，电脑上正在用的不能被踢掉；
3. **续期属于同一会话**（`sid` 不变、不新建会话），且新令牌可用；
4. **改 / 重置密码 → 该账号全部会话一起失效**，别的账号不受影响；
   旧口令不能再登录、新口令可以；被拒的更新**不**误伤会话；
5. **失效判断只看数据库**：用一条**独立连接**的 SQL 把会话改成 revoked
   （等价于"另一个进程/运维动作"），接口下一次请求就认账 ——
   这是「重启、多进程后仍然有效」在本套件里**可验证**的形态：
   套件不该去管后端进程，但能证明进程内没有缓存、判据就在库里；
6. **没有会话标识的旧令牌一律拒绝**（放行它们等于留一类吊销不了的凭据）。

夹具：自建账号 `chksess{RUN}`，跑完连它、它的会话、相关审计一起清掉
（`login_sessions.user_id` 是 `ondelete=CASCADE`，删用户即带走会话，
这里仍显式查一次"确实没留下"）。
"""

import asyncio
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings

if not os.environ.get('API_BASE'):
    # 默认的 8000 是开发后端：本套件会建账号、改密码，打到开发库等于拿真实数据做实验。
    raise SystemExit(
        '必须显式设置 API_BASE（默认的 http://127.0.0.1:8000 是开发后端）。\n'
        '例：API_BASE="http://127.0.0.1:8001/api/v1" PYTHONPATH=. .venv/bin/python '
        'scripts/check_tenth_round_sessions.py'
    )

BASE = os.environ['API_BASE']
RUN = str(int(time.time()))[-6:]
FAILURES: list[str] = []

ADMIN_USERNAME = 'admin'
ADMIN_PASSWORD = 'admin123'

USERNAME = f'chksess{RUN}'
PASSWORD = 'chk-123456'
NEW_PASSWORD = 'chk-new-123456'


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
        raise SystemExit(
            f'登录失败（{username}）：{res.get("message")!r}；后端在 {BASE} 跑着吗？'
        )
    return res['data']['access_token']


def sid_of(token: str) -> str | None:
    """从令牌里取出会话标识（**只看载荷，不验签**：这里是要读它，不是信它）。"""
    part = token.split('.')[1]
    part += '=' * (-len(part) % 4)
    return json.loads(base64.urlsafe_b64decode(part)).get('sid')


# ---------------------------------------------------------------- 直连库
#
# ⚠️ 不要复用 `app.core.database.SessionLocal`：本脚本用多次 `asyncio.run()`
# 把同步 HTTP 调用与异步库操作串起来，每次 `asyncio.run()` 都是一个新事件循环，
# 而全局 engine 的连接池里那些连接绑在**第一个**循环上 —— 第二次用就是
# `got Future attached to a different loop`。所以每次自建 NullPool 引擎、用完 dispose。


async def _with_conn(fn):
    eng = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with eng.connect() as conn:
            return await fn(conn)
    finally:
        await eng.dispose()


async def _exec(*statements: tuple[str, dict]) -> None:
    eng = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with eng.begin() as conn:
            for sql, params in statements:
                await conn.execute(text(sql), params)
    finally:
        await eng.dispose()


async def session_row(sid: str | None):
    """取会话行（直连库读，不信接口）。"""

    async def run(conn):
        row = (
            await conn.execute(
                text(
                    'select id, user_id, status, revoked_reason, revoked_at, last_refresh_at '
                    'from login_sessions where sid = :s'
                ),
                {'s': sid},
            )
        ).first()
        return tuple(row) if row else None

    return await _with_conn(run)


async def active_session_count(user_id: int) -> int:
    """该账号**还有几个有效会话**（直连库读，不信接口的自述）。"""

    async def run(conn):
        return (
            await conn.execute(
                text(
                    "select count(*) from login_sessions where user_id = :u and status = 'active'"
                ),
                {'u': user_id},
            )
        ).scalar()

    return int(await _with_conn(run))


async def total_session_count(user_id: int) -> int:
    async def run(conn):
        return (
            await conn.execute(
                text('select count(*) from login_sessions where user_id = :u'), {'u': user_id}
            )
        ).scalar()

    return int(await _with_conn(run))


async def user_id_of(username: str) -> int | None:
    async def run(conn):
        return (
            await conn.execute(
                text('select id from users where username = :u'), {'u': username}
            )
        ).scalar()

    return await _with_conn(run)


# ---------------------------------------------------------------- 夹具

async def purge():
    """删掉本套件造的账号，并清掉与它相关的审计行。

    会话行不用单独删：`login_sessions.user_id` 是 `ondelete=CASCADE`，
    删用户时会一起走掉 —— 这里正是要顺带验证这一点。
    内置 admin 账号**只读不改**（本套件不动它的口令与状态），
    所以不需要任何"恢复"动作。
    """
    await _exec(
        ('delete from audit_logs where business_type = \'auth\' and operator_id in '
         '(select id from users where username like :p)', {'p': f'chksess{RUN}%'}),
        ('delete from audit_logs where business_type = \'user\' and business_id in '
         '(select id from users where username like :p)', {'p': f'chksess{RUN}%'}),
        ('delete from audit_logs where operator_id in '
         '(select id from users where username like :p)', {'p': f'chksess{RUN}%'}),
        ('delete from user_roles where user_id in (select id from users where username like :p)',
         {'p': f'chksess{RUN}%'}),
        ('delete from users where username like :p', {'p': f'chksess{RUN}%'}),
    )


def main():
    admin_token = login(ADMIN_USERNAME, ADMIN_PASSWORD)

    # 给夹具账号配一个真实角色：既能顺手证明"有角色的账号登录链路没变"，
    # 也避免造出"零角色"这种真实系统里不存在的形态。
    status, res = call('GET', '/roles', token=admin_token)
    role_ids = [
        r['id'] for r in (res.get('data') or [])
        if r.get('code') == 'salesperson' and r.get('status') in (None, 'active')
    ]
    check_true('找到内置「业务员」角色（夹具要用）', bool(role_ids), str(role_ids[:1]))

    status, res = call('POST', '/users', token=admin_token, body={
        'name': f'CHK{RUN}会话账号', 'username': USERNAME, 'password': PASSWORD,
        'role_ids': role_ids,
    })
    if res.get('code') != 0:
        raise SystemExit(f'建账号 {USERNAME} 失败：{res}')
    uid = res['data']['id']
    print(f'  夹具账号 {USERNAME}（id={uid}）已建')

    token_a = login(USERNAME, PASSWORD)
    token_b = login(USERNAME, PASSWORD)
    admin_second = login(ADMIN_USERNAME, ADMIN_PASSWORD)

    # ---------------- 1. 登录即建服务端会话 ----------------
    print()
    print('=== 1. 登录建会话：两次登录是两个不同的会话 ===')
    sid_a, sid_b = sid_of(token_a), sid_of(token_b)
    check_true('令牌里带会话标识', bool(sid_a), f'sid={sid_a!r}')
    check_true('两次登录的会话不同', sid_a != sid_b, f'{sid_a} vs {sid_b}')
    row_a = asyncio.run(session_row(sid_a))
    check_true('会话在库里、归属正确、状态 active',
               bool(row_a) and row_a[1] == uid and row_a[2] == 'active', str(row_a))
    check('两份凭据都能用（A）', call('GET', '/auth/me', token_a)[1].get('code'), 0)
    check('两份凭据都能用（B）', call('GET', '/auth/me', token_b)[1].get('code'), 0)

    # ---------------- 2. 登出只作废本次会话 ----------------
    print()
    print('=== 2. 登出只作废**本次**会话（不踢掉同一账号的其它登录）===')
    status, res = call('POST', '/auth/logout', token=token_a)
    check('登出成功', res.get('code'), 0)
    check('登出后旧凭据不能用（接口）', call('GET', '/auth/me', token_a)[1].get('code'), 40101)
    check('登出后旧凭据不能续期（续期同一判据）',
          call('POST', '/auth/refresh', token=token_a)[1].get('code'), 40101)
    check('同一账号的**另一个**会话不受影响', call('GET', '/auth/me', token_b)[1].get('code'), 0)
    row_a = asyncio.run(session_row(sid_a))
    check_true('库里那一行已置为 revoked 且记了原因',
               bool(row_a) and row_a[2] == 'revoked' and row_a[3] == 'logout'
               and row_a[4] is not None, str(row_a))
    row_b = asyncio.run(session_row(sid_b))
    check_true('另一个会话仍是 active', bool(row_b) and row_b[2] == 'active', str(row_b))

    # ---------------- 3. 续期属于同一会话 ----------------
    print()
    print('=== 3. 续期属于同一会话（不新建会话、sid 不变）===')
    before_rows = asyncio.run(total_session_count(uid))
    before_seen = asyncio.run(session_row(sid_b))[5]
    time.sleep(1.1)  # 让"最近一次续期时间"有机会前进（同一秒内比大小是假绿）
    status, res = call('POST', '/auth/refresh', token_b)
    check('续期成功', res.get('code'), 0)
    token_b2 = res['data']['access_token']
    check_true('续期换了新令牌', token_b2 != token_b, 'token 已更换')
    check('新令牌可用', call('GET', '/auth/me', token_b2)[1].get('code'), 0)
    check_true('续期后仍是**同一个**会话', sid_of(token_b2) == sid_b,
               f'{sid_b} -> {sid_of(token_b2)}')
    check('续期没有新建会话', asyncio.run(total_session_count(uid)), before_rows)
    after_seen = asyncio.run(session_row(sid_b))[5]
    check_true('会话上的"最近一次续期时间"前进了',
               after_seen is not None and before_seen is not None and after_seen > before_seen,
               f'{before_seen} -> {after_seen}')

    # ---------------- 4. 改（重置）密码 → 该账号全部会话失效 ----------------
    print()
    print('=== 4. 改 / 重置密码：该账号全部会话一起失效 ===')
    # 先把三个会话都造出来（A 已登出、B 已续期，再补一个）
    token_c = login(USERNAME, PASSWORD)
    check('B 的续期令牌与 C 的凭据都能用',
          [call('GET', '/auth/me', t)[1].get('code') for t in (token_b2, token_c)], [0, 0])
    check_true('改密码前，该账号有多个有效会话',
               asyncio.run(active_session_count(uid)) >= 2,
               str(asyncio.run(active_session_count(uid))))

    # 4.0 反向：被拒的更新**不**该误伤会话（防"先吊销、后校验"的顺序 bug）
    status, res = call('PATCH', f'/users/{uid}', token=admin_token,
                       body={'name': '', 'password': NEW_PASSWORD})
    check('姓名为空 → 更新被拒', res.get('code'), 40003)
    check_true('被拒的更新没有误伤会话',
               asyncio.run(active_session_count(uid)) >= 2,
               str(asyncio.run(active_session_count(uid))))
    check('被拒的更新没有改掉口令（旧口令仍能登录）',
          call('POST', '/auth/login',
               body={'username': USERNAME, 'password': PASSWORD})[1].get('code'), 0)

    # 4.1 真改密码
    status, res = call('PATCH', f'/users/{uid}', token=admin_token,
                       body={'password': NEW_PASSWORD})
    check('改密码成功', res.get('code'), 0)

    # 4.2 接口返回的**那一刻**就该没:密码和新会话状态是同一笔事务
    left = asyncio.run(active_session_count(uid))
    check('改完立刻查库：该账号有效会话已清零（与写哈希同一笔事务）', left, 0)
    check('旧凭据不能用（接口）', call('GET', '/auth/me', token_b2)[1].get('code'), 40101)
    check('旧凭据不能续期', call('POST', '/auth/refresh', token_b2)[1].get('code'), 40101)
    check('第二份旧凭据也一起失效', call('GET', '/auth/me', token_c)[1].get('code'), 40101)
    check('续期**之前**那份旧凭据同样失效（同一会话，谁也留不下来）',
          call('GET', '/auth/me', token_b)[1].get('code'), 40101)
    reasons = {
        (asyncio.run(session_row(sid_of(t))) or (None,) * 6)[3]
        for t in (token_b2, token_c)
    }
    check_true('吊销原因记为 password_change', reasons == {'password_change'}, str(reasons))
    check('旧口令不能再登录',
          call('POST', '/auth/login',
               body={'username': USERNAME, 'password': PASSWORD})[1].get('code'), 40001)
    token_new = login(USERNAME, NEW_PASSWORD)
    check('新口令可以登录', call('GET', '/auth/me', token_new)[1].get('code'), 0)
    check('**别人**的会话不受影响（admin 自己那份照常可用）',
          call('GET', '/auth/me', admin_second)[1].get('code'), 0)

    # ---------------- 5. 失效判断只看数据库（重启 / 多进程等价形态）----------------
    print()
    print('=== 5. 失效判断只看数据库（等价于"另一个进程/运维动作"）===')
    sid_new = sid_of(token_new)
    check('被盗用前的凭据可用', call('GET', '/auth/me', token_new)[1].get('code'), 0)
    # 用一条**独立连接**直接改库：不经过任何接口、也不经过本进程的任何对象。
    asyncio.run(_exec((
        "update login_sessions set status = 'revoked', revoked_reason = 'out_of_band', "
        "revoked_at = now() where sid = :s",
        {'s': sid_new},
    )))
    check('库里被改后，下一次请求立刻认账（无进程内缓存）',
          call('GET', '/auth/me', token_new)[1].get('code'), 40101)
    check('续期也立刻认账（同一判据）',
          call('POST', '/auth/refresh', token_new)[1].get('code'), 40101)

    # ---------------- 6. 没有会话标识的旧令牌一律拒绝 ----------------
    print()
    print('=== 6. 没有会话标识的旧令牌一律拒绝 ===')
    import jwt as pyjwt

    now = int(time.time())

    def mint(payload_extra: dict) -> str:
        payload = {'sub': str(uid), 'iat': now, 'exp': now + 3600, 'name': 'legacy'}
        payload.update(payload_extra)
        return pyjwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)

    legacy = mint({})
    check_true('（自签）无 sid 的令牌签得出来', bool(legacy))
    check('无 sid 的令牌不能用（接口）', call('GET', '/auth/me', legacy)[1].get('code'), 40101)
    check('无 sid 的令牌不能续期', call('POST', '/auth/refresh', legacy)[1].get('code'), 40101)
    fake = mint({'sid': 'does-not-exist'})
    check('凭空编一个 sid 也不行', call('GET', '/auth/me', fake)[1].get('code'), 40101)
    check('凭空编 sid 也不能续期', call('POST', '/auth/refresh', fake)[1].get('code'), 40101)

    # ---------------- 7. 收尾检查 ----------------
    print()
    print('=== 7. 收尾：会话随用户删除一起走（不留孤儿行）===')
    check_true('该账号此刻确实还有会话行（否则第 7 节是假绿）',
               asyncio.run(total_session_count(uid)) > 0,
               str(asyncio.run(total_session_count(uid))))
    asyncio.run(purge())
    check_true('账号已删除', asyncio.run(user_id_of(USERNAME)) is None)
    check('删除用户后会话行被级联清掉（没有孤儿）',
          asyncio.run(total_session_count(uid)), 0)
    check('admin 仍然可用（本套件全程没动它）',
          call('GET', '/auth/me', admin_second)[1].get('code'), 0)


if __name__ == '__main__':
    try:
        main()
    finally:
        # 无条件清理：中途抛异常也不会把夹具账号留在库里
        # （它带了角色、也带了会话，留着会污染后面的套件与统计）。
        asyncio.run(purge())
        print()
        if FAILURES:
            print(f'FAILED（{len(FAILURES)} 项）：' + '；'.join(FAILURES))
            sys.exit(1)
        print('全部通过 ✓')
