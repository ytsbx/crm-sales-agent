"""线索导入导出/软删恢复 + 认证续期 + 角色权限与数据范围 回归测试。

跑法（后端必须先起来，默认 http://127.0.0.1:8000）：
    cd backend
    $env:PYTHONPATH="."
    .venv\\Scripts\\python.exe scripts\\check_lead_auth_role_api.py

脚本自带清库，可反复执行。

## 覆盖

线索（03-API §6）：
  GET    /leads/import-template
  GET    /leads/export
  POST   /leads/export
  POST   /leads/import
  DELETE /leads/{id}           软删；已转化的不给删
  POST   /leads/{id}/restore   回收站恢复（同样要过数据范围）

认证（03-API §2）：
  POST /auth/refresh
  POST /auth/sso/wecom/callback  未配企微必须明确报错，不许假装成功

角色（03-API §5）：
  GET/PUT /roles/{id}/permissions
  GET/PUT /roles/{id}/data-scope

## 重点

`/auth/refresh` 的价值在"过期了还能救"，所以必须验证**过期后的宽限期内仍能续期**，
以及"停用的账号不能续期"（否则一个后台页面能把停用账号无限续命）。
这两条用直接构造 JWT 的方式测，比等 12 小时现实。
"""

import asyncio
import os
import csv
import io
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get('API_BASE', 'http://127.0.0.1:8000/api/v1')
RUN = str(int(time.time()))[-6:]
FAILURES = []


def check(label, actual, expected):
    good = actual == expected
    print(f'  {"OK  " if good else "FAIL"} {label}: {actual!r}（期望 {expected!r}）')
    if not good:
        FAILURES.append(label)


def check_true(label, condition, detail=''):
    print(f'  {"OK  " if condition else "FAIL"} {label}{f"：{detail}" if detail else ""}')
    if not condition:
        FAILURES.append(label)


def call(method, path, token=None, body=None, raw_body=None, content_type='application/json'):
    data = raw_body if raw_body is not None else (
        json.dumps(body).encode() if body is not None else None
    )
    # 查询串里可能带中文（关键字搜索），urllib 要求请求行是 ASCII，先编码
    safe_path = urllib.parse.quote(path, safe='/?&=%')
    req = urllib.request.Request(BASE + safe_path, data=data, method=method)
    if data is not None:
        req.add_header('Content-Type', content_type)
    if token:
        req.add_header('Authorization', 'Bearer ' + token)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            ctype = resp.headers.get('content-type', '')
            if 'json' not in ctype:
                return resp.status, {
                    '_text': raw.decode('utf-8-sig', 'replace'),
                    '_ctype': ctype,
                    '_disposition': resp.headers.get('content-disposition', ''),
                }
            return resp.status, json.loads(raw.decode())
    except urllib.error.HTTPError as e:
        raw = e.read()
        text = raw.decode('utf-8', 'replace')
        try:
            return e.code, json.loads(text)
        except json.JSONDecodeError:
            return e.code, {'code': None, 'message': text[:200]}


def login(username, password):
    return call('POST', '/auth/login', body={'username': username, 'password': password})[1][
        'data'
    ]['access_token']


def csv_bytes(rows, headers):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    writer.writerows(rows)
    return buf.getvalue().encode('utf-8-sig')


def upload_csv(token, path, headers, rows):
    """构造 multipart/form-data 上传。"""
    boundary = '----crmcheck' + RUN
    body = io.BytesIO()
    payload = csv_bytes(rows, headers)
    body.write(f'--{boundary}\r\n'.encode())
    body.write(
        b'Content-Disposition: form-data; name="file"; filename="t.csv"\r\n'
        b'Content-Type: text/csv\r\n\r\n'
    )
    body.write(payload)
    body.write(f'\r\n--{boundary}--\r\n'.encode())
    return call(
        'POST',
        path,
        token=token,
        raw_body=body.getvalue(),
        content_type=f'multipart/form-data; boundary={boundary}',
    )


