"""打样生产侧资料与客户确认回归（文档 §3.5）。

跑法（后端要在 8000 跑着——这条规则在接口层）：
    cd backend
    PYTHONPATH=. .venv/bin/python scripts/check_sample_production.py

## 文档原话

> **生产打样**记录用途、工艺/材质、图纸版本、样品数量、目标完成日、验收标准、
> 费用和责任人，再分别记录制作、寄出、签收、客户确认。**客户收到样品不等于
> 样品被接受。**

## 锁住的断言

1. 生产资料能从跟单补进来（用途/工艺/材质/图纸版本/目标完成日/验收标准/费用/责任人）；
2. **没批准不能登记制作完成**（422）——制作发生在批准之后，顺序不能反；
3. **没签收不能登记客户确认**（422）——"客户收到样品"才是确认的前提，
   这条不锁住，库里就会出现"客户还没收到就接受了"的假数据；
4. 签收后确认：`confirm_status` 变 accepted/rejected、带确认时间与备注，
   且**签收时间与确认时间是两个事实**（都留着，互不覆盖）；
5. **打样需求单要带上生产资料**——这才是这几项字段的意义：
   单子发给车间得能干活（知道材质、图纸版本、什么时候要、按什么验收）。

夹具带 CHKSP 前缀，跑完即清。
"""

import asyncio
import json
import sys
import time
import urllib.error
import urllib.request

from sqlalchemy import select, text

from app.core.database import SessionLocal

FAILURES = []
BASE = 'http://127.0.0.1:8000/api/v1'
PREFIX = 'CHKSP'


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
        raise SystemExit('登录失败：后端没在 8000 跑？')
    return res['data']['access_token']


async def cleanup():
    async with SessionLocal() as s:
        cust = "(select id from customers where name like :p)"
        sample = f"(select id from sample_requests where customer_id in {cust})"
        for sql in (
            f"delete from biz_docs where customer_id in {cust}",
            f"delete from sample_items where sample_request_id in {sample}",
            f"delete from sample_shipments where sample_request_id in {sample}",
            f"delete from sample_requests where customer_id in {cust}",
            "delete from customers where name like :p",
        ):
            await s.execute(text(sql), {'p': f'{PREFIX}%'})
        await s.commit()


