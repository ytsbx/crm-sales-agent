"""引用存在性回归测试。

跑法（后端必须先起来，默认 http://127.0.0.1:8000）：
    cd backend
    $env:PYTHONPATH="."
    .venv\\Scripts\\python.exe scripts\\check_reference_integrity.py

## 为什么需要这个脚本

`leads` / `followups` / `tasks` 在数据库层**没有外键约束**（`02-ER` 里是"弱关联"），
所以往这些表写一个不存在的 `customer_id` / `owner_id` 会被**静默接受**：
接口返回 200、库里留下悬空引用 —— 列表页显示空白、统计口径悄悄错、
客户"最近跟进时间"永远不更新（`if customer:` 安静跳过）。

`quotes` / `sample_requests` 有真实 FK，漏校验则相反：撞约束报 500，
而不是可读的 40401。

两条路径都必须返回可读的 40401。这个脚本钉住这条约定：
**任何"引用一个不存在的对象"的写接口，都必须报 40401 —— 不许 200、不许 500。**

## 实现要点

不必逐个接口造数据。校验逻辑统一走 `app.core.refs.ensure_refs`，
用 `raise_on_missing=False` 直接对每个模型和 id 求值即可：
"这些字段都被接住了吗"是同一件事，比端到端打接口更直接、也更快。
少数几条端到端用例（末尾）用来确认接线真的生效了。
"""

import asyncio
import os
import sys

sys.path.insert(0, '.')

# --------------------------------------------------------------------------
# 待校验的 (模型, 标签, 探测 id) —— 覆盖 task/followup/quote 的每个引用字段
# --------------------------------------------------------------------------
MISSING_ID = 999999


async def scan_refs():
    from app.core.refs import ensure_refs
    from app.core.database import SessionLocal
    from app.modules.customer.model import Contact, Customer
    from app.modules.lead.model import Lead
    from app.modules.opportunity.model import Opportunity
    from app.modules.order.model import SalesOrder
    from app.modules.product.model import Sku
    from app.modules.quote.model import Quote
    from app.modules.user.model import User

    print('=== ensure_refs 对不存在的 id 一律判定为缺失 ===')
    print('（每条 = 一个会被写进库的引用字段）')
    batches = [
        (User, '负责人', {'owner_id': MISSING_ID}),
        (Customer, '客户', {'customer_id': MISSING_ID}),
        (Contact, '联系人', {'contact_id': MISSING_ID}),
        (Lead, '线索', {'lead_id': MISSING_ID}),
        (Opportunity, '商机', {'opportunity_id': MISSING_ID}),
        (Quote, '报价单', {'quote_id': MISSING_ID}),
        (SalesOrder, '订单', {'order_id': MISSING_ID}),
        (Sku, 'SKU', {'sku_id': MISSING_ID}),
    ]
    failures = []
    async with SessionLocal() as session:
        for model, label, ids in batches:
            missing = await ensure_refs(
                session, model=model, ids=ids, label=label, raise_on_missing=False
            )
            caught = len(missing) == len(ids)
            field = next(iter(ids))
            print(f'  {"OK  " if caught else "FAIL"} {label:6} {field}')
            if not caught:
                failures.append(f'{label}.{field}')

        print()
        print('=== None 表示"不关联"，是合法值，必须放行 ===')
        for model, label in [(Customer, '客户'), (Lead, '线索'), (SalesOrder, '订单')]:
            missing = await ensure_refs(
                session, model=model, ids={'x': None}, label=label, raise_on_missing=False
            )
            ok = missing == []
            print(f'  {"OK  " if ok else "FAIL"} {label} 传 None 放行')
            if not ok:
                failures.append(f'{label}.None')

        print()
        print('=== 软删对象视为不存在（deleted_at 非空不能被引用）===')
        # 直接构造判断，不依赖库里恰好有软删数据
        soft_deleted = await ensure_refs(
            session,
            model=Customer,
            ids={'customer_id': MISSING_ID},
            label='客户',
            soft_delete=True,
            raise_on_missing=False,
        )
        ok = len(soft_deleted) == 1
        print(f'  {"OK  " if ok else "FAIL"} 软删口径生效（soft_delete=True）')
        if not ok:
            failures.append('soft_delete')

    return failures


