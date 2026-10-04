"""定制无 SKU 通路回归（文档场景09；后端必须先起来）。

跑法：
    cd backend
    PYTHONPATH=. .venv/bin/python scripts/check_custom_no_sku.py

## 覆盖

文档场景09：「尚无正式 SKU 时，用需求编号也能询价、报价、打样，投产之后再关联上」。
此前三处硬约束让这条路整条走不通（报价/打样都强制 sku_id，需求连编号都没有）。

1. 需求有编号（XQ+日期+4 位，走取号器）；
2. 报价明细可无 SKU：靠需求编号 + 人工核价的成本与报价，快照要落全；
3. 打样明细可无 SKU：靠需求编号，展示名取需求标题；
4. 负面路径拒得明白：两者都不给、不给成本、不给报价、打样不给依据——
   尤其"不给成本"：按 0 记成本会算出 100% 毛利、低价审批永不触发；
5. 需求被报价引用后状态转「已转商机」（只在待评估/开发中时翻转）。

夹具全部带 CHK 前缀，脚本开头先清残留、结尾再清一次，可反复执行。
"""

import asyncio
import json
import sys
import time
import urllib.error
import urllib.request

BASE = 'http://127.0.0.1:8000/api/v1'
FAILURES = []
STAMP = str(int(time.time()))


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
    except urllib.error.HTTPError as error:
        raw = error.read().decode()
        try:
            return error.status, json.loads(raw or '{}')
        except json.JSONDecodeError:
            return error.status, {'message': raw[:200]}
    except urllib.error.URLError as error:
        # 网络层错误（连接被掐断、后端被回收等）以前会直接抛出、中断脚本，
        # 于是前面建的 auto_pass 规则（=任意金额自动过审）来不及删就留在库里。
        # 兜成 status=0，让后续断言正常红、脚本能走到收尾清理，而不是半路崩。
        return 0, {'message': f'URLError: {error}'}


def login(username, password):
    _, payload = call('POST', '/auth/login', body={'username': username, 'password': password})
    return payload['data']['access_token']


async def _purge(s, ids):
    """按依赖顺序清夹具。

    库里不少跨模块引用没有外键，但商机阶段历史、报价明细/费用、打样明细有——
    顺序错了就会卡在外键上（第一次跑残留就是这么来的）。
    """
    from sqlalchemy import text

    # 订单最先清（转单用例会造单；订单与报价/商机之间没有外键，但明细必须先走）
    for order_id in ids.get('orders', []):
        oi = {'o': order_id}
        await s.execute(text('delete from payment_records where order_id = :o'), oi)
        await s.execute(text('delete from receivable_plans where order_id = :o'), oi)
        await s.execute(text('delete from order_status_history where order_id = :o'), oi)
        await s.execute(text('delete from order_milestones where order_id = :o'), oi)
        await s.execute(text(
            'delete from order_shipment_batch_items where batch_id in '
            '(select id from order_shipment_batches where order_id = :o)'
        ), oi)
        await s.execute(text('delete from order_shipment_batches where order_id = :o'), oi)
        await s.execute(text('delete from sales_order_items where order_id = :o'), oi)
        await s.execute(text(
            "delete from notifications where business_type='order' and business_id = :o"
        ), oi)
        await s.execute(text('delete from sales_orders where id = :o'), oi)

    for quote_id in ids.get('quotes', []):
        q = {'q': quote_id}
        await s.execute(text('delete from followups where quote_id = :q'), q)
        await s.execute(text('delete from tasks where quote_id = :q'), q)
        for table in ('quote_items', 'quote_charges', 'quote_send_logs'):
            await s.execute(text(
                f'delete from {table} where quote_version_id in '
                '(select id from quote_versions where quote_id = :q)'
            ), q)
        await s.execute(text('delete from quote_versions where quote_id = :q'), q)
        await s.execute(text('delete from quotes where id = :q'), q)
        await s.execute(text(
            "delete from audit_logs where business_type in ('quote','quote_version') "
            'and business_id = :q'
        ), q)
    for sample_id in ids.get('samples', []):
        si = {'s': sample_id}
        await s.execute(text('delete from sample_shipments where sample_request_id = :s'), si)
        await s.execute(text('delete from sample_items where sample_request_id = :s'), si)
        await s.execute(text('delete from sample_requests where id = :s'), si)
        await s.execute(text(
            "delete from audit_logs where business_type='sample' and business_id = :s"
        ), si)
    for inquiry_id in ids.get('inquiries', []):
        ii = {'i': inquiry_id}
        await s.execute(text(
            "delete from audit_logs where business_type='custom_inquiry' and business_id = :i"
        ), ii)
        await s.execute(text('delete from custom_inquiries where id = :i'), ii)
    for opportunity_id in ids.get('opportunities', []):
        oi = {'o': opportunity_id}
        await s.execute(text('delete from opportunity_stage_history where opportunity_id = :o'), oi)
        await s.execute(text('delete from opportunity_items where opportunity_id = :o'), oi)
        await s.execute(text('delete from followups where opportunity_id = :o'), oi)
        await s.execute(text('delete from tasks where opportunity_id = :o'), oi)
        await s.execute(text(
            "delete from notifications where business_type='opportunity' and business_id = :o"
        ), oi)
        await s.execute(text('delete from business_events where business_id = :o'), oi)
        await s.execute(text('delete from opportunities where id = :o'), oi)


