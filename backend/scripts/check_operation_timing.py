"""操作耗时埋点回归（文档 §六「评价操作是否省时」/ 场景18）。

跑法：
    cd backend
    PYTHONPATH=. .venv/bin/python scripts/check_operation_timing.py

## 为什么要有这条

场景18 的合格条件是"操作时间与重复字段数**可与表格流程比较**"。系统里此前
一个耗时数字都没有，只能靠印象说"我们更快"——这条保证埋点通路真的能工作：
前端报得上来、后端算得出来、并且**乱报的会被挡住**（否则统计会被垃圾样本污染，
比没有数字更糟：拿一个被污染的平均值去汇报，等于用错误证据下结论）。

夹具标记：本脚本报的每一条都带 `business_type=CHK<RUN>`，收尾按它清理。
"""

import asyncio
import json
import sys
import time
import urllib.error
import urllib.request

BASE = 'http://127.0.0.1:8000/api/v1'
RUN = str(int(time.time()))[-6:]
TAG = f'CHK{RUN}'
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


def call(method, path, token=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header('Content-Type', 'application/json')
    if token:
        req.add_header('Authorization', 'Bearer ' + token)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode('utf-8', 'replace')
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {'code': None, 'message': raw[:200]}


def login(username, password):
    status, res = call('POST', '/auth/login',
                       body={'username': username, 'password': password})
    if res.get('code') != 0:
        raise SystemExit(f'登录失败：{username}（后端没在 8000 跑？）')
    return res['data']['access_token']


async def cleanup():
    from sqlalchemy import text

    from app.core.database import SessionLocal

    async with SessionLocal() as s:
        await s.execute(
            text('delete from operation_timings where business_type = :t'), {'t': TAG}
        )
        await s.commit()


async def main() -> int:
    await cleanup()
    admin = login('admin', 'admin123')
    zhangsan = login('zhangsan', '123456')

    print('=== 1. 正常上报 ===')
    status, res = call('POST', '/usage/timings', token=zhangsan, body={
        'operation': 'quote_from_inquiry', 'duration_ms': 185000,
        'business_type': TAG, 'business_id': 1,
        'typed_fields': 9, 'rework_count': 1,
    })
    check('上报成功', res.get('code'), 0)
    check('回带流程标签', res['data'].get('operation_label'), '需求 → 报价')
    check('耗时原样落库', res['data'].get('duration_ms'), 185000)

    status, res = call('POST', '/usage/timings', token=zhangsan, body={
        'operation': 'sample_from_inquiry', 'duration_ms': 60000,
        'business_type': TAG, 'typed_fields': 4, 'rework_count': 0,
    })
    check('第二条（打样）上报成功', res.get('code'), 0)

    print()
    print('=== 2. 汇总 ===')
    status, res = call('GET', '/usage/timings/summary?days=1', token=zhangsan)
    check('汇总可读', res.get('code'), 0)
    by_op = {row['operation']: row for row in res['data']['summary']}
    quote_row = by_op.get('quote_from_inquiry', {})
    check_true('报价样本被统计到', quote_row.get('samples', 0) >= 1,
               f"samples={quote_row.get('samples')}")
    check_true('平均耗时算得出来',
               isinstance(quote_row.get('avg_ms'), int), str(quote_row.get('avg_ms')))
    check_true('手输字段数与返工次数也统计', quote_row.get('avg_typed_fields') is not None
               and quote_row.get('avg_rework_count') is not None, '')
    check_true('白名单随汇总返回', len(res['data'].get('operations') or []) >= 2, '')
    # 这条不是形式主义：数字本身不能冒充场景18 的全部证据
    check_true('汇总里写明哪些还没测',
               '跨系统重复录入' in (res['data'].get('note') or ''), '')

    print()
    print('=== 3. 乱报的要挡住（不然统计被污染）===')
    status, res = call('POST', '/usage/timings', token=zhangsan,
                       body={'operation': 'not_a_real_flow', 'duration_ms': 1000})
    check('未知流程名 → 422', status, 422)
    status, res = call('POST', '/usage/timings', token=zhangsan,
                       body={'operation': 'quote_from_inquiry', 'duration_ms': 0})
    check('耗时 0 → 422', status, 422)
    status, res = call('POST', '/usage/timings', token=zhangsan,
                       body={'operation': 'quote_from_inquiry', 'duration_ms': 9 * 3600 * 1000})
    check('隔夜挂着的超长耗时 → 422', status, 422)

    print()
    print('=== 4. 数据范围：张三只看得见自己报的 ===')
    status, res = call('POST', '/usage/timings', token=admin, body={
        'operation': 'quote_from_inquiry', 'duration_ms': 300000, 'business_type': TAG,
    })
    check('管理员也报一条', res.get('code'), 0)
    status, res = call('GET', '/usage/timings/summary?days=1', token=zhangsan)
    zs_names = {row['user_name'] for row in res['data']['by_user']}
    check_true('张三的汇总里只有自己', len(zs_names) <= 1, str(zs_names))
    status, res = call('GET', '/usage/timings/summary?days=1', token=admin)
    admin_user_ids = {row['user_id'] for row in res['data']['by_user']}
    check_true('管理员看得到两个人',
               len(admin_user_ids) >= 2, str(sorted(admin_user_ids)))

    await cleanup()
    print()
    if FAILURES:
        print(f'FAILED（{len(FAILURES)}）: {FAILURES}')
        return 1
    print('操作耗时埋点 全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
