#!/usr/bin/env python3
"""横向摸查：合同 / 回款 / 物流报价 / 附件 —— "能不能挂到别家的记录上"。

主人 2026-10-12 的要求（原话）：
    「这四处只读地试一遍（建两个客户、互相挂一下，看接口拦不拦），
      出一张清单：哪几处能挂过去、后果是什么。」

⚠️ **本脚本只打接口、看返回、落库核对归属；不改任何生产代码**；跑完自己清夹具。

## 判据分两类（这一点很关键，摸查报价那轮就是在这里判错过的）

· **双输入型**（请求里同时给客户 + 另一个对象，例如"合同挂客户 A、又挂 B 的订单"）：
  这种**应该拒**；接受了并写成别家的，才是口子。
· **推导型**（客户只有一个来源，例如"回款只给应收单 id、客户由订单推出来"）：
  这种没有"两家不一致"的可能；真正要防的是**数据范围**——
  能不能借一个 id 把记录挂到/读到**别人的客户**上去。

跑法（一次性库 + 隔离后端）：
    DB=crm_check_xref4
    psql -h 127.0.0.1 -U postgres -d postgres -c "CREATE DATABASE $DB OWNER crm;"
    cd backend && export DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5432/$DB
    PYTHONPATH=. .venv/bin/python -m alembic upgrade head
    PYTHONPATH=. .venv/bin/python -m scripts.seed
    PYTHONPATH=. DATABASE_URL=$DATABASE_URL WECOM_PUSH_OFF=1 DINGTALK_PUSH_OFF=1 \
      SCHEDULER_ENABLED=false .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8067 &
    API_BASE=http://127.0.0.1:8067/api/v1 PYTHONPATH=. DATABASE_URL=$DATABASE_URL \\
      .venv/bin/python -u scripts/check_cross_entity_scope4.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.error
import urllib.request
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _test_support import require_api_base, require_isolated_db  # noqa: E402

RUN = os.environ.get('CHK_RUN', 'X4')
FINDINGS: list[tuple[str, str, str]] = []


def call(method: str, path: str, token: str | None = None, body=None):
    req = urllib.request.Request(
        f'{require_api_base()}{path}',
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
    )
    if body is not None:
        req.add_header('Content-Type', 'application/json')
    if token:
        req.add_header('Authorization', f'Bearer {token}')
    try:
        with urllib.request.urlopen(req) as res:
            return res.status, json.loads(res.read().decode())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {'code': exc.code, 'message': raw[:200]}


def login(username: str, password: str) -> str:
    _, res = call('POST', '/auth/login', body={'username': username, 'password': password})
    return res['data']['access_token']


def report(path: str, verdict: str, detail: str) -> None:
    icon = {'口子': '🔴', '拦住': '🔒', '正常': '✅', '不可达': '⏭'}.get(verdict, '•')
    FINDINGS.append((path, verdict, detail))
    print(f'  {icon} {verdict:4} {path}')
    print(f'        {detail}')


def build_order(admin: str, quote_version_id: int) -> dict:
    """把一版报价推成正式订单（合同/回款都要有订单才能试）。"""
    status, res = call('POST', f'/quote-versions/{quote_version_id}/items/batch', token=admin, body=[
        {'sku_id': SKU_ID, 'quantity': 2, 'quoted_price': 100, 'unit_cost': 60},
    ])
    if res.get('code') != 0:
        return {'error': f'录明细失败：{res.get("message")}'}
    # 顺序不能省：**标记已发送**（客户接受的前提）→ 客户接受 → 转订单
    status, res = call('POST', f'/quote-versions/{quote_version_id}/mark-sent', token=admin, body={})
    if res.get('code') != 0:
        return {'error': f'标记已发送失败：{res.get("message")}'}
    status, res = call('POST', f'/quote-versions/{quote_version_id}/accept', token=admin, body={})
    if res.get('code') != 0:
        return {'error': f'客户接受失败：{res.get("message")}'}
    status, res = call('POST', f'/quote-versions/{quote_version_id}/convert-to-order', token=admin, body={})
    if res.get('code') != 0:
        return {'error': f'转订单失败：{res.get("message")}'}
    return res.get('data') or {}


async def make_order(customer_id: int, no_prefix: str) -> int | None:
    """造一张订单夹具（返回订单 id）。"""
    from datetime import UTC, datetime

    from sqlalchemy import text

    from app.core.database import SessionLocal

    async with SessionLocal() as session:
        order_id = (await session.execute(text(
            'insert into sales_orders (order_no, customer_id, owner_id, total_amount,'
            ' status, currency, created_at, updated_at) values (:no, :cid, 1, 1000,'
            ' \'pending\', \'CNY\', :now, :now) returning id'
        ), {'no': f'{no_prefix}-{uuid.uuid4().hex[:6]}', 'cid': customer_id,
            'now': datetime.now(UTC)})).scalar_one()
        await session.commit()
        return int(order_id)


async def make_order_with_plan(customer_id: int, no_prefix: str) -> int | None:
    """直接造一张订单 + 一个应收节点当夹具（返回应收节点 id）。

    为什么不走"报价→发送→客户接受→转订单"整条链：那要过审批闸门、运费确认等
    一堆前置（实测在 `mark-sent` 就被"报价未通过审批"挡住），为了**试一条越权路径**
    去铺整条业务链得不偿失。这一段要验的是"业务员能不能借应收单 id 越权动数据"，
    夹具长什么样不影响结论。
    """
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import text

    from app.core.database import SessionLocal

    order_id = await make_order(customer_id, no_prefix)
    async with SessionLocal() as session:
        plan_id = (await session.execute(text(
            'insert into receivable_plans (order_id, plan_name, due_date, amount, status,'
            ' currency, created_at) values (:oid, :name, :due, 500, \'pending\', \'CNY\','
            ' :now) returning id'
        ), {'oid': order_id, 'name': f'{no_prefix}应收',
            'due': (datetime.now(UTC) + timedelta(days=30)).date(),
            'now': datetime.now(UTC)})).scalar_one()
        await session.commit()
        return int(plan_id)


async def main() -> None:
    global SKU_ID
    require_isolated_db()
    admin = login('admin', 'admin123')
    zhangsan = login('zhangsan', '123456')

    _, res = call('GET', '/skus?page=1&page_size=1', token=admin)
    SKU_ID = (res['data']['items'] or [{}])[0].get('id')

    print()
    print('=== 准备：客户 A / B，各自商机、联系人、报价 ===')
    ids: dict[str, dict] = {}
    for tag in ('A', 'B'):
        _, res = call('POST', '/customers', token=admin, body={'name': f'{RUN}客户{tag}'})
        customer = res['data']['id']
        _, res = call('POST', '/opportunities', token=admin, body={
            'title': f'{RUN}{tag}的商机', 'customer_id': customer,
        })
        opportunity = res['data']['id']
        _, res = call('POST', f'/customers/{customer}/contacts', token=admin, body={
            'name': f'{RUN}{tag}的联系人', 'mobile': '13800000000',
        })
        contact = res['data']['id']
        _, res = call('POST', '/quotes', token=admin, body={
            'customer_id': customer, 'opportunity_id': opportunity, 'currency': 'CNY',
        })
        quote_id = (res.get('data') or {}).get('quote_id')
        version_id = (res.get('data') or {}).get('version_id')
        ids[tag] = {'customer': customer, 'opportunity': opportunity, 'contact': contact,
                    'quote': quote_id, 'version': version_id}
        print(f'  {tag}: customer={customer} opportunity={opportunity} quote={quote_id} version={version_id}')

    print()
    print('=== 1. 合同：客户 A + B 的订单 / B 的报价（双输入型，应该拒）===')
    # A、B 各造一张订单（合同挂订单要真的存在）
    # ⚠️ 这里**直接造订单夹具**，不走"报价→发送→接受→转订单"那条链：
    # 那条链要过审批闸门（实测在 mark-sent 就被"报价未通过审批"挡住），
    # 而铺整条链只为试一个挂载点不划算。**更要紧的是**：如果订单没造出来、
    # `order_id` 变成 null，探针会把"根本没传订单"误判成"挂上了别家的订单"——
    # 我第一版就是这么假报了一个口子。所以下面必须确认订单 id 真的拿到了。
    orders = {}
    for tag in ('A', 'B'):
        orders[tag] = await make_order(ids[tag]['customer'], f'{RUN}{tag}单')
        print(f'  {tag} 的订单夹具：{orders[tag]}')
    assert all(orders.values()), f'订单夹具没造出来，后面的合同用例会失真：{orders}'
    template_id = None
    _, res = call('GET', '/contract-templates?page=1&page_size=5', token=admin)
    # ⚠️ 这个接口回的是**裸数组**，不是 `{items:[...]}`（实测踩过：按分页形状解析会
    # 抛 'list' object has no attribute 'get'）
    data = res.get('data')
    items = data if isinstance(data, list) else ((data or {}).get('items') or [])
    if items:
        template_id = items[0].get('id')
    if not template_id:
        # 种子不带模板，自己建一个（夹具；名字带 RUN 前缀，收尾会清）
        _, res = call('POST', '/contract-templates', token=admin, body={
            'doc_type': 'contract', 'name': f'{RUN}摸查模板',
            'body': '甲方：{{customer_name}}\n金额：{{total_amount}}',
        })
        template_id = (res.get('data') or {}).get('id')
        print(f'  （自建模板：code={res.get("code")} id={template_id}）')
    print(f'  合同模板 id={template_id}')

    if template_id:
        status, res = call('POST', '/contract-documents', token=admin, body={
            'template_id': template_id, 'customer_id': ids['A']['customer'],
            'order_id': orders.get('B'), 'request_key': str(uuid.uuid4()),
        })
        if res.get('code') == 0:
            report('合同：客户 A + B 的订单', '口子',
                   f'接受了！contract_id={(res.get("data") or {}).get("id")}')
        else:
            report('合同：客户 A + B 的订单', '拦住',
                   f'code={res.get("code")} {str(res.get("message"))[:80]!r}')

        status, res = call('POST', '/contract-documents', token=admin, body={
            'template_id': template_id, 'customer_id': ids['A']['customer'],
            'quote_id': ids['B']['quote'], 'request_key': str(uuid.uuid4()),
        })
        if res.get('code') == 0:
            report('合同：客户 A + B 的报价', '口子',
                   f'接受了！contract_id={(res.get("data") or {}).get("id")}')
        else:
            report('合同：客户 A + B 的报价', '拦住',
                   f'code={res.get("code")} {str(res.get("message"))[:80]!r}')

        # 正常路径不能被误伤
        status, res = call('POST', '/contract-documents', token=admin, body={
            'template_id': template_id, 'customer_id': ids['A']['customer'],
            'order_id': orders.get('A'), 'request_key': str(uuid.uuid4()),
        })
        report('合同：客户 A + A 自己的订单（应放行）', '正常' if res.get('code') == 0 else '拦住',
               f'code={res.get("code")} {str(res.get("message"))[:60]!r}')
    else:
        report('合同：跨客户挂订单/报价', '不可达', '库里没有合同模板，这一段跳过（不是没测，是没有夹具）')

    print()
    print('=== 2. 回款（推导型：只给应收单 id，客户由订单推出）===')
    print('     真正要试的是**数据范围**：业务员能不能借一个 id 去动别人的应收单')
    plan_id = await make_order_with_plan(ids['A']['customer'], f'{RUN}A单')
    if plan_id:
        print(f'  A 的订单上造好了应收节点：{plan_id}（夹具）')
        if plan_id:
            # 张三越权登记回款（A 的客户负责人是管理员）
            status, res = call('POST', '/payments', token=zhangsan, body={
                'receivable_plan_id': plan_id, 'received_date': '2026-10-12',
                'received_amount': 10, 'request_key': str(uuid.uuid4()),
            })
            if res.get('code') == 0:
                report('回款：业务员给别人的应收单登记回款', '口子',
                       f'接受了！payment_id={(res.get("data") or {}).get("id")}')
            else:
                report('回款：业务员给别人的应收单登记回款', '拦住',
                       f'code={res.get("code")} {str(res.get("message"))[:80]!r}')
            # 管理员给自家节点登记（正常路径）
            status, res = call('POST', '/payments', token=admin, body={
                'receivable_plan_id': plan_id, 'received_date': '2026-10-12',
                'received_amount': 10, 'request_key': str(uuid.uuid4()),
            })
            report('回款：给自家应收单登记（应放行）', '正常' if res.get('code') == 0 else '拦住',
                   f'code={res.get("code")} {str(res.get("message"))[:60]!r}')
    else:
        report('回款：业务员动别人的应收单', '不可达', '订单/应收夹具没造成，这一段跳过')

    print()
    print('=== 3. 物流报价：客户 A + B 的商机（双输入型，应该拒）===')
    # 真实端点：POST /logistics/calculate，save=true 才落库（不是 /logistics/quotes）
    status, res = call('POST', '/logistics/calculate', token=admin, body={
        'sku_id': SKU_ID, 'quantity': 10, 'origin': '宁波', 'destination': '上海',
        'shipping_method': '陆运', 'customer_id': ids['A']['customer'],
        'opportunity_id': ids['B']['opportunity'], 'save': True,
    })
    if res.get('code') == 0:
        data = res.get('data') or {}
        got_c, got_o = data.get('customer_id'), data.get('opportunity_id')
        bad = got_c == ids['A']['customer'] and got_o == ids['B']['opportunity']
        report('物流报价：客户 A + B 的商机', '口子' if bad else '正常',
               f'接受了：落库 customer={got_c} opportunity={got_o}')
    else:
        report('物流报价：客户 A + B 的商机', '拦住',
               f'code={res.get("code")} {str(res.get("message"))[:80]!r}')

    print()
    print('=== 4. 附件：把文件挂到别人家的业务对象上（双输入型，应该拒）===')
    # 找一个"业务员看不到"的对象：用张三去挂管理员的报价附件
    # 真实端点：POST /business/{type}/{id}/files?file_id=…（file_id 是 query 参数）
    status, res = call('POST',
                       f"/business/quote/{ids['A']['quote']}/files?file_id=1",
                       token=zhangsan)
    if res.get('code') == 0:
        report('附件：业务员把文件挂到别人的报价上', '口子',
               f'接受了：{json.dumps(res.get("data"), ensure_ascii=False)[:80]}')
    else:
        report('附件：业务员把文件挂到别人的报价上', '拦住',
               f'code={res.get("code")} {str(res.get("message"))[:80]!r}')

    print()
    print('=== 5. 附件：业务员读别人报价上的附件列表（读路径）===')
    status, res = call('GET', f"/business/quote/{ids['A']['quote']}/files", token=zhangsan)
    if res.get('code') == 0:
        rows = res.get('data')
        n = len(rows) if isinstance(rows, list) else len((rows or {}).get('items') or [])
        report('附件：读别人报价上的附件列表', '口子' if n else '拦住',
               f'code=0，返回 {n} 条（0 条=没漏内容）')
    else:
        report('附件：读别人报价上的附件列表', '拦住',
               f'code={res.get("code")} {str(res.get("message"))[:80]!r}')


async def cleanup(verbose: bool = False) -> None:
    from sqlalchemy import text

    from app.core.database import SessionLocal

    pattern = f'{RUN}%'
    async with SessionLocal() as session:
        await session.execute(text(
            'delete from logistics_quotes where customer_id in '
            '(select c.id from customers c where c.name like :p)'
        ), {'p': pattern})
        await session.execute(text(
            'delete from payment_records where order_id in '
            '(select o.id from sales_orders o join customers c on c.id = o.customer_id '
            ' where c.name like :p)'
        ), {'p': pattern})
        await session.execute(text(
            'delete from receivable_plans where order_id in '
            '(select o.id from sales_orders o join customers c on c.id = o.customer_id '
            ' where c.name like :p)'
        ), {'p': pattern})
        await session.execute(text(
            'delete from order_milestones where order_id in '
            '(select o.id from sales_orders o join customers c on c.id = o.customer_id '
            ' where c.name like :p)'
        ), {'p': pattern})
        # ⚠️ 顺序：**合同→订单**。合同上挂着 order_id 外键，先删订单会撞约束
        # （实测报 contract_documents_order_id_fkey）——
        # 这是**夹具清理**的顺序问题，不是产品缺陷，别记错账
        await session.execute(text(
            'delete from contract_documents where order_id in '
            '(select o.id from sales_orders o join customers c on c.id = o.customer_id '
            ' where c.name like :p)'
        ), {'p': pattern})
        await session.execute(text(
            "delete from contract_templates where name like :p"
        ), {'p': pattern})
        await session.execute(text(
            'delete from sales_order_items where order_id in '
            '(select o.id from sales_orders o join customers c on c.id = o.customer_id '
            ' where c.name like :p)'
        ), {'p': pattern})
        await session.execute(text(
            'delete from sales_orders where customer_id in '
            '(select c.id from customers c where c.name like :p)'
        ), {'p': pattern})
        await session.execute(text(
            'delete from contract_documents where customer_id in '
            '(select c.id from customers c where c.name like :p)'
        ), {'p': pattern})
        quote_ids = (await session.execute(text(
            'select q.id from quotes q join customers c on c.id = q.customer_id '
            'where c.name like :p'
        ), {'p': pattern})).scalars().all()
        for quote_id in quote_ids:
            q = {'q': int(quote_id)}
            for table in ('quote_items', 'quote_charges', 'quote_send_logs'):
                await session.execute(text(
                    f'delete from {table} where quote_version_id in '
                    '(select id from quote_versions where quote_id = :q)'
                ), q)
            await session.execute(text('delete from quote_versions where quote_id = :q'), q)
            await session.execute(text('delete from quotes where id = :q'), q)
        await session.execute(text(
            'delete from opportunity_stage_history where opportunity_id in '
            '(select o.id from opportunities o join customers c on c.id = o.customer_id '
            ' where c.name like :p)'
        ), {'p': pattern})
        await session.execute(text(
            'delete from opportunity_items where opportunity_id in '
            '(select o.id from opportunities o join customers c on c.id = o.customer_id '
            ' where c.name like :p)'
        ), {'p': pattern})
        await session.execute(text(
            'delete from opportunities where customer_id in '
            '(select c.id from customers c where c.name like :p)'
        ), {'p': pattern})
        await session.execute(text(
            'delete from contacts where customer_id in '
            '(select c.id from customers c where c.name like :p)'
        ), {'p': pattern})
        await session.execute(text('delete from customers where name like :p'), {'p': pattern})
        await session.commit()
        if verbose:
            print(f'  （清掉 {len(quote_ids)} 张报价及其客户链）')


async def _driver() -> None:
    print()
    print('=== 0. 先清上次残留 ===')
    await cleanup(verbose=True)
    try:
        await main()
    except Exception as exc:  # noqa: BLE001 —— 摸查脚本：单点失败不该让整轮没有结论
        print(f'  ⚠️ 中途出错：{type(exc).__name__}: {exc}')
    finally:
        print()
        print('=== 收尾：清掉本脚本造的夹具 ===')
        await cleanup(verbose=True)
    print()
    print('=== 清单汇总（哪几处能挂过去 / 后果）===')
    for path, verdict, detail in FINDINGS:
        print(f'  {verdict:4}  {path}')
        print(f'        {detail}')


if __name__ == '__main__':
    asyncio.run(_driver())
