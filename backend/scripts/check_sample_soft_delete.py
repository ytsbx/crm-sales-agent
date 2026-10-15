"""打样单软删与恢复回归（2026-10-15 补，03-API §41.17）。

主人问：「样品管理没有删除吗，就一直待在那？」—— 打样单原来**没有删除入口**：
状态机 `pending→approved→shipped→signed` 没有出口，误建的、驳回后决定作罢的
单子只能永远躺在列表里。补的入口是「**软删 + 收闸**」：只让**还没走流程**的删。

## 锁住的断言

1. **没走流程的能删**（`pending` / `rejected`）：删完默认列表看不见它，
   详情 404，**写入口也 404** —— 软删对其它一切入口都必须等于"不存在"，
   否则"删掉的单子还能被改、被审批"，软删就是个摆设；
2. **走了流程的删不掉**（`approved` / `shipped` / `signed` → 400
   `STATUS_NOT_ALLOWED`）—— 那几张连着审批留痕与车间依据，删掉对不上账；
   被拒之后单子必须**还在**、**没被动过**；
3. 「已删除」筛出来的是**只有已删的**（不是"连没删的一起列"），
   每行带 `is_deleted` / `deleted_at` / `can_delete`；
4. **恢复**：回到默认列表、`is_deleted` 回到 False；没删过的恢复请求被 400 拒；
   恢复用的是同一把钥匙（`sample:manage`），不新开权限码；
5. 删除 / 恢复各留一条审计（`delete` / `restore`，`business_type='sample'`）——
   回收站要知道"这条是谁删的"。

夹具带 CHKSD 前缀，跑完即清。

跑法（必须显式给 API_BASE，不许默认打到 8000）：
    cd backend
    API_BASE=http://127.0.0.1:8001/api/v1 \
    DATABASE_URL=postgresql+asyncpg://crm:***@127.0.0.1:5432/crm_iso_test_xxx \
    FILE_ROOT=data/iso-files-crm_iso_test_xxx \
    PYTHONPATH=. .venv/bin/python scripts/check_sample_soft_delete.py
"""

import asyncio
import json
import sys
import time
import urllib.error
import urllib.request

from sqlalchemy import select, text

from _test_support import require_api_base, require_isolated_db

require_isolated_db()

from app.core.database import SessionLocal

FAILURES = []
BASE = require_api_base()
PREFIX = 'CHKSD'


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
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or '{}')


def login() -> str:
    _, res = call('POST', '/auth/login', body={'username': 'admin', 'password': 'admin123'})
    if res.get('code') != 0:
        raise SystemExit('登录失败：后端没在给定的 API_BASE 上跑？')
    return res['data']['access_token']


def row_of(token, sample_id, *, include_deleted=False):
    """在（默认 / 已删除）列表里找出这一行；找不到返回 None。"""
    path = '/samples?page_size=200'
    if include_deleted:
        path += '&include_deleted=true'
    _, res = call('GET', path, token=token)
    items = (res.get('data') or {}).get('items') or []
    for row in items:
        if row['id'] == sample_id:
            return row
    return None


def list_ids(token, *, include_deleted=False):
    path = '/samples?page_size=200'
    if include_deleted:
        path += '&include_deleted=true'
    _, res = call('GET', path, token=token)
    items = (res.get('data') or {}).get('items') or []
    return [r['id'] for r in items]


async def cleanup():
    async with SessionLocal() as s:
        cust = "(select id from customers where name like :p)"
        sample = f"(select id from sample_requests where customer_id in {cust})"
        for sql in (
            f"delete from biz_docs where customer_id in {cust}",
            # 通知挂在 sample_requests 上，得趁样品单还在时清（判据同上一个套件）
            f"delete from notifications where business_type = 'sample' "
            f"and business_id in {sample}",
            f"delete from followups where followup_type = '系统' and customer_id in {cust}",
            f"delete from sample_items where sample_request_id in {sample}",
            f"delete from sample_shipments where sample_request_id in {sample}",
            f"delete from sample_requests where customer_id in {cust}",
            "delete from customers where name like :p",
        ):
            await s.execute(text(sql), {'p': f'{PREFIX}%'})
        await s.commit()


