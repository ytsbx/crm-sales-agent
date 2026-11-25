"""「我的权限」回归测试：GET /auth/permissions 的中文名（permission_names）。

跑法（后端必须先起来）：
    cd backend
    API_BASE=http://127.0.0.1:8001/api/v1 \
    DATABASE_URL=postgresql+asyncpg://crm:***@127.0.0.1:5432/crm_iso_test \
    PYTHONPATH=. .venv/bin/python scripts/check_personal_permissions_api.py

脚本自带清库，可反复执行。

## 覆盖

  GET /auth/permissions   我的权限（含中文名）

## 重点

「看自己有哪些权限」这条路上真正的坑是**门槛**：权限目录接口
`GET /users/permissions` 要求「账号与权限」管理权限，普通人调不动。所以中文名
必须由 `/auth/permissions` 在服务端拼好下发 —— 这里的判据就落在：

1. **管理员、普通业务员、以及一个只挂着 `customer:view` 的临时账号**，三种身份
   都要拿得到，且每个权限码都有对应的中文名（一个都不许漏，漏了界面上就会
   露出 `customer:view` 这种英文码）；
2. 只下发**这个人自己持有**的权限的中文名，不把整个权限目录泄露出去
   （临时账号只有 1 个权限，那就只能拿到 1 条）；
3. 没登录 → `40101`。

改登录名 / 改密码那一半由 `check_personal_account.py` 覆盖，这里不重复。
"""

import asyncio
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

#: 临时账号：只有 `customer:view`，用来验证"看自己的权限"不需要管理权限
TMP_USERNAME = f'chk{RUN}'
TMP_ROLE_CODE = f'chkrole{RUN}'
TMP_PASSWORD = 'Tmp123456'


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
    safe_path = urllib.parse.quote(path, safe='/?&=%')
    req = urllib.request.Request(BASE + safe_path, data=data, method=method)
    if data is not None:
        req.add_header('Content-Type', 'application/json')
    if token:
        req.add_header('Authorization', 'Bearer ' + token)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        text = e.read().decode('utf-8', 'replace')
        try:
            return e.code, json.loads(text)
        except json.JSONDecodeError:
            return e.code, {'code': None, 'message': text[:200]}


def login_token(username, password):
    status, res = call('POST', '/auth/login', body={'username': username, 'password': password})
    if status != 200 or res.get('code') != 0:
        return None
    return res['data']['access_token']


def has_chinese(text):
    return any('一' <= ch <= '鿿' for ch in text)


async def clean(verbose=False):
    from sqlalchemy import text

    from app.core.database import SessionLocal

    statements = [
        ('用例登录会话', "delete from login_sessions where user_id in "
                       f"(select id from users where username like 'chk{RUN}%')"),
        ('用例用户角色', "delete from user_roles where user_id in "
                       f"(select id from users where username like 'chk{RUN}%')"),
        ('用例审计', "delete from audit_logs where operator_id in "
                    f"(select id from users where username like 'chk{RUN}%')"),
        ('用例用户', f"delete from users where username like 'chk{RUN}%'"),
        ('用例角色权限', "delete from role_permissions where role_id in "
                      f"(select id from roles where code like 'chkrole{RUN}%')"),
        ('用例角色', f"delete from roles where code like 'chkrole{RUN}%'"),
    ]
    async with SessionLocal() as s:
        for label, sql in statements:
            result = await s.execute(text(sql))
            if verbose and result.rowcount:
                print(f'  {result.rowcount:>4}  {label}')
        await s.commit()


def describe(label, token):
    """读一次「我的权限」，把三条判据一次验完：拿到了 / 一一对应 / 是中文。"""
    status, res = call('GET', '/auth/permissions', token=token)
    check(f'{label}读自己的权限', res.get('code'), 0)
    if res.get('code') != 0:
        return
    owned = res['data']['permissions']
    names = res['data']['permission_names']
    check_true(f'{label}带角色', 'roles' in res['data'], str(res['data'].get('roles')))
    # 每个权限码都要有对应的中文名：漏一个，界面上就会露出英文码
    check(f'{label}中文名一一对应', sorted(names.keys()), sorted(owned))
    translated = [v for v in names.values() if has_chinese(v)]
    check_true(f'{label}中文名确实是中文', len(translated) == len(owned),
               f'{len(translated)}/{len(owned)}')


async def main():
    admin = login_token('admin', 'admin123')
    zhangsan = login_token('zhangsan', '123456')
    check_true('管理员登录', bool(admin))
    check_true('张三登录', bool(zhangsan))

    print()
    print('=== 1. 管理员 ===')
    describe('管理员', admin)

    print()
    print('=== 2. 普通业务员（没有「账号与权限」管理权）===')
    describe('张三', zhangsan)

    print()
    print('=== 3. 只挂一个权限的临时账号 ===')
    status, res = call('POST', '/roles', token=admin, body={
        'code': TMP_ROLE_CODE, 'name': f'CHK{RUN}看权限角色', 'data_scope': 'self',
        'permission_codes': ['customer:view'],
    })
    check('建临时角色', res.get('code'), 0)
    tmp_role = res['data']['id']

    status, res = call('POST', '/users', token=admin, body={
        'name': f'CHK{RUN}看权限', 'username': TMP_USERNAME, 'password': TMP_PASSWORD,
    })
    check('建临时用户', res.get('code'), 0)
    tmp_user = res['data']['id']
    call('PUT', f'/users/{tmp_user}/roles', token=admin, body={'role_ids': [tmp_role]})

    tmp_token = login_token(TMP_USERNAME, TMP_PASSWORD)
    check_true('临时账号登录成功', bool(tmp_token))
    describe('临时账号', tmp_token)
    # 只下发自己持有的那部分，不把整个权限目录泄露出去
    status, res = call('GET', '/auth/permissions', token=tmp_token)
    check('只拿到自己那一个权限', res['data']['permissions'], ['customer:view'])
    check('中文名也只一条', len(res['data']['permission_names']), 1)

    print()
    print('=== 4. 没登录不能读 ===')
    status, res = call('GET', '/auth/permissions')
    check('无 token 读权限', res.get('code'), 40101)


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