async def main():
    from app.modules.product.model import Sku

    token = login()
    await cleanup()
    stamp = int(time.time())

    async with SessionLocal() as s:
        sku_id = (await s.execute(select(Sku.id).limit(1))).scalar_one()

    print('=== 1. 建单并补生产资料 ===')
    _, res = call('POST', '/customers', token=token, body={'name': f'{PREFIX}客户-{stamp}'})
    customer_id = res['data']['id']
    _, res = call(
        'POST',
        '/samples',
        token=token,
        body={
            'customer_id': customer_id,
            'remark': '客户要求先看样',
            'items': [{'sku_id': sku_id, 'quantity': '12'}],
        },
    )
    sample_id = res['data']['id']
    check('打样申请创建成功', res.get('code'), 0)

    production = {
        'purpose': '客户新品打样确认',
        'craft': '注塑+表面拉丝',
        'material': '304 不锈钢',
        'drawing_version': 'DWG-2026-A3',
        'target_completion_date': '2026-10-15',
        'acceptance_criteria': '尺寸公差 ±0.2mm，表面无划痕',
        'sample_fee': '800',
    }
    _, res = call('PATCH', f'/samples/{sample_id}', token=token, body=production)
    check('资料保存成功', res.get('code'), 0)
    data = res['data']
    check('用途已存', data['purpose'], production['purpose'])
    check('图纸版本已存', data['drawing_version'], production['drawing_version'])
    check('目标完成日已存', data['target_completion_date'], production['target_completion_date'])
    check('验收标准已存', data['acceptance_criteria'], production['acceptance_criteria'])
    check('打样费用已存', float(data['sample_fee']), 800.0)
    check('确认状态默认待确认', data['confirm_status'], 'pending')

    print('=== 2. 顺序不能反：没批准不登记制作 ===')
    status, res = call(
        'POST', f'/samples/{sample_id}/made', token=token, body={}
    )
    check('未批准登记制作被拒', status, 422)

    print('=== 3. 客户收到 ≠ 客户接受 ===')
    call('POST', f'/samples/{sample_id}/approve', token=token, body={'approved': True})
    status, res = call('POST', f'/samples/{sample_id}/made', token=token, body={})
    check('批准后可登记制作完成', res.get('code'), 0)
    check_true('制作时间已记', bool(res['data']['made_at']), f"made_at={res['data']['made_at']}")

    status, res = call(
        'POST', f'/samples/{sample_id}/confirm', token=token, body={'accepted': True}
    )
    check('还没签收就确认被拒', status, 422)

    call(
        'POST',
        f'/samples/{sample_id}/ship',
        token=token,
        body={'carrier': '顺丰', 'tracking_no': f'SF{stamp}'},
    )
    call('POST', f'/samples/{sample_id}/sign', token=token, body={})
    _, res = call('GET', f'/samples/{sample_id}', token=token)
    signed = res['data']
    check('已签收', signed['status'], 'signed')
    check('签收后仍只是"待客户确认"（收到 ≠ 接受）', signed['confirm_status'], 'pending')
    check('签收时间已记', bool(signed['signed_at']), True)
    check('确认时间还没记', signed['customer_confirmed_at'], None)

    print('=== 4. 客户确认 ===')
    _, res = call(
        'POST',
        f'/samples/{sample_id}/confirm',
        token=token,
        body={'accepted': True, 'remark': '客户确认可以做正式订单'},
    )
    check('确认成功', res.get('code'), 0)
    confirmed = res['data']
    check('确认状态', confirmed['confirm_status'], 'accepted')
    check('确认状态标签', confirmed['confirm_status_label'], '客户已接受')
    check_true('确认时间已记', bool(confirmed['customer_confirmed_at']))
    check('签收时间没被确认覆盖', confirmed['signed_at'], signed['signed_at'])
    check('确认备注落库', confirmed['confirm_remark'], '客户确认可以做正式订单')

    print('=== 5. 打样需求单带上生产资料（车间才能照单干活） ===')
    _, res = call(
        'POST',
        '/biz-docs/sample-request',
        token=token,
        body={'sample_request_id': sample_id},
    )
    doc_id = res['data']['id']
    _, res = call('GET', f'/biz-docs/{doc_id}', token=token)
    sections = {sec['label']: sec['value'] for sec in res['data']['input_snapshot']['sections']}
    check('用途带上了', sections.get('用途'), production['purpose'])
    check('工艺/材质带上了', sections.get('工艺 / 材质'), '注塑+表面拉丝 / 304 不锈钢')
    check('图纸版本带上了', sections.get('图纸版本'), production['drawing_version'])
    check('目标完成日带上了', sections.get('目标完成日'), production['target_completion_date'])
    check('验收标准带上了', sections.get('验收标准'), production['acceptance_criteria'])
    check('打样费用带上了', sections.get('打样费用'), '800')
    # 文档里这一项刻意带上确认日期：车间/业务看到的是"哪天客户认可的"
    check_true(
        '客户确认状态带上了（含确认日期）',
        (sections.get('客户确认') or '').startswith('客户已接受')
        and '（20' in (sections.get('客户确认') or ''),
        f"实际 {sections.get('客户确认')!r}",
    )
    check('样品数量来自明细', str(res['data']['input_snapshot']['items'][0]['quantity']), '12')

    await cleanup()

    print()
    if FAILURES:
        print(f'FAILED {len(FAILURES)} 项：' + '、'.join(FAILURES))
        return 1
    print('打样生产侧资料回归 全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