async def audit_actions(sample_id: int):
    async with SessionLocal() as s:
        rows = (
            await s.execute(
                text(
                    "select action from audit_logs "
                    "where business_type = 'sample' and business_id = :sid "
                    "order by id"
                ),
                {'sid': sample_id},
            )
        ).scalars().all()
        return list(rows)


async def make_sample(token, customer_id, sku_id, note):
    _, res = call('POST', '/samples', token=token, body={
        'customer_id': customer_id,
        'remark': note,
        'items': [{'sku_id': sku_id, 'quantity': '2'}],
    })
    if res.get('code') != 0:
        raise SystemExit(f'夹具打样单建不出来：{res}')
    return res['data']['id']


async def main():
    from app.modules.product.model import Sku

    token = login()
    await cleanup()
    stamp = int(time.time())

    async with SessionLocal() as s:
        sku_id = (await s.execute(select(Sku.id).limit(1))).scalar_one()

    _, res = call('POST', '/customers', token=token, body={'name': f'{PREFIX}客户-{stamp}'})
    customer_id = res['data']['id']

    print('=== 1. 刚建出来的单子：可删 ===')
    pending_id = await make_sample(token, customer_id, sku_id, 'CHKSD 待审批')
    row = row_of(token, pending_id)
    check_true('新建的单子在默认列表里', row is not None)
    check('新建的单子可删（can_delete）', row and row['can_delete'], True)
    check('新建的单子 is_deleted=False', row and row['is_deleted'], False)
    check('新建的单子 deleted_at 为空', row and row['deleted_at'], None)

    print('=== 2. 删掉它：软删，不是物理删 ===')
    status, res = call('DELETE', f'/samples/{pending_id}', token=token)
    check('未走流程的单子删除成功', status, 200)
    check('返回 code=0', res.get('code'), 0)
    check('删除后默认列表里没有它', pending_id in list_ids(token), False)
    deleted_row = row_of(token, pending_id, include_deleted=True)
    check_true('「已删除」筛选里能看见它', deleted_row is not None)
    check('已删除行 is_deleted=True', deleted_row and deleted_row['is_deleted'], True)
    check_true('已删除行带 deleted_at', bool(deleted_row and deleted_row['deleted_at']))
    check('已删除行 can_delete=False（已删的没必要再删）',
          deleted_row and deleted_row['can_delete'], False)

    print('=== 2.1 软删之后，它对其它入口等于"不存在" ===')
    status, _ = call('GET', f'/samples/{pending_id}', token=token)
    check('详情 404（已删的看不见）', status, 404)
    status, _ = call('PATCH', f'/samples/{pending_id}', token=token, body={'purpose': '偷改'})
    check('编辑入口 404（删掉的不能被改）', status, 404)
    status, _ = call('POST', f'/samples/{pending_id}/approve', token=token,
                     body={'approved': True})
    check('审批入口 404（删掉的不能被审批）', status, 404)
    status, _ = call('POST', f'/samples/{pending_id}/ship', token=token,
                     body={'carrier': '顺丰', 'tracking_no': f'CHKSD{stamp}'})
    check('寄出入口 404', status, 404)

    print('=== 3. 恢复 ===')
    status, res = call('POST', f'/samples/{pending_id}/restore', token=token)
    check('恢复成功', status, 200)
    check('返回 code=0', res.get('code'), 0)
    row = row_of(token, pending_id)
    check_true('恢复后回到默认列表', row is not None)
    check('恢复后 is_deleted=False', row and row['is_deleted'], False)
    status, _ = call('GET', f'/samples/{pending_id}', token=token)
    check('恢复后详情可读', status, 200)
    status, res = call('POST', f'/samples/{pending_id}/restore', token=token)
    check('没删过的单子恢复被拒（400）', status, 400)
    check('拒绝理由是人话', '没有被删除' in (res.get('message') or ''), True)

    print('=== 4. 走了流程的删不掉 ===')
    status, res = call('POST', f'/samples/{pending_id}/approve', token=token,
                       body={'approved': True})
    check('批准成功', res.get('code'), 0)
    row = row_of(token, pending_id)
    check('已批准的单子 can_delete=False', row and row['can_delete'], False)
    status, res = call('DELETE', f'/samples/{pending_id}', token=token)
    check('已批准的单子删除被拒（400）', status, 400)
    check('拒绝理由带上当前状态（人话）',
          '已批准' in (res.get('message') or '') or '不能删除' in (res.get('message') or ''),
          True)
    check('被拒之后它还在默认列表里', pending_id in list_ids(token), True)
    status, res = call('GET', f'/samples/{pending_id}', token=token)
    check('被拒之后单子没被动过（详情仍可读）', status, 200)
    check('被拒之后没有被软删', (res.get('data') or {}).get('is_deleted'), False)

    print('=== 4.1 已寄出 / 已签收同样删不掉 ===')
    call('POST', f'/samples/{pending_id}/ship', token=token,
         body={'carrier': '顺丰', 'tracking_no': f'CHKSD{stamp}'})
    row = row_of(token, pending_id)
    check('已寄出的 can_delete=False', row and row['can_delete'], False)
    status, _ = call('DELETE', f'/samples/{pending_id}', token=token)
    check('已寄出的删除被拒（400）', status, 400)
    call('POST', f'/samples/{pending_id}/sign', token=token, body={})
    row = row_of(token, pending_id)
    check('已签收的 can_delete=False', row and row['can_delete'], False)
    status, _ = call('DELETE', f'/samples/{pending_id}', token=token)
    check('已签收的删除被拒（400）', status, 400)

    print('=== 5. 被驳回的能删（驳回不是终态，但可以就此作罢）===')
    rejected_id = await make_sample(token, customer_id, sku_id, 'CHKSD 会被驳回')
    _, res = call('POST', f'/samples/{rejected_id}/approve', token=token,
                  body={'approved': False, 'reject_reason': '规格没写清'})
    check('驳回成功', res.get('code'), 0)
    row = row_of(token, rejected_id)
    check('已驳回的单子状态', row and row['status'], 'rejected')
    check('已驳回的单子可删', row and row['can_delete'], True)
    status, res = call('DELETE', f'/samples/{rejected_id}', token=token)
    check('已驳回的单子删除成功', status, 200)
    check('删完默认列表里没有它', rejected_id in list_ids(token), False)
    check('已删除筛选里有它',
          rejected_id in list_ids(token, include_deleted=True), True)

    print('=== 6. 「已删除」筛选里只有已删的 ===')
    deleted_ids = list_ids(token, include_deleted=True)
    check('未删的对照单不混进「已删除」列表', pending_id in deleted_ids, False)

    print('=== 7. 审计留痕 ===')
    actions = await audit_actions(pending_id)
    check('删除动作留痕了', 'delete' in actions, True)
    check('恢复动作留痕了', 'restore' in actions, True)
    check_true('时间线顺序是「先删后恢复」',
               actions.index('delete') < actions.index('restore'), f'实际序列 {actions}')
    actions = await audit_actions(rejected_id)
    check('驳回后删掉那张留了删除动作', 'delete' in actions, True)
    check('删除是它最后一条留痕（没顺手写别的）', actions[-1], 'delete')

    await cleanup()

    print()
    if FAILURES:
        print(f'FAILED {len(FAILURES)} 项：' + '、'.join(FAILURES))
        return 1
    print('打样单软删与恢复回归 全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