async def clean(verbose=False):
    from sqlalchemy import text

    from app.core.database import SessionLocal

    statements = [
        ('用例线索归属', "delete from lead_assignments where lead_id in "
                      f"(select id from leads where name like 'CHK{RUN}%')"),
        ('用例线索', f"delete from leads where name like 'CHK{RUN}%'"),
        # 线索转化（用例 5）会真的建一个客户，名字沿用线索名，此前只删了线索，
        # 客户就一直留在库里。先清挂在客户下的联系人与归属历史，再删客户本身。
        ('用例转化客户联系人', "delete from contacts where customer_id in "
                        f"(select id from customers where name like 'CHK{RUN}%')"),
        ('用例转化客户归属', "delete from customer_owner_history where customer_id in "
                       f"(select id from customers where name like 'CHK{RUN}%')"),
        ('用例转化客户', f"delete from customers where name like 'CHK{RUN}%'"),
        ('用例角色', f"delete from role_permissions where role_id in "
                   f"(select id from roles where code like 'chk{RUN}%')"),
        ('用例角色', f"delete from roles where code like 'chk{RUN}%'"),
    ]
    async with SessionLocal() as s:
        for label, sql in statements:
            result = await s.execute(text(sql))
            if verbose and result.rowcount:
                print(f'  {result.rowcount:>4}  {label}')
        await s.commit()


