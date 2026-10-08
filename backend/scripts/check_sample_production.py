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
from _test_support import require_api_base, require_isolated_db

require_isolated_db()

from app.core.database import SessionLocal

FAILURES = []
BASE = require_api_base()
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


def call_raw(path, token):
    """取原始字节（下载 PDF 用）：走 JSON 的那个 call() 会把二进制读坏。"""
    req = urllib.request.Request(BASE + path, method='GET')
    if token:
        req.add_header('Authorization', 'Bearer ' + token)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.headers.get('Content-Type', ''), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, '', exc.read()


async def cleanup():
    async with SessionLocal() as s:
        cust = "(select id from customers where name like :p)"
        sample = f"(select id from sample_requests where customer_id in {cust})"
        for sql in (
            f"delete from biz_docs where customer_id in {cust}",
            # 通知与系统留痕要在样品单还在时清（判据挂在 sample_requests 上），
            # 否则下面删完样品单，这两条子查询就查空、通知永远删不掉。
            f"delete from notifications where business_type = 'sample' "
            f"and business_id in {sample}",
            f"delete from followups where followup_type = '系统' "
            f"and customer_id in {cust}",
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
    item_id = res['data']['items'][0]['id']
    check('打样申请创建成功', res.get('code'), 0)

    # 车间依据（材质/工艺/图纸版本）逐行不同 → 走明细接口，不走单头
    # （业务口径 2026-10-05：一单多样时每个商品各存一套）
    production = {
        'purpose': '客户新品打样确认',
        'target_completion_date': '2026-10-15',
        'acceptance_criteria': '尺寸公差 ±0.2mm，表面无划痕',
        'sample_fee': '800',
    }
    part_spec = {
        'craft': '注塑+表面拉丝',
        'material': '304 不锈钢',
        'drawing_version': 'DWG-2026-A3',
    }
    _, res = call('PATCH', f'/samples/{sample_id}', token=token, body=production)
    check('资料保存成功', res.get('code'), 0)
    data = res['data']
    check('用途已存', data['purpose'], production['purpose'])
    check('目标完成日已存', data['target_completion_date'], production['target_completion_date'])
    check('验收标准已存', data['acceptance_criteria'], production['acceptance_criteria'])
    check('打样费用已存', float(data['sample_fee']), 800.0)
    check('确认状态默认待确认', data['confirm_status'], 'pending')

    _, res = call(
        'PATCH', f'/samples/{sample_id}/items/{item_id}', token=token, body=part_spec
    )
    check('明细车间依据保存成功', res.get('code'), 0)
    item = res['data']['items'][0]
    check('工艺已存到明细行', item['craft'], part_spec['craft'])
    check('材质已存到明细行', item['material'], part_spec['material'])
    check('图纸版本已存到明细行', item['drawing_version'], part_spec['drawing_version'])
    # 单头不再有这三项：它们逐行不同，放单头就只有一个真相
    check('单头不再有材质字段', 'material' in res['data'], False)
    # 窄接口：多传字段要明确报错，不能静默忽略（否则前端以为改成功了）
    status, _ = call(
        'PATCH', f'/samples/{sample_id}/items/{item_id}', token=token,
        body={'quantity': '5'},
    )
    check('明细接口不收无关字段（不静默忽略）', status, 400)

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

    print('=== 3.1 制作说明的幂等靠结构化事件，不靠备注子串 ===')
    # 旧实现是 `note in sample.remark` —— 整段备注的子串匹配：新说明只要恰好是旧说明的
    # 子串（"已制作完成，等待寄出" → "已制作"）就被判成"已经写过"而**被吞掉**，
    # 界面还回"没有变化"。现在幂等只看事件 key，备注退回纯展示。
    same_at = res['data']['made_at']
    r1 = call('POST', f'/samples/{sample_id}/made', token=token,
              body={'made_at': same_at, 'remark': '已制作完成，等待寄出'})[1]
    check('第一条制作说明记成一条事件', len(r1['data'].get('made_events') or []), 2)
    r2 = call('POST', f'/samples/{sample_id}/made', token=token,
              body={'made_at': same_at, 'remark': '已制作'})[1]
    # 这条刻意不依赖新字段：旧实现下它同样会红（备注末尾不会被追加"制作说明：已制作"），
    # 所以"修复前 FAIL / 修复后 OK"对比在这条上是干净的。
    check('新说明恰好是旧说明的子串时也要留在备注里（旧实现会吞掉它）',
          (r2['data'].get('remark') or '').rstrip().endswith('制作说明：已制作'), True)
    check('新说明恰好是旧说明的子串时也算新事件',
          len(r2['data'].get('made_events') or []), 3)
    check('两条说明都留在备注里（备注只负责展示）',
          '制作说明：已制作完成，等待寄出' in (r2['data'].get('remark') or ''), True)
    r3 = call('POST', f'/samples/{sample_id}/made', token=token,
              body={'made_at': same_at, 'remark': '已制作'})[1]
    check('重发同一个请求不会重复留痕', len(r3['data'].get('made_events') or []), 3)
    r4 = call('POST', f'/samples/{sample_id}/made', token=token,
              body={'made_at': same_at, 'remark': '带显式幂等键',
                    'request_key': f'CHK{stamp}-made-1'})[1]
    check('带 request_key 的新请求记成一条新事件',
          len(r4['data'].get('made_events') or []), 4)
    r5 = call('POST', f'/samples/{sample_id}/made', token=token,
              body={'made_at': same_at, 'remark': '带显式幂等键',
                    'request_key': f'CHK{stamp}-made-1'})[1]
    check('同一 request_key 重发不再追加', len(r5['data'].get('made_events') or []), 4)

    print('=== 3.2 已制作后改车间依据：原地改被拒，只能开新修订版（§3.3）===')
    # 另造一张（不动前面那张，免得冻结影响后续步骤）：**已批准 + 已制作、尚未寄出** ——
    # 正是旧实现会"原地改成功"的那一档（旧代码只挡了 shipped/signed）。
    _, res = call('POST', '/samples', token=token, body={
        'customer_id': customer_id,
        'remark': '§3.3 修订版夹具',
        'items': [{'sku_id': sku_id, 'quantity': '3'}],
    })
    rev_id = res['data']['id']
    rev_item_id = res['data']['items'][0]['id']
    # 车间依据**走明细接口**（创建接口的单头/明细都不收 material，别指望一次带上）
    call('PATCH', f'/samples/{rev_id}/items/{rev_item_id}', token=token,
         body={'material': 'PP 中空板', 'craft': '注塑', 'drawing_version': 'DWG-V1'})
    call('POST', f'/samples/{rev_id}/approve', token=token, body={'approved': True})
    call('POST', f'/samples/{rev_id}/made', token=token, body={})

    # ① 已制作后原地改材质：必须被拒
    status, _ = call('PATCH', f'/samples/{rev_id}/items/{rev_item_id}', token=token,
                     body={'material': 'ABS 改料'})
    check('已制作后原地改车间依据被拒', status, 422)
    status, res = call('GET', f'/samples/{rev_id}', token=token)
    check('被拒后原件材质没被动过', res['data']['items'][0]['material'], 'PP 中空板')
    check('被拒后原件的制作时间还在', bool(res['data']['made_at']), True)

    # ② 开新修订版
    status, res = call('POST', f'/samples/{rev_id}/revise', token=token,
                       body={'remark': '客户改了材质要求'})
    check('开新修订版成功', res.get('code'), 0)
    # 旧代码没有这个字段/接口，下面整段用 if 守住：那样上面那条会干净地 FAIL，
    # 而不是在后面越界崩掉（崩了就看不到修复前后的对比）。
    v2 = (res.get('data') or {})
    if res.get('code') == 0:
        check('新版本号 = 旧版 + 1', v2.get('version'), 2)
        check('新版本指向旧版（修订关系）', v2.get('parent_id'), rev_id)
        check('新版本从待审批开始', v2.get('status'), 'pending')
        check('新版本不继承制作完成事实（那是旧版的既成事实）', v2.get('made_at'), None)
        check('新版本复制了明细行', len(v2.get('items') or []), 1)
        check('新版本带着旧版车间依据作为起点',
              ((v2.get('items') or [{}])[0]).get('material'), 'PP 中空板')
        check('新版本此刻还没被取代', v2.get('superseded_by'), None)

        # ③ 旧版冻结只读，但仍可读、且标出被谁取代——"历史版本可核对"
        status, _ = call('PATCH', f'/samples/{rev_id}', token=token,
                         body={'purpose': '偷改'})
        check('旧版冻结只读（写操作被拒）', status, 422)
        status, res = call('GET', f'/samples/{rev_id}', token=token)
        check('旧版仍可读', status, 200)
        check('旧版标出了被谁取代', (res.get('data') or {}).get('superseded_by'),
              v2.get('id'))
        check('旧版保留制作完成时间（事实冻结）',
              bool((res.get('data') or {}).get('made_at')), True)

        # ④ 列表默认只列当前版本，历史版本要显式要
        status, res = call('GET', '/samples?page_size=200', token=token)
        ids = [r['id'] for r in (res.get('data') or {}).get('items', [])]
        check('列表默认不出现被取代的旧版', rev_id in ids, False)
        check('列表里有新版本', v2.get('id') in ids, True)
        status, res = call('GET', '/samples?page_size=200&include_history=true', token=token)
        ids_hist = [r['id'] for r in (res.get('data') or {}).get('items', [])]
        check('include_history=true 能看到旧版（历史可核对）', rev_id in ids_hist, True)

    # ⑤ 没有制作/寄出事实的单子不给开修订版：直接改就行，多开一版只会让台账变脏
    _, res = call('POST', '/samples', token=token, body={
        'customer_id': customer_id, 'items': [{'sku_id': sku_id, 'quantity': '1'}],
    })
    no_fact_id = res['data']['id']
    status, _ = call('POST', f'/samples/{no_fact_id}/revise', token=token, body={})
    check('没有制作/寄出事实的单子不给开修订版', status, 422)

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
    snapshot = res['data']['input_snapshot']
    sections = {sec['label']: sec['value'] for sec in snapshot['sections']}
    check('用途带上了', sections.get('用途'), production['purpose'])
    check('目标完成日带上了', sections.get('目标完成日'), production['target_completion_date'])
    check('验收标准带上了', sections.get('验收标准'), production['acceptance_criteria'])
    check('打样费用带上了', sections.get('打样费用'), '800')
    # 车间依据逐行不同 → 跟着明细走，不再混进整单的 sections
    check('工艺/材质不再混进单头', '工艺 / 材质' in sections, False)
    check('图纸版本不再混进单头', '图纸版本' in sections, False)
    doc_item = snapshot['items'][0]
    check('明细带上工艺', doc_item.get('craft'), part_spec['craft'])
    check('明细带上材质', doc_item.get('material'), part_spec['material'])
    check('明细带上图纸版本', doc_item.get('drawing_version'), part_spec['drawing_version'])

    # 真的把 PDF 渲出来：明细表在"有车间依据"时会多一列，
    # 列宽是手算的、列数变了会溢出或报错——只查快照是查不出这个的
    status, ctype, payload = call_raw(f'/biz-docs/{doc_id}/download', token)
    check('打样单可下载', status, 200)
    check_true('返回 PDF 字节', payload.startswith(b'%PDF'), f'ctype={ctype}')
    check_true('PDF 有内容（明细表含车间依据列）', len(payload) > 2000, f'{len(payload)} 字节')
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