async def cleanup(ids):
    from app.core.database import SessionLocal

    async with SessionLocal() as s:
        await _purge(s, ids)
        await s.commit()


async def cleanup_leftovers():
    """清掉上次跑到一半留下的 CHK 夹具（脚本中途失败也能自愈）。"""
    from sqlalchemy import text

    from app.core.database import SessionLocal

    prefix = 'CHK定制需求%'
    sample_prefix = 'CHK定制打样%'
    async with SessionLocal() as s:
        rows = (await s.execute(text(
            'select o.id as oid, i.id as iid '
            'from opportunities o '
            'left join custom_inquiries i on i.opportunity_id = o.id '
            'where o.title like :p'
        ), {'p': prefix})).all()
        quote_ids = (await s.execute(text(
            'select q.id from quotes q join opportunities o on o.id = q.opportunity_id '
            'where o.title like :p'
        ), {'p': prefix})).scalars().all()
        inquiry_ids = (await s.execute(text(
            'select id from custom_inquiries where title like :p'
        ), {'p': prefix})).scalars().all()
        sample_ids = (await s.execute(text(
            'select id from sample_requests where remark like :p'
        ), {'p': sample_prefix})).scalars().all()
        ids = {
            'quotes': [int(x) for x in quote_ids],
            'opportunities': [int(r.oid) for r in rows if r.oid],
            'inquiries': sorted({int(x) for x in inquiry_ids} | {int(r.iid) for r in rows if r.iid}),
            'samples': [int(x) for x in sample_ids],
        }
        await _purge(s, ids)
        # 回归里建的 auto_pass 规则（名字以 CHK定制免审 开头）必须一起清：
        # 漏一条"无限额自动过审"留在库里，就是给全站开了一个后门。
        await s.execute(text(
            "delete from approval_rule_versions where rule_id in "
            "(select id from approval_rules where name like 'CHK定制免审%')"
        ))
        await s.execute(text("delete from approval_rules where name like 'CHK定制免审%'"))
        await s.commit()


