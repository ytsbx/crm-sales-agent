#!/usr/bin/env python3
"""跟单页 GET 与财务改应收的**真并发**交错（第三批审查遗留 P1，2026-10-12 复审）。

守的问题
--------
`GET /orders/{id}/milestones` 会"建空节点 → 按财务事实同步 → commit"，
但取订单时**没有加锁**（`get_visible_order` 默认 `for_update=False`）。
财务改应收金额那条（`PATCH /receivables/{id}`）虽然按"订单 → 节点"取了锁，
**挡不住 GET 这边**：GET 不取锁，也就不参与排队。

交错（两会话、真 PostgreSQL）：

    T1 页面 GET        : 读订单(不加锁) → 建节点 → 读汇总 = 应收100/已收100
    T2 财务改应收      : 取订单锁 → 应收改 200 → 节点转 partial → 提交
    T1 页面 GET        : 手里的旧汇总判定"已收齐" → 把「收款」节点写成完成 → 提交

    终态：应收 200、已收 100，**收款节点却是完成** —— 一份订单两个说法，
    而且这个说法是"钱收齐了"（比漏标严重：会让人不再催款）。

## 为什么必须真库

`FOR UPDATE` 在 SQLite 上被直接忽略，锁的互斥只有真 PostgreSQL 能证明。
等待/不等待用 `FOR UPDATE NOWAIT` 的成败判定 —— **确定性，不靠 sleep 碰运气**
（与 `check_sku_parent_lock.py` 同一手法）。

## 三个结论（脚本自己判定，不靠人看日志）

  ① **基座**：GET 不持锁时，另一个事务能立刻 `NOWAIT` 拿到订单行 → 证明"没锁"；
     持锁时拿到 `55P03 / lock_not_available` → 证明"锁住了"；
  ② **交错**：把上面那段交错跑一遍，看终态是不是"应收200/已收100/收款节点完成"；
  ③ **修好之后**：GET 持锁 → 财务的 `NOWAIT` 必须失败；GET 提交后财务再改 → 终态一致。

跑法（一次性库 + 隔离后端）：

    DB=crm_check_ms_lock
    psql -h 127.0.0.1 -U postgres -d postgres -c "CREATE DATABASE $DB OWNER crm;"
    cd backend && export DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5432/$DB
    PYTHONPATH=. .venv/bin/python -m alembic upgrade head
    PYTHONPATH=. .venv/bin/python -m scripts.seed
    PYTHONPATH=. .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8071 &
    API_BASE=http://127.0.0.1:8071/api/v1 PYTHONPATH=. DATABASE_URL=$DATABASE_URL \\
      .venv/bin/python scripts/check_milestone_lock_race.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _test_support import require_api_base, require_isolated_db  # noqa: E402

#: 把**所有** ORM 模型导入一次再建对象：只导入其中几个的话，映射器解析外键时
#: 会找不到 `users` 之类的表（实测报 `NoReferencedTableError: customers.owner_id
#: could not find table 'users'`）。照抄 `alembic/env.py` 的做法。
def _register_all_models() -> None:
    import importlib
    import pkgutil

    import app.modules as modules

    for info in pkgutil.iter_modules(modules.__path__):
        try:
            importlib.import_module(f'app.modules.{info.name}.model')
        except ModuleNotFoundError:
            continue  # 没有 model.py 的模块（路由/工具包）跳过


_register_all_models()

RUN = 'MSE' 
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


# --------------------------------------------------------------------- 夹具


async def make_fixture() -> dict:
    """造一张订单：应收 100 + 一笔**已确认**回款 100（即"看起来已收齐"）。

    ⚠️ 用 ORM 模型造，不手写 INSERT：这几张表的非空列很多（`status` / `pool_status`
    / `currency` / `updated_at`…），手写 INSERT 会一个个撞过去（我撞了两轮），
    而模型的默认值本来就定义好了这些列该怎么填。
    """
    from app.core.database import SessionLocal
    from app.modules.customer.model import Customer
    from app.modules.order.model import SalesOrder
    from app.modules.payment.model import PaymentRecord, ReceivablePlan

    async with SessionLocal() as session:
        customer = Customer(name=f'{RUN}客户-{uuid.uuid4().hex[:6]}', owner_id=1)
        session.add(customer)
        await session.flush()
        order = SalesOrder(
            order_no=f'{RUN}-{uuid.uuid4().hex[:6]}',
            customer_id=customer.id,
            owner_id=1,
            total_amount=Decimal('100'),
        )
        session.add(order)
        await session.flush()
        # `created_at` 在这两张表上**没有默认值**（必填），得显式给
        now = datetime.now(UTC)
        plan = ReceivablePlan(
            order_id=order.id,
            plan_name=f'{RUN}收款',
            due_date=date.today() + timedelta(days=30),
            amount=Decimal('100'),
            currency='CNY',
            created_at=now,
        )
        session.add(plan)
        await session.flush()
        session.add(PaymentRecord(
            order_id=order.id,
            receivable_plan_id=plan.id,
            received_amount=Decimal('100'),
            received_date=date.today(),
            status='confirmed',
            currency='CNY',
            created_at=now,
        ))
        await session.commit()
        return {'order_id': int(order.id), 'plan_id': int(plan.id), 'customer_id': int(customer.id)}


async def read_state(order_id: int) -> dict:
    """库里的真实终态：应收合计、已确认已收合计、收款节点是否完成。"""
    from sqlalchemy import text

    from app.core.database import SessionLocal

    async with SessionLocal() as session:
        planned, received = (await session.execute(text(
            "select coalesce((select sum(amount) from receivable_plans where order_id = :o), 0), "
            "       coalesce((select sum(received_amount) from payment_records "
            "                 where order_id = :o and status = 'confirmed'), 0)"
        ), {'o': order_id})).one()
        actual, remark = (await session.execute(text(
            "select actual_date, remark from order_milestones "
            " where order_id = :o and node = 'payment'"
        ), {'o': order_id})).one_or_none() or (None, None)
        plan_status = (await session.execute(text(
            "select status from receivable_plans where order_id = :o order by id limit 1"
        ), {'o': order_id})).scalar_one()
        return {'planned': str(planned), 'received': str(received),
                'payment_actual_date': actual, 'plan_status': plan_status}


# ------------------------------------------------- 页面侧：GET 那段逻辑


async def page_get_once(order_id: int, *, with_lock: bool, after_sync=None) -> None:
    """走一遍 `list_milestones` 的逻辑。

    `with_lock` 就是这次变更的那一行：`get_visible_order(..., for_update=True)`。
    `after_sync` 用来在"汇总已算完、还没提交"的窗口里插事件（复现交错用）。
    """
    from app.core.database import SessionLocal
    from app.modules.order import milestones as milestones_svc
    from app.modules.order import service as order_svc

    class _User:
        id = 1
        role_codes = ['admin']
        data_scope = 'all'
        permission_codes: list[str] = []

    async with SessionLocal() as session:
        order = await order_svc.get_visible_order(
            session, _User(), order_id, for_update=with_lock
        )
        await milestones_svc.ensure_initialized(
            session, order.id, order.delivery_date, created_by=1
        )
        await milestones_svc.sync_payment_milestones(session, order.id, operator_id=1)
        if after_sync is not None:
            await after_sync()
        await session.commit()


async def finance_change_amount(plan_id: int, amount: str, *, retry_ms: int = 900) -> str:
    """财务改应收金额（`get_visible_plan(for_update=True)` 那段）。

    **完整走上游那三件事**：取订单锁 → 改金额 → 重算节点 → **同步跟单节点**
    （`sync_payment_milestones`，与 `payment/router.py:376` 同一调用）。

    返回 `'locked'` = 订单行被别人持锁、`NOWAIT` 立刻失败（不 sleep 碰运气）；
    `'ok'` = 改成功。
    """
    import asyncio as _asyncio

    from sqlalchemy import text

    from app.core.database import SessionLocal
    from app.modules.order import milestones as milestones_svc

    async with SessionLocal() as session:
        try:
            await session.execute(text(
                'select id from sales_orders where id = '
                '(select order_id from receivable_plans where id = :p) for update nowait'
            ), {'p': plan_id})
        except Exception as exc:  # noqa: BLE001 —— 只要"拿不到锁"这一种，其余照抛
            await session.rollback()
            # asyncpg 抛 `LockNotAvailableError`（SQLSTATE 55P03）；SQLAlchemy 会包装成
            # 不同层级，所以按类型名/错误码判定，别只 catch 某一层（我漏过一次）
            text_of = f'{type(exc).__name__} {exc}'.lower()
            if ('locknotavailable' in text_of or 'lock_not_available' in text_of
                    or '55p03' in text_of or 'could not obtain lock' in text_of):
                return 'locked'
            raise
        # 拿到锁了 —— 之后这段必须在**同一事务**里做完
        plan = (await session.execute(text(
            'select id, order_id from receivable_plans where id = :p'
        ), {'p': plan_id})).one()
        await session.execute(text(
            'update receivable_plans set amount = :a where id = :p'
        ), {'a': Decimal(amount), 'p': plan_id})
        await session.flush() if False else None
        # 等页面那边提交（锁在它手里时我们进不到这里；这里只是让重试者排队）
        await _asyncio.sleep(0)
        await milestones_svc.sync_payment_milestones(
            session, int(plan.order_id), operator_id=1
        )
        await session.commit()
        return 'ok'


# --------------------------------------------------------------------- 主流程


async def main() -> None:
    require_api_base()
    require_isolated_db()

    # ---------------------------------------------------------------- ① 基座
    print()
    print('=== 1. 基座：GET 取不取订单锁？（用 NOWAIT 判定，不靠 sleep）===')
    fx = await make_fixture()
    order_id, plan_id = fx['order_id'], fx['plan_id']
    print(f'  夹具：订单 {order_id}、应收节点 {plan_id}（应收 100、已确认已收 100）')

    # 不加锁跑一遍 GET：它必须能跟"别人持锁"并存 —— 这正是问题的一半
    print('  · 先用**不加锁**的方式跑一遍 GET（读汇总 → 写完成 → 提交）')
    await page_get_once(order_id, with_lock=False)
    plain = await read_state(order_id)
    print(f'    跑完：应收 {plain["planned"]}、已收 {plain["received"]}、'
          f'收款节点 actual_date={plain["payment_actual_date"]}')
    check_true('不加锁时，GET 自己就能把「收款」节点写成完成（100/100 场景）',
               plain['payment_actual_date'] is not None,
               f'actual_date={plain["payment_actual_date"]}')

    # ---------------------------------------------------------------- ② 交错
    print()
    print('=== 2. 复现交错：GET 读旧汇总 → 财务改 200 → GET 写"收款完成" ===')
    await reset_to_settled(order_id, plan_id)

    page_read_done = asyncio.Event()
    finance_done = asyncio.Event()

    async def page_side() -> None:
        """GET：建节点 → 读汇总（此刻 100/100）→ **等财务改完** → 写完成 → 提交。

        等待点插在"汇总已算完、尚未提交"之间 —— 这就是真实交错里的那个窗口。
        """

        async def wait_for_finance() -> None:
            page_read_done.set()
            await finance_done.wait()

        await page_get_once(order_id, with_lock=False, after_sync=wait_for_finance)

    async def finance_side() -> None:
        await page_read_done.wait()
        result = await finance_change_amount(plan_id, '200')
        check('财务改应收时**没有被 GET 挡住**（证明 GET 没持锁）', result, 'ok')
        finance_done.set()

    await asyncio.gather(page_side(), finance_side())
    raced = await read_state(order_id)
    print(f'  终态：应收 {raced["planned"]}、已确认已收 {raced["received"]}、'
          f'收款节点 actual_date={raced["payment_actual_date"]}、'
          f'应收节点状态={raced["plan_status"]}')
    check_true('【复现】应收 200 / 已收 100，而收款节点仍是完成',
               raced['planned'] == '200.00' and raced['received'] == '100.00'
               and raced['payment_actual_date'] is not None,
               f'应收{raced["planned"]} 已收{raced["received"]} '
               f'actual_date={raced["payment_actual_date"]}')

    # ------------------------------------------------------------- ③ 修好之后
    print()
    print('=== 3. 修好之后（GET 取订单锁）：财务排队 → 页面提交 → 财务改成并撤销完成标识 ===')
    await reset_to_settled(order_id, plan_id)

    locked_seen: list[str] = []
    page_done = asyncio.Event()

    async def page_side_locked() -> None:
        async def probe_finance() -> None:
            # 此刻 GET 正持订单锁：财务的 NOWAIT 必须失败
            locked_seen.append(await finance_change_amount(plan_id, '200'))
            page_done.set()

        await page_get_once(order_id, with_lock=True, after_sync=probe_finance)

    async def finance_after() -> None:
        await page_done.wait()
        # GET 提交、锁释放之后：财务这一改必须成功
        for _ in range(20):
            result = await finance_change_amount(plan_id, '200')
            locked_seen.append(result)
            if result == 'ok':
                return
            await asyncio.sleep(0.05)
        raise AssertionError('财务一直拿不到锁（GET 没释放？）')

    await asyncio.gather(page_side_locked(), finance_after())
    check('GET 持锁期间财务改不动（NOWAIT 失败）', locked_seen[0], 'locked')
    check('GET 提交后财务改得动', locked_seen[-1], 'ok')

    fixed = await read_state(order_id)
    print(f'  终态：应收 {fixed["planned"]}、已确认已收 {fixed["received"]}、'
          f'收款节点 actual_date={fixed["payment_actual_date"]}、'
          f'应收节点状态={fixed["plan_status"]}')
    check_true('【修好】财务改成 200 之后，收款节点的完成标识被撤销（终态一致）',
               fixed['planned'] == '200.00' and fixed['received'] == '100.00'
               and fixed['payment_actual_date'] is None,
               f'actual_date={fixed["payment_actual_date"]}')

    # ------------------------------------------------- ④ 真实端点（HTTP 黑盒）
    print()
    print('=== 4. 真实端点：有人持着订单锁时，GET 必须排队等着 ===')
    await reset_to_settled(order_id, plan_id)

    # 手法（这一节被我写坏过三次，把弯路记下来）：
    #   ✗ 给端点打补丁插停顿 —— 端点跑在 **uvicorn 独立进程**里，脚本进程的补丁
    #     对它无效，于是脚本等一个永远不会发生的事件（卡死，库里也没有活动查询）；
    #   ✗ 轮询 `pg_stat_activity` 掐窗口 —— 端点只要 5 毫秒就跑完，抓不到；
    #   ✗ 轮询里每轮**新开会话** —— 持锁会话 + 卡住的 GET + 探测器把连接池占满，
    #     探测器自己再也开不出连接（也是卡死）。
    # ✅ 确定性且不占额外连接的做法：**用耗时证明**。
    #     持锁 → 发真实 GET → 不查库、只 `asyncio.sleep` → 放锁 → 量 GET 耗时。
    #     端点取了锁就必然等到放锁（秒级）；没取锁则 5 毫秒返回（毫秒级）。
    from sqlalchemy import text

    from app.core.database import SessionLocal

    holder_ready = asyncio.Event()
    get_result: list = []
    SLEEP_S = 2.0

    async def holder() -> None:
        async with SessionLocal() as session:
            await session.execute(text(
                'select id from sales_orders where id = :o for update'
            ), {'o': order_id})  # 持住订单行
            holder_ready.set()
            await asyncio.sleep(SLEEP_S)  # 不查库：不额外占连接
            await session.execute(text(
                'update receivable_plans set amount = 200 where id = :p'
            ), {'p': plan_id})
            await session.commit()  # 放锁 → 端点应当立刻醒来

    async def caller() -> None:
        await holder_ready.wait()
        loop = asyncio.get_running_loop()
        started = loop.time()
        status, body = await http_get_milestones(order_id)
        get_result.append((status, body, loop.time() - started))

    await asyncio.gather(holder(), caller())

    status, _body, elapsed = get_result[0]
    print(f'  · 持锁 {SLEEP_S:.1f} 秒期间发出的真实 GET：返回 {status}，'
          f'**耗时 {elapsed:.3f}s**')
    check('GET /milestones 返回 200', status, 200)
    check_true('GET 被订单锁挡住了（耗时 > 1s；修好之前这里只有几毫秒）',
               elapsed > 1.0, f'{elapsed:.3f}s')

    final = await read_state(order_id)
    print(f'  终态：应收 {final["planned"]}、已确认已收 {final["received"]}、'
          f'收款节点 actual_date={final["payment_actual_date"]}')
    check_true('端点按**新事实**收尾：应收 200 / 已收 100 → 收款节点不得为完成',
               final['planned'] == '200.00' and final['payment_actual_date'] is None,
               f'actual_date={final["payment_actual_date"]}')

    await cleanup()


async def http_get_milestones(order_id: int) -> tuple[int, dict]:
    """发一次真实的 `GET /orders/{id}/milestones`（黑盒）。

    ⚠️ 必须丢到线程里跑（`asyncio.to_thread`）：`urllib` 是**同步阻塞**的，
    直接在协程里调会把整个事件循环卡住 —— 那样"持锁 2 秒后放锁"的那个
    `asyncio.sleep` 永远醒不来，端点就一直等锁，**死锁**（实测卡死，
    且从数据库侧看只是"有个会话在等锁"，看不出是脚本自己把循环堵了）。
    """
    return await asyncio.to_thread(_http_get_milestones_sync, order_id)


def _http_get_milestones_sync(order_id: int) -> tuple[int, dict]:
    import json
    import urllib.error
    import urllib.request

    token = _admin_token_sync()
    req = urllib.request.Request(
        f'{require_api_base()}/orders/{order_id}/milestones',
        headers={'Authorization': f'Bearer {token}'},
    )
    try:
        with urllib.request.urlopen(req) as res:
            return res.status, json.loads(res.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or '{}')


def _admin_token_sync() -> str:
    import json
    import urllib.request

    req = urllib.request.Request(
        f'{require_api_base()}/auth/login',
        data=json.dumps({'username': 'admin', 'password': 'admin123'}).encode(),
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    with urllib.request.urlopen(req) as res:
        return json.loads(res.read().decode())['data']['access_token']


async def reset_to_settled(order_id: int, plan_id: int) -> None:
    """把夹具恢复成"应收 100 / 已确认已收 100 / 收款节点未完成"。"""
    from sqlalchemy import text

    from app.core.database import SessionLocal

    async with SessionLocal() as session:
        await session.execute(text('update receivable_plans set amount = 100 where id = :p'),
                              {'p': plan_id})
        await session.execute(text(
            "update order_milestones set actual_date = null, skipped_at = null "
            " where order_id = :o and node = 'payment'"
        ), {'o': order_id})
        await session.commit()


async def cleanup() -> None:
    from sqlalchemy import text

    from app.core.database import SessionLocal

    async with SessionLocal() as session:
        pattern = f'{RUN}%'
        await session.execute(text(
            'delete from order_milestones where order_id in '
            '(select o.id from sales_orders o join customers c on c.id = o.customer_id '
            ' where c.name like :p)'
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
            'delete from sales_orders where customer_id in '
            '(select c.id from customers c where c.name like :p)'
        ), {'p': pattern})
        await session.execute(text('delete from customers where name like :p'), {'p': pattern})
        await session.commit()
        print('  （夹具已清）')


if __name__ == '__main__':
    asyncio.run(main())
    print()
    print(f'通过 {PASSED} 条，失败 {len(FAILED)} 条')
    if FAILED:
        for item in FAILED:
            print(f'  ✗ {item}')
        sys.exit(1)
    print('全部通过')