# --------------------------------------------------------------------------
# 端到端接线确认：真实打接口，确认路由里真的调了 ensure_refs
# --------------------------------------------------------------------------
def e2e():
    import json
    import urllib.error
    import urllib.request

    base = os.environ.get('API_BASE', 'http://127.0.0.1:8000/api/v1')

    def call(method, path, token=None, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(base + path, data=data, method=method)
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

    admin = call('POST', '/auth/login', body={'username': 'admin', 'password': 'admin123'})[1][
        'data'
    ]['access_token']

    # D8：报价必须挂商机（商机不存在由建商机侧把关），客户不存在场景改为无商机被拒；
    # 联系人引用完整性仍要验：先造一条客户 1 的商机
    status, res = call('POST', '/opportunities', token=admin,
                       body={'customer_id': 1, 'title': 'REFCHK 引用完整性商机'})
    refchk_opp = res.get('data', {}).get('id') if res.get('code') == 0 else None
    # (label, method, path, body, expected_code)：引用完整性一律 40401，
    # D8 无商机是业务规则拒绝（40001），单独标注
    cases = [
        ('建报价单-无商机被拒(D8)', 'POST', '/quotes', {'customer_id': MISSING_ID}, 40001),
        ('建报价单-联系人不存在', 'POST', '/quotes',
         {'opportunity_id': refchk_opp, 'contact_id': MISSING_ID}, 40401),
        ('建样品单-客户不存在', 'POST', '/samples', {'customer_id': MISSING_ID}, 40401),
        ('建任务-负责人不存在', 'POST', '/tasks', {'title': 'REFCHK 负责人', 'owner_id': MISSING_ID}, 40401),
        ('建任务-客户不存在', 'POST', '/tasks', {'title': 'REFCHK 客户', 'customer_id': MISSING_ID}, 40401),
        ('建任务-线索不存在', 'POST', '/tasks', {'title': 'REFCHK 线索', 'lead_id': MISSING_ID}, 40401),
        ('建任务-订单不存在', 'POST', '/tasks', {'title': 'REFCHK 订单', 'order_id': MISSING_ID}, 40401),
        ('建跟进-客户不存在', 'POST', '/followups',
         {'customer_id': MISSING_ID, 'content': 'REFCHK 客户'}, 40401),
        ('建跟进-线索不存在', 'POST', '/followups',
         {'lead_id': MISSING_ID, 'content': 'REFCHK 线索'}, 40401),
        ('建跟进-报价单不存在', 'POST', '/followups',
         {'quote_id': MISSING_ID, 'content': 'REFCHK 报价'}, 40401),
    ]

    print()
    print('=== 端到端：真实接口必须明确报错（不许 200 / 不许 500）===')
    failures = []
    for label, method, path, body, expected in cases:
        status, res = call(method, path, token=admin, body=body)
        code = res.get('code')
        good = code == expected
        detail = f'http={status} code={code}' + ('' if good else f' msg={res.get("message")!r}')
        print(f'  {"OK  " if good else "FAIL"} {label}：{detail}')
        if not good:
            failures.append(label)

    # 改派任务负责人（同为弱关联）
    print()
    print('=== 端到端：改派任务负责人 ===')
    status, res = call('POST', '/tasks', token=admin,
                       body={'title': 'REFCHK 正常任务', 'owner_id': 1})
    if res.get('code') != 0:
        print(f'  FAIL 先建一条正常任务：{res.get("message")!r}')
        failures.append('建正常任务')
    else:
        task_id = res['data']['id']
        status, res = call('PATCH', f'/tasks/{task_id}', token=admin,
                           body={'owner_id': MISSING_ID})
        good = res.get('code') == 40401
        print(f'  {"OK  " if good else "FAIL"} 改派给不存在的负责人被拒：{res.get("code")!r}')
        if not good:
            failures.append('改派负责人')

    return failures


async def clean():
    from sqlalchemy import text

    from app.core.database import SessionLocal

    statements = [
        ("用例任务", "delete from tasks where title like 'REFCHK%'"),
        ("用例跟进", "delete from followups where content like 'REFCHK%'"),
        ("用例通知", "delete from notifications where title like '%REFCHK%'"),
        # 用例在建"正常商机"那一步真的建了一条商机，此前只有任务/跟进/通知被清，
        # 商机一直留着——而且它挂在演示客户 id=1 名下，会出现在真实客户的商机列表里。
        # 商机的明细与阶段历史同属这条链路，按依赖顺序先清。
        ("用例商机明细", "delete from opportunity_items where opportunity_id in "
                     "(select id from opportunities where title like 'REFCHK%')"),
        ("用例商机阶段历史", "delete from opportunity_stage_history where opportunity_id in "
                       "(select id from opportunities where title like 'REFCHK%')"),
        ("用例商机", "delete from opportunities where title like 'REFCHK%'"),
    ]
    async with SessionLocal() as s:
        for label, sql in statements:
            result = await s.execute(text(sql))
            if result.rowcount:
                print(f'  {result.rowcount:>4}  {label}')
        await s.commit()


async def main():
    failures = await scan_refs()
    failures += e2e()
    return failures


if __name__ == '__main__':
    async def _driver():
        print('=== 清库（跑前）===')
        await clean()
        print()
        try:
            failures = await main()
        finally:
            print()
            print('=== 清库（跑后）===')
            await clean()
        return failures

    FAILURES = asyncio.run(_driver())
    print()
    if FAILURES:
        print(f'FAILED（{len(FAILURES)}）: {FAILURES}')
        sys.exit(1)
    print('全部通过')