async def main():
    await cleanup_leftovers()
    token = login('admin', 'admin123')
    ids = {'quotes': [], 'samples': [], 'inquiries': [], 'opportunities': []}
    title = f'CHK定制需求-{STAMP}'

    print('=== 1. 需求有编号，不是自增 id 顶包 ===')
    status, payload = call('GET', '/customers?page_size=1', token=token)
    customer_id = payload['data']['items'][0]['id']
    status, payload = call('POST', '/custom-inquiries', token=token, body={
        'title': title,
        'description': '客户要一批异形包装，材质未定',
        'customer_id': customer_id,
        'quantity': 500,
        'target_price': 12.5,
    })
    check('建需求成功', status, 200)
    inquiry = payload['data']
    inquiry_id = inquiry['id']
    ids['inquiries'].append(inquiry_id)
    check_true('需求有编号', bool(inquiry.get('inquiry_no')), f"inquiry_no={inquiry.get('inquiry_no')}")
    check_true(
        '编号形如 XQ+日期+4位（共 14 位）',
        (inquiry.get('inquiry_no') or '').startswith('XQ')
        and len(inquiry.get('inquiry_no') or '') == 14,
        str(inquiry.get('inquiry_no')),
    )

    print()
    print('=== 2. 修订不换号（编号标识需求、version 标识修订）===')
    status, payload = call('POST', f'/custom-inquiries/{inquiry_id}/revise', token=token, body={
        'revision_note': '材质改成可降解',
    })
    check('修订成功', status, 200)
    revised = payload['data']
    ids['inquiries'].append(revised['id'])
    check('新一版沿用同一编号', revised.get('inquiry_no'), inquiry.get('inquiry_no'))
    check('版本号 +1', revised.get('version'), 2)
    check('新版没被标记为已取代', revised.get('is_superseded'), False)
    status, payload = call('GET', f'/custom-inquiries/{inquiry_id}', token=token)
    check('旧版被标记"已被新版取代"',
          (payload.get('data') or {}).get('is_superseded'), True)
    # 在旧版上再发起修订 → 必须被拒（此前不校验，在 v1 上连点两次会出两条 v2）
    status, payload = call('POST', f'/custom-inquiries/{inquiry_id}/revise', token=token,
                           body={'revision_note': '在旧版上重复修订'})
    check('在旧版上再修订被拒', status, 409)
    # 直接改历史版的内容 → 必须被拒（要改请走修订）
    status, payload = call('PATCH', f'/custom-inquiries/{inquiry_id}', token=token,
                           body={'title': '偷偷改历史版内容'})
    check('改历史版内容被拒', status, 409)

    print()
    print('=== 3. 报价明细可无 SKU（场景09 主路径）===')
    # 报价这一层仍要求关联商机（既有口径，不动）。链条是 需求 → 商机 → 报价 → 打样
    status, payload = call('POST', '/opportunities', token=token, body={
        'customer_id': customer_id, 'title': title,
    })
    check('从需求建商机成功', status, 200)
    opportunity_id = payload['data']['id']
    ids['opportunities'].append(opportunity_id)
    # 链级字段（挂商机）在旧版上仍可改——这是整条需求共享的，且现有流程就靠它
    # 把商机链到需求链条上；改一次对链条每一版生效。
    status, payload = call('PATCH', f'/custom-inquiries/{inquiry_id}', token=token,
                           body={'opportunity_id': opportunity_id})
    check('链级字段（挂商机）在旧版上仍可改', status, 200)
    status, payload = call('GET', f'/custom-inquiries/{revised["id"]}', token=token)
    check('链级字段对整条链条生效（新版也挂上了商机）',
          (payload.get('data') or {}).get('opportunity_id'), opportunity_id)

    status, payload = call('POST', '/quotes', token=token, body={
        'customer_id': customer_id, 'opportunity_id': opportunity_id,
    })
    check('建报价成功', status, 200)
    created = payload['data']
    quote_id, version_id = created['quote_id'], created['version_id']
    ids['quotes'].append(quote_id)

    status, payload = call('POST', f'/quote-versions/{version_id}/items', token=token, body={
        'inquiry_id': inquiry_id,
        'item_name': '异形包装（定制）',
        'quantity': 500,
        'unit_cost': 8.0,
        'quoted_price': 11.5,
    })
    check('定制明细（无 SKU）落库', status, 200)
    item = payload['data']
    check_true('标记为定制项', item.get('is_custom') is True, str(item.get('is_custom')))
    check('明细带回需求编号', item.get('inquiry_no'), inquiry['inquiry_no'])
    check_true('报价快照落全（成本 8 / 毛利 3.5）',
               item.get('cost_snapshot') == 8.0 and item.get('profit_snapshot') == 3.5,
               f"cost={item.get('cost_snapshot')} profit={item.get('profit_snapshot')}")
    check('最低保护价按最低毛利率推（成本 8×(1+0.15)=9.2）',
          item.get('minimum_price_snapshot'), 9.2)
    check_true('高于保护价不触发审批', item.get('approval_required') is False,
               f"报价 11.5 > 9.2：{item.get('approval_required')}")

    # ---- 3b. 改定制行（照真实用户操作：填完发现价格/数量要调，点进去改）----
    # 回归：改价要重算快照，而重算走的是"sku_id 为空 → 定制分支"。早先没把
    # inquiry_id 传下去，于是必然报"明细必须关联 SKU 或定制需求编号"——
    # 新做的定制报价只能一次填死、改不动。接口用例不碰这条，只有真人会碰。
    print()
    print('=== 3b. 改定制行（价格/数量）===')
    status, payload = call('PATCH', f"/quote-items/{item['id']}", token=token, body={
        'quantity': 800, 'quoted_price': 12.0,
    })
    check('改定制行成功（回归：以前必报错）', status, 200)
    if status == 200:
        row = payload['data']
        check('数量已改', row.get('quantity'), 800.0)
        check('价格已改', row.get('quoted_price'), 12.0)
        check('需求编号没丢', row.get('inquiry_no'), inquiry['inquiry_no'])
        check('成本快照沿用', row.get('cost_snapshot'), 8.0)
        check('最低保护价跟着重算', row.get('minimum_price_snapshot'), 9.2)

    # ---- 3c. 外币定制行：成本人民币、报价按币种（静默放行过的那个 bug）----
    # 回归：审批把报价乘汇率折人民币，却把成本当人民币直接用。定制项原先按
    # "报价币种"存成本，于是美元单上 7 美元成本被当成 7 人民币，与 50 人民币的
    # 报价一比永远不触发低价审批与绝对底价，页面毛利还虚高——**不报错的错最危险**。
    print()
    print('=== 3c. 外币定制行：成本按人民币、报价按币种 ===')
    status, payload = call('POST', '/quotes', token=token, body={
        'customer_id': customer_id, 'opportunity_id': opportunity_id,
        'currency': 'USD', 'exchange_rate': 7,
    })
    check('建美元报价', status, 200)
    fx_quote, fx_version = payload['data']['quote_id'], payload['data']['version_id']
    ids['quotes'].append(fx_quote)
    status, payload = call('POST', f'/quote-versions/{fx_version}/items', token=token, body={
        'inquiry_id': inquiry_id, 'quantity': 10, 'unit_cost': 300, 'quoted_price': 10,
    })
    check('美元单定制行落库', status, 200)
    fx_item = payload['data']
    check('最低保护价按人民币存 300×(1+0.15)=345',
          fx_item.get('minimum_price_snapshot'), 345.0)
    # 下面两条是**能区分新旧实现**的断言，别改成"利润为负"这种两边都成立的弱断言：
    #   旧实现 profit = 10 − 300 = −290（拿美元价直接减人民币成本）；
    #   新实现 profit = 10 − 300/7 = −32.857（先把成本折成报价币种）。
    # 只断言"为负"的话，改回去也照样绿——这正是这个 bug 一直没被发现的原因。
    profit = float(fx_item.get('profit_snapshot') or 0)
    check_true('利润按汇率折算（10 − 300/7 ≈ −32.86，而不是 −290）',
               abs(profit - (-32.8571)) < 0.01, f"profit={profit}")
    price_cny = 10 * 7
    check_true('10 美元=70 人民币 < 345 → 触发低价审批',
               fx_item.get('approval_required') is True and price_cny < 345,
               f"approval_required={fx_item.get('approval_required')}")

    print()
    print('=== 4. 需求状态随报价推进 ===')
    status, payload = call('GET', f'/custom-inquiries/{inquiry_id}', token=token)
    check('被报价引用后转「已转商机」', payload['data']['status'], 'converted')

    print()
    print('=== 5. 打样明细可无 SKU ===')
    status, payload = call('POST', '/samples', token=token, body={
        'customer_id': customer_id,
        'remark': f'CHK定制打样-{STAMP}',
        'items': [{'inquiry_id': inquiry_id, 'quantity': 2, 'remark': '看材质手感'}],
    })
    check('建定制打样成功', status, 200)
    sample = payload['data']
    ids['samples'].append(sample['id'])
    detail_status, detail = call('GET', f"/samples/{sample['id']}", token=token)
    sample_item = (detail.get('data', {}).get('items') or [{}])[0]
    check_true('打样明细标记定制项', sample_item.get('is_custom') is True, str(sample_item))
    check_true('打样明细带回需求编号与需求名',
               sample_item.get('inquiry_no') == inquiry['inquiry_no']
               and bool(sample_item.get('sku_name')),
               f"no={sample_item.get('inquiry_no')} name={sample_item.get('sku_name')}")

    print()
    print('=== 6. 负面路径要拒得明白 ===')
    status, payload = call('POST', f'/quote-versions/{version_id}/items', token=token, body={
        'quantity': 1, 'quoted_price': 10, 'unit_cost': 5,
    })
    check('既无 SKU 也无需求 → 422', status, 422)
    check_true('错误说明指出该给什么', '需求' in (payload.get('message') or ''),
               str(payload.get('message'))[:60])

    status, payload = call('POST', f'/quote-versions/{version_id}/items', token=token, body={
        'inquiry_id': inquiry_id, 'quantity': 1, 'quoted_price': 10,
    })
    check('定制项不给成本 → 422', status, 422)
    check_true('错误说明成本为什么必须有', '成本' in (payload.get('message') or ''),
               str(payload.get('message'))[:60])

    status, payload = call('POST', f'/quote-versions/{version_id}/items', token=token, body={
        'inquiry_id': inquiry_id, 'quantity': 1, 'unit_cost': 5,
    })
    check('定制项不给报价 → 422', status, 422)

    status, payload = call('POST', '/samples', token=token, body={
        'customer_id': customer_id, 'items': [{'quantity': 1}],
    })
    check('打样明细两者都不给 → 422', status, 422)

    print()
    print('=== 7. 需求一键转报价（§3.1：从需求页可发起报价）===')
    status, payload = call('POST', f'/custom-inquiries/{inquiry_id}/create-quote', token=token, body={
        'unit_cost': 8.0,
        'quoted_price': 11.5,
    })
    check('一键转报价成功', status, 200)
    direct = payload.get('data') or {}
    ids['quotes'].append(direct['quote_id'])
    check('带回报价单号与需求编号', direct.get('inquiry_no'), inquiry['inquiry_no'])
    check_true('同时回报是否触发审批（省得点进去才发现）',
               direct.get('approval_required') is False,
               str(direct.get('approval_required')))
    status, payload = call('GET', f"/quote-versions/{direct['version_id']}/items", token=token)
    direct_item = (payload.get('data') or [{}])[0]
    check_true('生成的明细就是定制项',
               direct_item.get('is_custom') is True
               and direct_item.get('inquiry_no') == inquiry['inquiry_no'],
               str(direct_item.get('inquiry_no')))

    # 需求没挂客户时不能报价（否则报价单没有客户，等于凭空造单据）
    status, payload = call('POST', '/custom-inquiries', token=token,
                           body={'title': f'{title}-无客户'})
    orphan_id = payload['data']['id']
    ids['inquiries'].append(orphan_id)
    status, payload = call('POST', f'/custom-inquiries/{orphan_id}/create-quote', token=token,
                           body={'unit_cost': 8, 'quoted_price': 11.5})
    check('需求没挂客户 → 422', status, 422)
    check_true('错误说明该先补什么', '客户' in (payload.get('message') or ''),
               str(payload.get('message'))[:60])

    # ---- 定制报价转订单（场景09 主路径的最后一环）----
    # 放在**最后**：这一步会把主版本提交审批并标记已发送，之后再改同一版本都会被拒
    # （回归跑第一版时就是这样把前面两条负面用例弄红的——它们复用同一个版本）。
    # 回归本体：sales_order_items.sku_id 原本 NOT NULL，而定制明细 sku_id 为 None，
    # 插入直接违反约束——询价/报价/打样三环都通了，唯独转订单必炸。
    print()
    print('=== 9. 定制报价转订单（主路径最后一环）===')
    status, payload = call('POST', '/approval-rules', token=token, body={
        'name': f'CHK定制免审-{STAMP}', 'kind': 'auto_pass', 'priority': 1,
        'conditions': [{'field': 'total_amount', 'op': 'lte', 'value': 999999999}],
        'action': {},
    })
    rule_id = payload['data']['id'] if payload.get('code') == 0 else None
    if rule_id:
        call('POST', f'/approval-rules/{rule_id}/publish', token=token, body={})
        call('PATCH', f'/approval-rules/{rule_id}/enabled', token=token,
             body={'enabled': True})
    call('POST', f'/quote-versions/{version_id}/submit-approval', token=token, body={})
    call('POST', f'/quote-versions/{version_id}/mark-sent', token=token, body={})
    status, payload = call('POST', f'/opportunities/{opportunity_id}/confirm-win',
                           token=token, body={})
    check('定制报价能转订单（以前必炸）', payload.get('code'), 0)
    order_id = (payload.get('data') or {}).get('order_id')
    if order_id:
        ids.setdefault('orders', []).append(order_id)
        # 明细在这个子端点上（订单详情只回表头；详情里没有 items 键，
        # 上一版从这里取才一直是 None——是取错地方，不是数据没写进去）
        status, payload = call('GET', f'/orders/{order_id}/items', token=token)
        line = (payload.get('data') or [{}])[0]
        check('订单行带回需求编号', line.get('inquiry_no_snapshot'), inquiry['inquiry_no'])
        check_true('订单行没有 SKU 也成立', line.get('sku_id') is None, str(line.get('sku_id')))
    if rule_id:
        call('DELETE', f'/approval-rules/{rule_id}', token=token)

    await cleanup(ids)
    print()
    print('（夹具已清理）')
    if FAILURES:
        print(f'FAILED {len(FAILURES)} 项：' + '、'.join(FAILURES))
        return 1
    print('定制无 SKU 通路回归 全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
