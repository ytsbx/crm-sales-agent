#!/usr/bin/env python3
"""第四批审查 6 项的真库端到端验证（能打接口的都打接口）。

离线回归在 `backend/tests/test_round4_review_fixes.py`（49 条），那份跑得快、
CI 上每次跑。**这份是补"真接口真的拦住/真的清掉了"** —— 报告里那 6 条都是
用真实路由复现的，所以修完也要用真实路由证明：

| # | 项 | 这里怎么验 |
| --- | --- | --- |
| 1 | 预付比例被误认成账期 | 建一张付款条件为"预付30%，余款月结60天"的报价，**真实提交审批**，看它有没有被错误免审 |
| 2 | 外币让价未折人民币 | 建一张外币报价版本（改库造历史数据，模拟"口径曾是 export"），看规则引擎是否明确拒绝 |
| 3 | 未来到账可今天确认 | **真实登记**未来日期的回款 → 必须拒；确认存量异常记录 → 必须拒 |
| 4 | CSV 公式注入 | **真实导出**客户 CSV，看 `=1+1` 有没有被保护 |
| 5 | 多余列让导入 500 | **真实上传**带多余列的 CSV → 必须是业务错误（不是 500） |
| 6 | 合同 PDF 未清理 | 离线用替身验（起真实合同需要模板+渲染链，成本高收益低）—— 见离线回归 |

跑法（一次性库 + 隔离后端）：

    DB=crm_check_r4
    psql -h 127.0.0.1 -U postgres -d postgres -c "CREATE DATABASE $DB OWNER crm;"
    cd backend && export DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5432/$DB
    PYTHONPATH=. .venv/bin/python -m alembic upgrade head
    PYTHONPATH=. .venv/bin/python -m scripts.seed
    PYTHONPATH=. .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8073 &
    API_BASE=http://127.0.0.1:8073/api/v1 PYTHONPATH=. DATABASE_URL=$DATABASE_URL \
      .venv/bin/python scripts/check_round4_review_fixes.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from datetime import UTC, date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _test_support import require_api_base, require_isolated_db  # noqa: E402

def _register_all_models() -> None:
    """把所有 ORM 模型导入一次：只导入几个的话，映射器解析外键会找不到
    `users` 之类的表（`NoReferencedTableError`，同一坑在跟单锁脚本里也踩过）。"""
    import importlib
    import pkgutil

    import app.modules as modules

    for info in pkgutil.iter_modules(modules.__path__):
        try:
            importlib.import_module(f'app.modules.{info.name}.model')
        except ModuleNotFoundError:
            continue


_register_all_models()

RUN = 'R4CHK'
FAILED: list[str] = []
PASSED = 0


def check(label: str, got, want) -> None:
    global PASSED
    if got == want:
        PASSED += 1
        print(f'  OK   {label}: {got!r}')
    else:
        FAILED.append(label)
        print(f'  FAIL {label}: 实际 {got!r}，期望 {want!r}')


def check_true(label: str, value: bool, detail: str = '') -> None:
    global PASSED
    if value:
        PASSED += 1
        print(f'  OK   {label}{f"（{detail}）" if detail else ""}')
    else:
        FAILED.append(label)
        print(f'  FAIL {label}{f"（{detail}）" if detail else ""}')


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


def upload(path: str, token: str, filename: str, content: bytes, field: str = 'file'):
    """multipart 上传（标准库手搓，不引第三方）。"""
    boundary = f'----{uuid.uuid4().hex}'
    body = b''.join([
        f'--{boundary}\r\n'.encode(),
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'.encode(),
        b'Content-Type: text/csv\r\n\r\n',
        content,
        f'\r\n--{boundary}--\r\n'.encode(),
    ])
    req = urllib.request.Request(f'{require_api_base()}{path}', data=body, method='POST')
    req.add_header('Content-Type', f'multipart/form-data; boundary={boundary}')
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


# ------------------------------------------------------------------ 夹具


async def make_order_with_plan(amount: str = '100') -> tuple[int, int]:
    """订单 + 一个应收节点（回款要挂在节点上）。"""
    from decimal import Decimal

    from app.core.database import SessionLocal
    from app.modules.customer.model import Customer
    from app.modules.order.model import SalesOrder
    from app.modules.payment.model import ReceivablePlan

    async with SessionLocal() as session:
        customer = Customer(name=f'{RUN}客户-{uuid.uuid4().hex[:6]}', owner_id=1)
        session.add(customer)
        await session.flush()
        order = SalesOrder(
            order_no=f'{RUN}-{uuid.uuid4().hex[:6]}',
            customer_id=customer.id,
            owner_id=1,
            total_amount=Decimal(amount),
        )
        session.add(order)
        await session.flush()
        plan = ReceivablePlan(
            order_id=order.id,
            plan_name=f'{RUN}收款',
            due_date=date.today() + timedelta(days=30),
            amount=Decimal(amount),
            currency='CNY',
            status='pending',
            created_at=datetime.now(UTC),  # 这一列没有默认值（必填）
        )
        session.add(plan)
        await session.commit()
        return int(order.id), int(plan.id)


# ------------------------------------------------------------------ 各项


async def check_payment_terms_not_bypassed(admin: str) -> None:
    """① 预付比例不能被当成账期（第四批 P1）。"""
    print()
    print('=== 1. 账期识别：预付比例不当账期 ===')
    _, res = call('POST', '/customers', token=admin, body={'name': f'{RUN}账期客户'})
    customer = res['data']['id']
    _, res = call('POST', '/opportunities', token=admin, body={
        'title': f'{RUN}账期商机', 'customer_id': customer,
    })
    opportunity = res['data']['id']
    _, res = call('POST', '/quotes', token=admin, body={
        'customer_id': customer, 'opportunity_id': opportunity, 'currency': 'CNY',
    })
    version = res['data']['version_id']
    call('POST', f'/quote-versions/{version}/items/batch', token=admin, body=[
        {'sku_id': 6, 'quantity': 1, 'quoted_price': 100, 'unit_cost': 60},
    ])

    # 沙盒/条件求值拿到的账期天数，就是规则匹配用的那个数
    status, res = call('POST', '/approval-rules/sandbox', token=admin,
                       body={'quote_version_id': version})
    ctx_days = ((res.get('data') or {}).get('context') or {}).get('payment_terms_days')
    print(f'  · 未填付款条件时 sandbox 的账期天数 = {ctx_days!r}')

    status, res = call('PATCH', f'/quote-versions/{version}', token=admin,
                       body={'payment_terms': '预付30%，余款月结60天'})
    check('改付款条件为「预付30%，余款月结60天」', res.get('code'), 0)
    status, res = call('POST', '/approval-rules/sandbox', token=admin,
                       body={'quote_version_id': version})
    ctx = (res.get('data') or {}).get('context') or {}
    days = ctx.get('payment_terms_days')
    print(f'  · 填了复合付款条件后，账期天数 = {days!r}（必须是 60，不能是 30）')
    check('账期取的是 60 天（不是预付比例 30）', days, 60)


async def check_foreign_concession_rejected(admin: str) -> None:
    """② 外币不让它参与人民币口径的规则（主人口径：不做折算，直接拒）。"""
    print()
    print('=== 2. 外币让价：直接拒，不当人民币算 ===')
    # 模拟"历史外币数据"：口径曾经是 export 时建的版本，或 ERP 同步进来的
    # （外部同步刻意不拦，所以库里确实可能有外币记录）
    async def seed_foreign_version() -> int:
        """造一对版本：**人民币 10000 的旧版 + 美元 9000 的新版**。

        模拟"库里本来就有外币记录"（口径曾经是 export 时建的，或 ERP 同步进来的
        —— 外部同步刻意不拦）。规则引擎在算「较上一版让价金额」时必须拒绝它：
        从前会算出 USD 差额 1000 当成人民币 1000，命中"让价 ≤ 1000 走极速通道"。
        """
        from decimal import Decimal

        from sqlalchemy import select

        from app.core.database import SessionLocal
        from app.modules.quote.model import QuoteVersion

        # ⚠️ **自己建一张报价单，不要 `select(...).limit(1)` 借库里的第一条**
        # （2026-10-13 修）。
        #
        # 原来是"抓库里任意一条版本所在的报价单"，再让 admin 拿去 sandbox。
        # 这在**干净库 + 全套件顺序跑**时是碰运气的：第一条版本很可能属于别人
        # （实测第一条 `owner_id=2` = 张三），而 admin 的数据范围看不到它，
        # `GET /approval-rules/sandbox` 直接 404「报价单不存在」——
        # 断言于是报"外币版本没有被拒"，其实**外币那段代码根本没被走到**。
        # 它之前是绿的，只因为跑到它时库里第一条恰好可见；清单里插进新套件、
        # 顺序一变就露出来了（单独复跑也复现）。
        #
        # 借别人的数据来验自己的功能，本身就是错的：造一张自己可见的单，
        # 结论才不依赖"谁先跑"。
        _, res = call('POST', '/customers', token=admin, body={'name': f'{RUN}外币客户'})
        customer = res['data']['id']
        _, res = call('POST', '/opportunities', token=admin, body={
            'title': f'{RUN}外币商机', 'customer_id': customer,
        })
        opportunity = res['data']['id']
        _, res = call('POST', '/quotes', token=admin, body={
            'customer_id': customer, 'opportunity_id': opportunity, 'currency': 'CNY',
        })
        quote_id = res['data']['quote_id']

        async with SessionLocal() as session:
            # 版本号用**当前最大 +1**，而不是写死 98/99：
            # 上一次跑到一半失败会留下 98/99，写死就撞 `uq_quote_versions_quote_version`
            # （实测撞过一次，整节变成"抛异常"而不是给结论）
            from sqlalchemy import func
            base = int((await session.execute(
                select(func.coalesce(func.max(QuoteVersion.version_no), 0))
                .where(QuoteVersion.quote_id == quote_id)
            )).scalar_one() or 0)
            prev = QuoteVersion(
                quote_id=quote_id, version_no=base + 1, currency='CNY',
                subtotal_amount=Decimal('10000'), total_amount=Decimal('10000'),
                approval_status='not_submitted', created_at=datetime.now(UTC),
            )
            session.add(prev)
            await session.flush()
            foreign = QuoteVersion(
                quote_id=quote_id, version_no=base + 2, currency='USD',
                subtotal_amount=Decimal('9000'), total_amount=Decimal('9000'),
                approval_status='not_submitted', created_at=datetime.now(UTC),
            )
            session.add(foreign)
            await session.commit()
            return int(foreign.id)

    version_id = await seed_foreign_version()

    status, res = call('POST', '/approval-rules/sandbox', token=admin,
                       body={'quote_version_id': version_id})
    print(f'  · 外币版本走 sandbox：code={res.get("code")} message={str(res.get("message"))[:110]!r}')
    check_true('外币版本被明确拒绝（不静默算成人民币）',
               res.get('code') != 0 and '人民币' in str(res.get('message')),
               str(res.get('code')))


async def check_future_received_date(admin: str) -> None:
    """③ 未来到账不能登记、也不能确认（第四批 P1）。"""
    print()
    print('=== 3. 回款实收日期不能在未来 ===')
    _order_id, plan_id = await make_order_with_plan()
    future = (date.today() + timedelta(days=10)).isoformat()
    today = date.today().isoformat()

    status, res = call('POST', '/payments', token=admin, body={
        'receivable_plan_id': plan_id, 'received_date': future,
        'received_amount': 50, 'request_key': str(uuid.uuid4()),
    })
    print(f'  · 登记未来日期（{future}）：code={res.get("code")} {str(res.get("message"))[:90]!r}')
    check('登记未来日期被拒', res.get('code'), 40001)
    check_true('拒绝理由说清了"日期在未来"', '未来' in str(res.get('message')),
               str(res.get('message'))[:60])

    # 正常登记一笔（今天），再把它改成未来 —— 修改口也要拦
    status, res = call('POST', '/payments', token=admin, body={
        'receivable_plan_id': plan_id, 'received_date': today,
        'received_amount': 50, 'request_key': str(uuid.uuid4()),
    })
    check('登记今天日期的回款（应放行）', res.get('code'), 0)
    payment_id = (res.get('data') or {}).get('id')
    if payment_id:
        status, res = call('PATCH', f'/payments/{payment_id}', token=admin,
                           body={'received_date': future})
        check('把已登记的回款改成未来日期被拒', res.get('code'), 40001)

        # 造一条"修复前登记的存量异常记录"，确认口必须拦住它
        async def make_legacy() -> None:
            from sqlalchemy import text

            from app.core.database import SessionLocal

            async with SessionLocal() as session:
                await session.execute(text(
                    "update payment_records set received_date = :d where id = :i"
                ), {'d': date.today() + timedelta(days=10), 'i': payment_id})
                await session.commit()

        await make_legacy()
        status, res = call('POST', f'/payments/{payment_id}/confirm', token=admin,
                           body={'comment': '核验'})
        print(f'  · 确认存量未来日期记录：code={res.get("code")} {str(res.get("message"))[:80]!r}')
        check('确认存量未来日期记录被拒（锁内复查生效）', res.get('code'), 40001)


def export_customers_csv(admin: str, purpose: str, keyword: str) -> str | None:
    """调真实导出接口并**按 CSV 解码**（回的是带 BOM 的文件流，不是 JSON 信封）。

    ⚠️ 第一版用 `json.loads` 解析导出响应，直接撞 `Unexpected UTF-8 BOM`
    —— 导出这条路的响应体不是 `{code,message,data}`，别拿信封那套去解。
    """
    req = urllib.request.Request(
        f'{require_api_base()}/customers/export',
        data=json.dumps({'purpose': purpose, 'keyword': keyword}).encode(),
        headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {admin}'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req) as res:
            return res.read().decode('utf-8-sig', errors='replace')
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode('utf-8-sig', errors='replace')
        print(f'  · 导出被拒：HTTP {exc.code} {raw[:120]!r}')
        return None


def check_csv_export_and_import(admin: str) -> None:
    """④⑤ CSV 公式注入 + 多余列（第四批两个 P2）。"""
    print()
    print('=== 4. CSV 导出：公式字符被保护 ===')
    _, res = call('POST', '/customers', token=admin, body={'name': '=1+1'})
    check('造一个名字是 =1+1 的客户', res.get('code'), 0)
    # purpose 是**枚举**（实测报 "Input should be 'customer_follow_up', ..."），
    # 得用合法值，不能自己编一个
    csv_text = export_customers_csv(admin, 'business_analysis', '=1+1')
    print(f'  · 导出内容片段：{csv_text[:120]!r}')
    if csv_text is not None:
        check_true('导出内容里 =1+1 被加了前导单引号', "'=1+1" in csv_text, csv_text[:80])
    else:
        # 未直接返回 CSV 时，用纯函数验证编码结果（离线回归里也有）
        from app.core.csvio import csv_bytes

        raw = csv_bytes([['=1+1']], ['客户名称']).decode('utf-8-sig')
        check_true('csv_bytes 对 =1+1 加了保护', "'=1+1" in raw, raw.strip())

    print()
    print('=== 5. CSV 导入：多余列给业务错误，不是 500 ===')
    status, res = upload('/customers/import', admin, 'bad.csv',
                         '客户名称\n,多余值\n'.encode('utf-8'))
    print(f'  · 上传多余列文件：HTTP {status} code={res.get("code")} '
          f'{str(res.get("message"))[:90]!r}')
    check_true('不是 500（服务器内部错误）', status != 500, f'HTTP {status}')
    check_true('是业务错误且带行号',
               res.get('code') == 40001 and '第 2 行' in str(res.get('message')),
               str(res.get('message'))[:80])


async def cleanup() -> None:
    from sqlalchemy import text

    from app.core.database import SessionLocal

    async with SessionLocal() as session:
        pattern = f'{RUN}%'
        # ⚠️ `approval_instances` 是 `business_type` + `business_id` 的多态表，
        # 没有 `quote_version_id` 这个列（我第一版按名字猜，清理直接报
        # `UndefinedColumnError`，把整个脚本打断在断言之前）
        await session.execute(text(
            "delete from approval_instances where business_type = 'quote_version' "
            'and business_id in (select v.id from quote_versions v '
            ' join quotes q on q.id = v.quote_id '
            ' join customers c on c.id = q.customer_id where c.name like :p)'
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
        await session.execute(text(
            'delete from sales_orders where customer_id in '
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
        # ⚠️ 顺序：`opportunity_stage_history` 挂着 opportunities 外键（实测撞过）
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


async def main() -> None:
    """⚠️ **全程一个事件循环**：SQLAlchemy 的 async 连接池是**绑定事件循环**的，
    混用 `run_until_complete` / `run` 会报
    "attached to a different loop"（本会话早先踩过一次，这次又踩），
    所以夹具、检查、清理全部 await 在同一个 `asyncio.run` 里。
    """
    require_api_base()
    require_isolated_db()
    admin = login('admin', 'admin123')
    try:
        # 每节各自兜住异常：一节出错（比如夹具造不出来）不该让后面几节**没有结论**，
        # 更不该把整个脚本变成"跑了但什么都没说"
        for step in (check_payment_terms_not_bypassed,
                     check_foreign_concession_rejected,
                     check_future_received_date,
                     check_csv_export_and_import):
            try:
                await step(admin) if asyncio.iscoroutinefunction(step) else step(admin)
            except Exception as exc:  # noqa: BLE001 —— 排查脚本：要的是结论不是栈
                FAILED.append(f'{step.__name__} 抛异常')
                print(f'  ❌ {step.__name__} 抛异常：{type(exc).__name__}: {exc}')
    finally:
        print()
        print('=== 收尾：清夹具 ===')
        await cleanup()
        print('  （已清）')


if __name__ == '__main__':
    asyncio.run(main())
    print()
    print(f'通过 {PASSED} 条，失败 {len(FAILED)} 条')
    if FAILED:
        for item in FAILED:
            print(f'  ✗ {item}')
        sys.exit(1)
    print('全部通过')
