"""「找个人」回归测试：GET /users 的关键字 / 状态 / 部门 / 分页四路筛选。

跑法（后端必须先起来）：
    cd backend
    API_BASE=http://127.0.0.1:8001/api/v1 \
    DATABASE_URL=postgresql+asyncpg://crm:***@127.0.0.1:5432/crm_iso_test \
    PYTHONPATH=. .venv/bin/python scripts/check_user_list_filters.py

脚本自带清库，可反复执行。

## 覆盖

  GET /users           keyword / status / department_id / page / page_size

## 重点

系统设置里「找个人」以前只能靠肉眼翻列表 —— 后端早就支持四个筛选参数，前端
一个都没传（`page_size=50` 且不分页），于是**人一多就有相当一部分人根本显示
不出来**。现在前端接上了，这里的判据就是把这条路锁死，重点盯三件事：

1. **关键字只认姓名和登录名**。这是刻意定的边界：手机号、邮箱不参与模糊匹配，
   否则搜「139」会把半个公司捞出来。判据落在「拿手机号去搜，一条都不许有」；
2. **筛选能叠加**。部门 + 状态同时给，结果是交集不是并集（只出「这个部门里
   停用的那一个」）；
3. **分页真的在翻页**，不是每次都返回同一批人。判据拿全量 id 序列做切片比对，
   换页必须换人、总数必须对得上 —— 顺带把「默认排序按 id 升序」也钉住，
   不然两页之间会漏人。

另外钉一条容易被人改坏的：停用的人**默认列表里必须还能搜到**，否则管理员
想重新启用他时反而找不到人。
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

#: 造三个人：甲（有部门·启用）、乙（同部门·停用）、丙（没部门·启用）
TMP_DEPT_NAME = f'CHK{RUN}筛选用部门'
TMP_PASSWORD = 'Tmp123456'
NAME_A = f'筛选甲{RUN}'
NAME_B = f'筛选乙{RUN}'
NAME_C = f'筛选丙{RUN}'
USER_A = f'chkA{RUN}'
USER_B = f'chkB{RUN}'
USER_C = f'chkC{RUN}'
MOBILE_A = f'139{RUN}01'
MOBILE_B = f'139{RUN}02'


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


def list_users(token, query=''):
    """调一次列表，返回 (条数, id 列表, total)。"""
    status, res = call('GET', f'/users?{query}' if query else '/users', token=token)
    if res.get('code') != 0:
        return None, [], None
    data = res['data']
    return len(data['items']), [u['id'] for u in data['items']], data['total']


async def clean(verbose=False):
    from sqlalchemy import text

    from app.core.database import SessionLocal

    # 先清引用了用户/部门的表，再清用户和部门本身
    statements = [
        ('用例登录会话', "delete from login_sessions where user_id in "
                       f"(select id from users where username like 'chk%')"),
        ('用例用户角色', "delete from user_roles where user_id in "
                       f"(select id from users where username like 'chk%')"),
        ('用例审计', "delete from audit_logs where operator_id in "
                    f"(select id from users where username like 'chk%')"),
        ('用例用户', "delete from users where username like 'chk%'"),
        ('用例部门', "delete from departments where name like 'CHK%筛选用部门'"),
    ]
    async with SessionLocal() as s:
        for label, sql in statements:
            result = await s.execute(text(sql))
            if verbose and result.rowcount:
                print(f'  {result.rowcount:>4}  {label}')
        await s.commit()


async def main():
    admin = login_token('admin', 'admin123')
    check_true('管理员登录', bool(admin))
    if not admin:
        return

    print()
    print('=== 0. 造数据：一个部门 + 三个人（甲启用 / 乙停用 / 丙没部门）===')
    status, res = call('POST', '/departments', token=admin, body={'name': TMP_DEPT_NAME})
    check('建筛选用部门', res.get('code'), 0)
    dept_id = res['data']['id']

    people = [
        (NAME_A, USER_A, MOBILE_A, dept_id, False),
        (NAME_B, USER_B, MOBILE_B, dept_id, True),
        (NAME_C, USER_C, None, None, False),
    ]
    ids = {}
    for name, username, mobile, department_id, disable in people:
        status, res = call('POST', '/users', token=admin, body={
            'name': name, 'username': username, 'password': TMP_PASSWORD,
            'mobile': mobile, 'department_id': department_id,
        })
        check(f'建用户 {name}', res.get('code'), 0)
        ids[username] = res['data']['id']
        if disable:
            status, res = call('POST', f'/users/{ids[username]}/disable', token=admin)
            check(f'停用 {name}', res.get('code'), 0)

    id_a, id_b, id_c = ids[USER_A], ids[USER_B], ids[USER_C]

    print()
    print('=== 1. 关键字：只认姓名和登录名 ===')
    _, got, _ = list_users(admin, f'keyword={urllib.parse.quote(NAME_A)}')
    check('按姓名搜到甲', got, [id_a])
    _, got, _ = list_users(admin, f'keyword={USER_A}')
    check('按登录名搜到甲', got, [id_a])
    # 刻意定的边界：手机号不参与模糊匹配，否则搜「139」会捞出半个公司
    count, got, _ = list_users(admin, f'keyword={MOBILE_A}')
    check('拿手机号搜不到人', got, [])

    print()
    print('=== 2. 状态：启用 / 停用 ===')
    _, got, _ = list_users(admin, 'status=disabled')
    check_true('停用名单里有乙', id_b in got, str(got))
    check_true('停用名单里没有甲', id_a not in got)
    check_true('停用名单里没有丙', id_c not in got)
    _, got, _ = list_users(admin, 'status=active')
    check_true('启用名单里有甲', id_a in got)
    check_true('启用名单里没有乙', id_b not in got)
    # 容易被人改坏的一条：停用的人默认列表里还得能翻到，不然想启用他时找不到
    _, got, _ = list_users(admin, '')
    check_true('默认列表也能看到停用的乙', id_b in got)

    print()
    print('=== 3. 部门 ===')
    _, got, _ = list_users(admin, f'department_id={dept_id}')
    check('部门里只有甲乙（丙没部门不算）', sorted(got), sorted([id_a, id_b]))

    print()
    print('=== 4. 筛选能叠加：部门 + 状态要取交集 ===')
    _, got, _ = list_users(admin, f'department_id={dept_id}&status=disabled')
    check('这个部门里停用的只有乙', got, [id_b])
    _, got, _ = list_users(admin, f'department_id={dept_id}&keyword={USER_A}')
    check('这个部门里再按关键字收窄', got, [id_a])

    print()
    print('=== 5. 分页：换页必须换人 ===')
    n_all, all_ids, total = list_users(admin, 'page=1&page_size=200')
    check('全量条数与 total 一致', n_all, total)
    check_true('全量按 id 升序', all_ids == sorted(all_ids))
    _, page1, total1 = list_users(admin, 'page=1&page_size=2')
    _, page2, total2 = list_users(admin, 'page=2&page_size=2')
    check('第一页就是全量的前两条', page1, all_ids[:2])
    check('第二页接着往后两条', page2, all_ids[2:4])
    check('翻页总数不变', total1, total)
    check('第二页总数也不变', total2, total)
    check_true('两页不重复', not set(page1) & set(page2), f'{page1} / {page2}')

    print()
    print('=== 6. 门槛 ===')
    status, res = call('GET', '/users')
    check('没登录不能查用户', res.get('code'), 40101)
    # 普通业务员没有「账号与权限」管理权，也不该看到全公司名册
    zhangsan = login_token('zhangsan', '123456')
    if zhangsan:
        status, res = call('GET', '/users', token=zhangsan)
        check('业务员查用户被挡', res.get('code'), 40301)
    else:
        print('  --   张三登录失败，跳过权限判据')


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