def main():
    admin = login('admin', 'admin123')
    zhangsan = login('zhangsan', '123456')

    status, res = call('GET', '/auth/me', token=admin)
    admin_id = res['data']['id']
    status, res = call('GET', '/auth/me', token=zhangsan)
    zs_id = res['data']['id']

    print()
    print('=== 1. 线索导入模板 ===')
    status, res = call('GET', '/leads/import-template', token=admin)
    check('下载模板', status, 200)
    check_true('是 CSV', 'text/csv' in res.get('_ctype', ''), res.get('_ctype'))
    check_true('表头含线索名称', '线索名称' in res.get('_text', ''), res.get('_text', '')[:60])

    print()
    print('=== 2. 线索导入（查重口径：公司名或手机号完全相同才跳过）===')
    headers = ['线索名称', '公司名称', '联系人', '手机号', '省份', '来源']
    rows = [
        [f'CHK{RUN}线索A', f'CHK{RUN}甲公司', '王经理', f'137{RUN}01', '浙江', '展会'],
        [f'CHK{RUN}线索B', f'CHK{RUN}乙公司', '李经理', f'137{RUN}02', '江苏', '官网'],
        # 与第一行同公司名 + 同手机号 -> 应跳过
        [f'CHK{RUN}线索A重复', f'CHK{RUN}甲公司', '王经理', f'137{RUN}01', '浙江', '展会'],
        # 同手机号不同公司 -> 也应跳过（手机号一致）
        [f'CHK{RUN}线索C', f'CHK{RUN}丙公司', '赵经理', f'137{RUN}01', '上海', '展会'],
        # 只有名称，应成功
        [f'CHK{RUN}线索D', '', '', '', '', ''],
    ]
    status, res = upload_csv(admin, '/leads/import', headers, rows)
    check('导入成功', res.get('code'), 0)
    check('总行数 5', res['data']['total'], 5)
    check('成功 3 条', res['data']['created_count'], 3)
    check('跳过 2 条', res['data']['skipped_count'], 2)
    check('失败 0 条', res['data']['failed_count'], 0)

    status, res = upload_csv(admin, '/leads/import', ['错误表头'], [['x']])
    check('表头不对被拒', res.get('code'), 40001)
    check_true('说明缺哪个表头', '表头缺少' in (res.get('message') or ''),
               res.get('message') or '')

    print()
    print('=== 3. 线索导出 ===')
    status, res = call('GET', f'/leads/export?keyword=CHK{RUN}', token=admin)
    check('GET 导出', status, 200)
    check_true('带文件名', 'leads.csv' in res.get('_disposition', ''),
               res.get('_disposition'))
    text = res.get('_text', '')
    check_true('含导入的线索', f'CHK{RUN}线索A' in text, text[:80])
    check_true('不含被跳过的', f'CHK{RUN}线索A重复' not in text, '去重生效')

    status, res = call('POST', '/leads/export', token=admin,
                       body={'keyword': f'CHK{RUN}线索B'})
    check('POST 筛选导出', status, 200)
    text_b = res.get('_text', '')
    check_true('只含线索B', f'CHK{RUN}线索B' in text_b, text_b[:80])
    check_true('不含线索A', f'CHK{RUN}线索A' not in text_b, '筛选生效')

    print()
    print('=== 4. 线索软删与恢复 ===')
    status, res = call('GET', f'/leads?keyword=CHK{RUN}线索A', token=admin)
    check('找到线索A', res.get('code'), 0)
    lead_a = res['data']['items'][0]['id']
    status, res = call('GET', f'/leads?keyword=CHK{RUN}线索D', token=admin)
    lead_d = res['data']['items'][0]['id']

    status, res = call('DELETE', f'/leads/{lead_a}', token=admin)
    check('软删线索', res.get('code'), 0)

    status, res = call('GET', f'/leads/{lead_a}', token=admin)
    check('删除后读不到', res.get('code'), 40401)
    status, res = call('GET', f'/leads?keyword=CHK{RUN}线索A', token=admin)
    check('删除后不在列表', res['data']['total'], 0)

    status, res = call('GET', f'/leads/export?keyword=CHK{RUN}线索A&include_deleted=true',
                       token=admin)
    check('导出可带回收站', status, 200)
    check_true('回收站里能看到', f'CHK{RUN}线索A' in res.get('_text', ''), 'include_deleted 生效')

    status, res = call('POST', f'/leads/{lead_a}/restore', token=admin)
    check('恢复线索', res.get('code'), 0)
    status, res = call('GET', f'/leads/{lead_a}', token=admin)
    check('恢复后可读', res.get('code'), 0)

    status, res = call('POST', f'/leads/{lead_a}/restore', token=admin)
    check('重复恢复被拒', res.get('code'), 40002)
    status, res = call('DELETE', '/leads/999999', token=admin)
    check('删除不存在的线索', res.get('code'), 40401)
    status, res = call('POST', '/leads/999999/restore', token=admin)
    check('恢复不存在的线索', res.get('code'), 40401)

    print()
    print('=== 5. 已转化的线索不给删 ===')
    status, res = call('POST', f'/leads/{lead_d}/convert', token=admin, body={})
    check('转化线索', res.get('code'), 0)
    status, res = call('DELETE', f'/leads/{lead_d}', token=admin)
    check('已转化不能删', res.get('code'), 40002)
    check_true('提示走废弃', '废弃' in (res.get('message') or ''), res.get('message') or '')

    print()
    print('=== 6. 线索数据范围 ===')
    # 线索A 的负责人是 admin（导入未指定负责人 -> 落在操作人身上）
    # 40301/40302 都算拒绝：张三本来就没有 lead:manage，会更早被权限点挡下，
    # 这比数据范围拒绝更严格，不是缺陷。
    status, res = call('DELETE', f'/leads/{lead_a}', token=zhangsan)
    check_true('张三删别人的线索被拒', res.get('code') in (40301, 40302), str(res.get('code')))
    status, res = call('POST', f'/leads/{lead_a}/restore', token=zhangsan)
    check_true('张三恢复别人的线索被拒', res.get('code') in (40301, 40302), str(res.get('code')))

    # 给张三 lead:assign 才能测到"有权限但不在范围内"的那一层
    status, res = call('POST', '/roles', token=admin, body={
        'code': f'chkzs{RUN}', 'name': f'CHK{RUN}张三临时角色', 'data_scope': 'self',
        'permission_codes': ['lead:view', 'lead:assign'],
    })
    check('建临时角色', res.get('code'), 0)
    tmp_role = res['data']['id']
    status, res = call('GET', f'/users/{zs_id}/roles', token=admin)
    original_role_ids = [r['id'] for r in res['data']]
    call('PUT', f'/users/{zs_id}/roles', token=admin,
         body={'role_ids': original_role_ids + [tmp_role]})
    zs2 = login('zhangsan', '123456')

    status, res = call('DELETE', f'/leads/{lead_a}', token=zs2)
    check('张三有 lead:assign 但不在范围内 -> 40302', res.get('code'), 40302)

    # admin 删掉，然后验证恢复接口的范围校验。
    # （必须先真删，否则恢复接口会先撞"该线索没有被删除"的 40002）
    status, res = call('DELETE', f'/leads/{lead_a}', token=admin)
    check('admin 删掉线索准备测恢复', res.get('code'), 0)
    status, res = call('POST', f'/leads/{lead_a}/restore', token=zs2)
    check('张三有 lead:assign 但越范围 -> 40302', res.get('code'), 40302)
    status, res = call('POST', f'/leads/{lead_a}/restore', token=admin)
    check('admin 恢复成功', res.get('code'), 0)

    # 还原张三的角色
    call('PUT', f'/users/{zs_id}/roles', token=admin, body={'role_ids': original_role_ids})
    call('DELETE', f'/roles/{tmp_role}', token=admin)

    status, res = call('GET', f'/leads/export?keyword=CHK{RUN}', token=zhangsan)
    check('张三导出请求成功', status, 200)
    check_true('拿不到别人的线索', f'CHK{RUN}线索A' not in res.get('_text', ''),
               res.get('_text', '')[:80])

    print()
    print('=== 7. POST /auth/refresh ===')
    status, res = call('POST', '/auth/refresh', token=admin)
    check('续期成功', res.get('code'), 0)
    new_token = res['data']['access_token']
    check_true('发的是新 token', new_token != admin, 'token 已更换')
    check('新 token 可用', call('GET', '/auth/me', token=new_token)[1].get('code'), 0)

    status, res = call('POST', '/auth/refresh')
    check('无 token 不能续期', res.get('code'), 40101)

    status, res = call('POST', '/auth/refresh', token='not.a.jwt')
    check('伪造 token 不能续期', res.get('code') in (40101, 40102), True)

    print()
    print('=== 8. 过期/停用账号不能续期（直接构造 JWT 验证）===')
    import jwt as pyjwt

    sys.path.insert(0, '.')
    from app.core.config import settings as app_settings

    def make_token(user_id, *, expired_minutes_ago=0):
        now = int(time.time())
        payload = {
            'sub': str(user_id),
            'iat': now - 3600,
            'exp': now - expired_minutes_ago * 60,
            'name': 'test',
        }
        return pyjwt.encode(payload, app_settings.jwt_secret,
                            algorithm=app_settings.jwt_algorithm)

    # 过期 5 分钟，在宽限期内 -> 应该能续
    status, res = call('POST', '/auth/refresh', token=make_token(admin_id, expired_minutes_ago=5))
    check('过期 5 分钟内可续期', res.get('code'), 0)

    # 过期远超宽限期 -> 必须重新登录
    status, res = call('POST', '/auth/refresh', token=make_token(admin_id, expired_minutes_ago=100000))
    check('过期太久不能续期', res.get('code'), 40102)

    # 停用账号即使 token 有效也不能续
    call('POST', f'/users/{zs_id}/disable', token=admin)
    status, res = call('POST', '/auth/refresh', token=make_token(zs_id))
    check('停用账号不能续期', res.get('code'), 40101)
    call('POST', f'/users/{zs_id}/enable', token=admin)
    status, res = call('POST', '/auth/refresh', token=make_token(zs_id))
    check('重新启用后可续期', res.get('code'), 0)

    print()
    print('=== 9. 企微 SSO 回调（未配凭据必须明确报错）===')
    status, res = call('POST', '/auth/sso/wecom/callback', body={'wecom_userid': 'x'})
    check_true('未配企微 -> 非 0', res.get('code') not in (0, None),
               f'code={res.get("code")} msg={res.get("message")!r}')
    # 两种未就绪都算对：开发机可能已配真实凭据（走到"尚未绑定"），
    # CI 没配凭据（走到"未配置"）——共同点是都明确报错而不是假装成功
    _sso_msg = res.get('message') or ''
    check_true('未就绪要有明确说明', ('未配置' in _sso_msg) or ('尚未绑定' in _sso_msg),
               _sso_msg)

    print()
    print('=== 10. 角色权限（GET/PUT /roles/{id}/permissions）===')
    status, res = call('POST', '/roles', token=admin, body={
        'code': f'chk{RUN}', 'name': f'CHK{RUN}测试角色', 'data_scope': 'self',
        'permission_codes': ['customer:view'],
    })
    check('建测试角色', res.get('code'), 0)
    role_id = res['data']['id']

    status, res = call('GET', f'/roles/{role_id}/permissions', token=admin)
    check('读权限', res.get('code'), 0)
    check('初始 1 个权限', res['data']['permission_codes'], ['customer:view'])

    status, res = call('PUT', f'/roles/{role_id}/permissions', token=admin,
                       body={'permission_codes': ['customer:view', 'lead:view']})
    check('覆盖权限', res.get('code'), 0)
    check('变成 2 个', sorted(res['data']['permission_codes']),
          ['customer:view', 'lead:view'])

    status, res = call('GET', f'/roles/{role_id}/permissions', token=admin)
    check('再读确认', sorted(res['data']['permission_codes']),
          ['customer:view', 'lead:view'])

    status, res = call('PUT', f'/roles/{role_id}/permissions', token=admin,
                       body={'permission_codes': []})
    check('可以清空（合法操作）', res.get('code'), 0)
    check('已清空', res['data']['permission_codes'], [])

    status, res = call('PUT', f'/roles/{role_id}/permissions', token=admin,
                       body={'permission_codes': ['不存在的权限码']})
    check('未知权限码被拒', res.get('code') not in (0,), True)
    check_true('明确报错', bool(res.get('message')), res.get('message') or '')

    status, res = call('GET', '/roles/999999/permissions', token=admin)
    check('角色不存在', res.get('code'), 40401)

    print()
    print('=== 11. 角色数据范围（GET/PUT /roles/{id}/data-scope）===')
    status, res = call('GET', f'/roles/{role_id}/data-scope', token=admin)
    check('读数据范围', res.get('code'), 0)
    check('初始 self', res['data']['data_scope'], 'self')
    check_true('带中文标签', res['data']['data_scope_label'] == '仅本人',
               res['data']['data_scope_label'])
    check_true('带影响人数', 'users' in res['data'], str(res['data'].get('users')))

    status, res = call('PUT', f'/roles/{role_id}/data-scope', token=admin,
                       body={'data_scope': 'department_and_sub'})
    check('改数据范围', res.get('code'), 0)
    check('已改为 department_and_sub', res['data']['data_scope'], 'department_and_sub')

    status, res = call('GET', f'/roles/{role_id}/data-scope', token=admin)
    check('再读确认', res['data']['data_scope'], 'department_and_sub')

    status, res = call('PUT', f'/roles/{role_id}/data-scope', token=admin,
                       body={'data_scope': 'bogus'})
    check('非法数据范围被拒', res.get('code'), 40001)

    # 改数据范围不应动到权限集合
    call('PUT', f'/roles/{role_id}/permissions', token=admin,
         body={'permission_codes': ['customer:view']})
    call('PUT', f'/roles/{role_id}/data-scope', token=admin, body={'data_scope': 'all'})
    status, res = call('GET', f'/roles/{role_id}/permissions', token=admin)
    check('改范围不影响权限', res['data']['permission_codes'], ['customer:view'])

    print()
    print('=== 12. 角色接口的权限门槛 ===')
    for label, method, path, body in [
        ('读权限', 'GET', f'/roles/{role_id}/permissions', None),
        ('改权限', 'PUT', f'/roles/{role_id}/permissions', {'permission_codes': []}),
        ('读数据范围', 'GET', f'/roles/{role_id}/data-scope', None),
        ('改数据范围', 'PUT', f'/roles/{role_id}/data-scope', {'data_scope': 'all'}),
    ]:
        status, res = call(method, path, token=zhangsan, body=body)
        check(f'张三{label}被拒', res.get('code'), 40301)


if __name__ == '__main__':
    async def _driver():
        print('=== 清库（跑前）===')
        await clean(verbose=True)
        print()
        try:
            main()
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
