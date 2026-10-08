#!/usr/bin/env python
"""第七批 7.9：回款登记 / 应收生成 / 并发确认的一致性（真 PostgreSQL 并发）。

为什么必须真库：内存 SQLite 会忽略 `FOR UPDATE`，"两个 50 同时确认"在那边
根本不会互相阻塞，跑出来的绿是假的。本套件用**两条独立 HTTP 连接同时打同一个
接口**（服务端各自一个事务、一条连接），验的是真行锁 + READ COMMITTED 下的汇总。

跑法（两个环境变量都必须显式给，指到一次性隔离库）：

    & .\\ops\\iso_checks.ps1 -Db crm_iso_pay2 -Port 8019 -Suites check_payment_consistency

或手工：

    cd backend
    $env:API_BASE="http://127.0.0.1:8019/api/v1"
    $env:DATABASE_URL="postgresql+asyncpg://crm:crm123456@127.0.0.1:5433/crm_iso_pay2"
    $env:DINGTALK_PUSH_OFF="1"; $env:WECOM_PUSH_OFF="1"; $env:SCHEDULER_ENABLED="0"
    $env:PYTHONPATH="."
    .venv\\Scripts\\python.exe scripts\\check_payment_consistency.py

⚠️ 本套件会真的调 CRM 接口（登记回款、财务确认、生成计划），所以 API_BASE 必须
显式给；不给就拒跑，免得默默打到开发后端 8000（有前科）。也不会调用钉钉/企微/ERP。

覆盖（7.9 验收四条 + 币种）：

0. **修前算法在真库上的复现**：只锁回款记录的确认顺序确实会停在 partial
   （实际合计 100）——说明第 3 条不是理论担忧；
1. 弱网重复登记（同一把键：串行 3 次 + 并发 2 次）→ 库里只有一行；
2. 同额同日、键不同的两笔真实回款 → 保留两行；
3. 并发 50+50 确认 → 节点 paid、已收 100、余额 0；
4. 并发生成计划 → 只落一套（修前会落两套 4 条）；
5. 确认/驳回竞态 → 一个成功一个被拒，终态唯一；
6. 币种：USD 节点上的回款记 USD；跨币种明确拒绝。
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import json
import os
from threading import Barrier
import time
import urllib.error
import urllib.request

#: ⚠️ 这两个变量必须在 import app.* **之前**校验：settings 在 import 时就读
#: DATABASE_URL，指错库的代价是直接写别人的数据。
from _test_support import require_api_base, require_isolated_db

require_isolated_db()
BASE = require_api_base()
DATABASE_URL = os.environ.get("DATABASE_URL") or ""
if not BASE:
    raise SystemExit(
        "必须显式设置 API_BASE（本套件会真的登记回款/确认/生成计划，"
        "不能默认打到开发后端 8000）"
    )
if "8000" in BASE:
    raise SystemExit(f"API_BASE 指向 8000（开发后端）很可能是误传：{BASE}")
if "127.0.0.1" not in BASE and "localhost" not in BASE:
    raise SystemExit(f"API_BASE 不是本机地址，拒绝跑：{BASE}")
_db_name = DATABASE_URL.rsplit("/", 1)[-1].split("?")[0]
if not _db_name.startswith(("crm_iso", "crm_check")):
    raise SystemExit(f"拒绝执行：库名 '{_db_name}' 不是一次性隔离库（须以 crm_iso/crm_check 开头）")

import app.main  # noqa: F401,E402  保证所有模型注册进 metadata
from sqlalchemy import func, select, text  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.modules.customer.model import Customer  # noqa: E402
from app.modules.order.model import SalesOrder  # noqa: E402
from app.modules.payment.model import PaymentRecord, ReceivablePlan  # noqa: E402
from app.modules.payment.service import confirmed_amount  # noqa: E402
from app.modules.user.model import User  # noqa: E402

PREFIX = f"CHK79{int(time.time())}"
STARTED_AT = datetime.now(UTC)
DUE = (date.today() + timedelta(days=30)).isoformat()
FAILURES: list[str] = []
IDS: dict = {}


def check(label: str, actual, expected) -> None:
    good = actual == expected
    print(f'  {"OK  " if good else "FAIL"} {label}: {actual!r}（期望 {expected!r}）')
    if not good:
        FAILURES.append(label)


def check_true(label: str, condition: bool, detail: str = "") -> None:
    print(f'  {"OK  " if condition else "FAIL"} {label}' + (f"：{detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


def call(method: str, path: str, token: str | None = None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"code": None, "message": raw[:200]}


def login(username: str, password: str) -> str:
    status, res = call("POST", "/auth/login", body={"username": username, "password": password})
    if status != 200 or res.get("code") != 0:
        raise SystemExit(f"登录失败（{username}）：HTTP {status} {res}")
    return res["data"]["access_token"]


def race(*requests):
    """两条**独立 HTTP 连接**同时发请求。

    用 Barrier 让两个线程真的同时起跑（否则线程池调度会让一个先跑完，
    并发窗口根本不存在，测出来的绿没有意义）。
    每项是 (method, path, token, body)。
    """

    barrier = Barrier(len(requests))
    results: list = [None] * len(requests)

    def run(index: int, method: str, path: str, token: str, body) -> None:
        barrier.wait(timeout=30)
        results[index] = call(method, path, token=token, body=body)

    with ThreadPoolExecutor(max_workers=len(requests)) as pool:
        futures = [pool.submit(run, i, *item) for i, item in enumerate(requests)]
        for future in futures:
            future.result(timeout=120)
    return results


# ---------------------------------------------------------------------------
# 夹具：客户/订单/节点直接建库（接口建单还要 SKU 明细，与本条要验的东西无关），
# 回款与生成统统走 HTTP 接口 —— 那才是被测代码。
# ---------------------------------------------------------------------------
async def build_fixtures() -> None:
    async with SessionLocal() as s:
        admin_id = (await s.execute(text("select id from users where username='admin'"))).scalar_one()
        IDS["admin_id"] = admin_id

        customer = Customer(
            name=f"{PREFIX}-客户",
            customer_type="企业",
            level="B",
            status="active",
            pool_status="private",
            owner_id=admin_id,
            created_by=admin_id,
        )
        s.add(customer)
        await s.flush()

        def order(no: str, total: str, currency: str = "CNY") -> SalesOrder:
            return SalesOrder(
                order_no=f"{PREFIX}-{no}",
                customer_id=customer.id,
                owner_id=admin_id,
                sales_owner_id=admin_id,
                total_amount=Decimal(total),
                currency=currency,
                status="pending",
                created_by=admin_id,
            )

        async def plan(row: SalesOrder, amount: str, currency: str, name: str) -> ReceivablePlan:
            node = ReceivablePlan(
                order_id=row.id,
                plan_name=name,
                due_date=date.today() + timedelta(days=30),
                amount=Decimal(amount),
                currency=currency,
                status="pending",
                created_at=datetime.now(UTC),
            )
            s.add(node)
            await s.flush()
            return node

        # A：100 元一个节点 —— 并发 50+50 确认
        order_a = order("A", "100")
        s.add(order_a)
        await s.flush()
        IDS["plan_a"] = (await plan(order_a, "100", "CNY", "并发确认")).id
        IDS["order_a"] = order_a.id

        # B：1000 元、**没有**节点 —— 并发生成计划
        order_b = order("B", "1000")
        # B2：同上，用来验"同一把键并发生成"
        order_b2 = order("B2", "1000")
        # D：1000 元一个节点 —— 同额同日两笔真实回款
        order_d = order("D", "1000")
        # E/L：100 元一个节点 —— 确认/驳回竞态、修前算法复现
        order_e = order("E", "100")
        order_l = order("L", "100")
        # C：USD 1000 一个美元节点 —— 币种
        order_c = order("C", "1000", currency="USD")
        s.add_all([order_b, order_b2, order_d, order_e, order_l, order_c])
        await s.flush()
        IDS["order_b"] = order_b.id
        IDS["order_b2"] = order_b2.id
        IDS["order_d"] = order_d.id
        IDS["order_e"] = order_e.id
        IDS["order_l"] = order_l.id
        IDS["order_c"] = order_c.id
        IDS["plan_d"] = (await plan(order_d, "1000", "CNY", "同额同日")).id
        IDS["plan_e"] = (await plan(order_e, "100", "CNY", "竞态")).id
        IDS["plan_l"] = (await plan(order_l, "100", "CNY", "修前复现")).id
        IDS["plan_c"] = (await plan(order_c, "1000", "USD", "外币")).id
        # 修前算法复现用的两笔回款直接建库：它们只是夹具，被测顺序在下面手工摆。
        for _ in range(2):
            s.add(
                PaymentRecord(
                    receivable_plan_id=IDS["plan_l"],
                    order_id=order_l.id,
                    received_date=date.today(),
                    received_amount=Decimal("50"),
                    currency="CNY",
                    payment_method="银行转账",
                    status="pending",
                    created_by=admin_id,
                    created_at=datetime.now(UTC),
                )
            )
        await s.flush()
        rows = (
            await s.execute(
                select(PaymentRecord.id)
                .where(PaymentRecord.receivable_plan_id == IDS["plan_l"])
                .order_by(PaymentRecord.id)
            )
        ).scalars().all()
        IDS["legacy_payments"] = list(rows)
        await s.commit()


async def cleanup() -> None:
    """只删本套件造的数据（订单号/客户名带 PREFIX），自底向上。"""
    orders = f"select id from sales_orders where order_no like '{PREFIX}%'"
    async with SessionLocal() as s:
        await s.execute(text(f"delete from payment_records where order_id in ({orders})"))
        await s.execute(text(f"delete from receivable_plans where order_id in ({orders})"))
        await s.execute(
            text(
                "delete from notifications where business_type = 'order' "
                f"and business_id in ({orders})"
            )
        )
        await s.execute(text(f"delete from followups where order_id in ({orders})"))
        await s.execute(
            text(
                "delete from audit_logs where business_type in ('payment','receivable_plan') "
                "and created_at >= :t"
            ),
            {"t": STARTED_AT},
        )
        await s.execute(text("delete from request_keys where request_key like :p"), {"p": f"{PREFIX}%"})
        await s.execute(text(f"delete from sales_orders where id in ({orders})"))
        await s.execute(text("delete from customers where name like :p"), {"p": f"{PREFIX}%"})
        await s.commit()


async def plan_row(plan_id: int) -> tuple[str, Decimal, Decimal]:
    """(节点状态, 计划金额, 已确认回款合计)。"""
    async with SessionLocal() as s:
        plan = (await s.execute(select(ReceivablePlan).where(ReceivablePlan.id == plan_id))).scalar_one()
        confirmed = await confirmed_amount(s, plan_id)
        return plan.status, Decimal(plan.amount), Decimal(confirmed)


async def payment_row(payment_id: int) -> PaymentRecord:
    async with SessionLocal() as s:
        return (await s.execute(select(PaymentRecord).where(PaymentRecord.id == payment_id))).scalar_one()


async def count_payments(plan_id: int) -> int:
    async with SessionLocal() as s:
        return (
            await s.execute(
                select(func.count()).select_from(PaymentRecord).where(
                    PaymentRecord.receivable_plan_id == plan_id
                )
            )
        ).scalar_one()


async def count_plans(order_id: int) -> int:
    async with SessionLocal() as s:
        return (
            await s.execute(
                select(func.count()).select_from(ReceivablePlan).where(
                    ReceivablePlan.order_id == order_id
                )
            )
        ).scalar_one()


# ---------------------------------------------------------------------------
# 0. 修前算法的真库复现
# ---------------------------------------------------------------------------
async def legacy_write_skew(plan_id: int, payment_ids: list[int]) -> tuple[Decimal, Decimal]:
    """把**修前的确认顺序**在真库上手工摆出来：各自锁住自己的回款记录、各自
    汇总、各自写节点状态并提交。两个事务锁的不是同一行，谁也不等谁，于是
    双方都只看到 50，都写 partial —— 而提交后真实合计是 100。

    这不是在测现在的代码（现在会先锁共同节点，这种交错不可能发生），而是证明
    "停在 partial" 在这套库上真的会发生，第 3 条不是理论担忧。
    """
    async with SessionLocal() as s1, SessionLocal() as s2:
        rec1 = (
            await s1.execute(
                select(PaymentRecord).where(PaymentRecord.id == payment_ids[0]).with_for_update()
            )
        ).scalar_one()
        rec2 = (
            await s2.execute(
                select(PaymentRecord).where(PaymentRecord.id == payment_ids[1]).with_for_update()
            )
        ).scalar_one()
        # 各自确认自己那一笔（都没提交，对方读不到）
        rec1.status = "confirmed"
        rec2.status = "confirmed"
        await s1.flush()
        await s2.flush()
        total1 = await confirmed_amount(s1, plan_id)
        total2 = await confirmed_amount(s2, plan_id)
        status1 = "partial" if total1 > 0 else "pending"
        status2 = "partial" if total2 > 0 else "pending"
        await s1.execute(
            text("update receivable_plans set status = :st where id = :pid"),
            {"st": status1, "pid": plan_id},
        )
        await s1.commit()
        # 第二笔的 UPDATE 会等第一笔的行锁释放，然后**用自己算的旧汇总覆盖回去**
        await s2.execute(
            text("update receivable_plans set status = :st where id = :pid"),
            {"st": status2, "pid": plan_id},
        )
        await s2.commit()
        return total1, total2


async def scenario_legacy_reproduction() -> None:
    print()
    print("=== 0. 修前算法（只锁回款记录）在真库上的复现 ===")
    total1, total2 = await legacy_write_skew(IDS["plan_l"], IDS["legacy_payments"])
    status, amount, confirmed = await plan_row(IDS["plan_l"])
    check("两个事务各自汇总到的金额（都看不到对方那笔）", (float(total1), float(total2)), (50.0, 50.0))
    check("复现结果：节点状态仍停在 partial", status, "partial")
    check("而真实已确认合计（=100 ≥ 计划额）", float(confirmed), 100.0)
    check_true(
        "结论：修前确实会出现「合计 100 而节点 partial」的写偏斜",
        status == "partial" and confirmed >= amount,
        f"status={status} confirmed={confirmed} amount={amount}",
    )


# ---------------------------------------------------------------------------
# 1/2. 请求键：弱网重复一行、同额同日两笔保留两行
# ---------------------------------------------------------------------------
async def scenario_request_key(admin: str) -> None:
    print()
    print("=== 1. 弱网重复登记：同一把键只落一行 ===")
    key = f"{PREFIX}-weak"
    body = {
        "receivable_plan_id": IDS["plan_d"],
        "received_date": date.today().isoformat(),
        "received_amount": 50,
        "payment_method": "银行转账",
        "request_key": key,
    }
    ids = []
    for attempt in range(3):
        status, res = call("POST", "/payments", admin, body)
        check_true(
            f"串行第 {attempt + 1} 次登记成功(HTTP 200/code 0)",
            status == 200 and res.get("code") == 0,
            str(res.get("message"))[:60],
        )
        if res.get("code") != 0:
            raise SystemExit(f"串行登记失败，后续断言无意义：HTTP {status} {res}")
        ids.append(res["data"]["id"])
    check("三次登记返回同一条记录", len(set(ids)), 1)
    check("串行重复后库里仍是 1 行", await count_payments(IDS["plan_d"]), 1)

    # 并发同键：服务端唯一约束只让一个进，另一个回放（或明确说"处理中"）
    key2 = f"{PREFIX}-weak-race"
    body2 = {**body, "request_key": key2}
    results = race(*[("POST", "/payments", admin, body2) for _ in range(2)])
    codes = sorted(res.get("code") for _, res in results)
    check_true(
        "并发同键：一个建成功、另一个回放或明确报处理中（不能又建一条）",
        codes[0] in (0, 40901, 40902) and codes[1] == 0,
        f"codes={codes}",
    )
    returned = {res["data"]["id"] for _, res in results if res.get("code") == 0 and res.get("data")}
    check("并发同键的两次成功都指向同一条记录", len(returned), 1)
    # 这里必须是 2 行：弱网键那一行 + 这次新键的一行。**不同键就是两笔不同的录入**，
    # 不能因为金额和日期一样就合并 —— 下一节专门验这条。
    check("并发同键后库里是 2 行（不同键各自一行）", await count_payments(IDS["plan_d"]), 2)

    print()
    print("=== 2. 同额同日、键不同的两笔真实回款 → 保留两行 ===")
    same_day = date.today().isoformat()
    real_codes = []
    for index in (1, 2):
        status, res = call(
            "POST",
            "/payments",
            admin,
            {
                "receivable_plan_id": IDS["plan_d"],
                "received_date": same_day,
                "received_amount": 50,
                "payment_method": "银行转账",
                "voucher_note": f"真实回款第 {index} 笔",
                "request_key": f"{PREFIX}-real-{index}",
            },
        )
        real_codes.append(res.get("code"))
    check("两笔真实回款都登记成功", real_codes, [0, 0])
    check(
        "库里有 4 行（两个弱网键各 1 行 + 真实这 2 行），没有被按金额+日期合并",
        await count_payments(IDS["plan_d"]),
        4,
    )


# ---------------------------------------------------------------------------
# 3. 并发 50+50 确认 → paid
# ---------------------------------------------------------------------------
async def scenario_concurrent_confirm(admin: str) -> None:
    print()
    print("=== 3. 并发 50+50 确认 → 节点 paid、余额 0 ===")
    payment_ids = []
    for index in (1, 2):
        status, res = call(
            "POST",
            "/payments",
            admin,
            {
                "receivable_plan_id": IDS["plan_a"],
                "received_date": date.today().isoformat(),
                "received_amount": 50,
                "payment_method": "银行转账",
                "request_key": f"{PREFIX}-conf-{index}",
            },
        )
        if res.get("code") != 0:
            raise SystemExit(f"造回款失败：{status} {res}")
        payment_ids.append(res["data"]["id"])

    results = race(*[("POST", f"/payments/{pid}/confirm", admin, {}) for pid in payment_ids])
    statuses = [status for status, _ in results]
    check("两个并发确认都成功（没有互相挡下去）", statuses, [200, 200])

    status, amount, confirmed = await plan_row(IDS["plan_a"])
    check("节点状态", status, "paid")
    check("已确认合计", float(confirmed), 100.0)
    check("计划金额", float(amount), 100.0)

    status, res = call("GET", f"/receivables/{IDS['plan_a']}", admin)
    check("接口返回的已收金额", res["data"]["received_amount"], 100.0)
    check("接口返回的未收金额（余额）", res["data"]["remaining_amount"], 0.0)
    status, res = call("GET", f"/orders/{IDS['order_a']}/finance-summary", admin)
    check("订单财务概览：已回款", res["data"]["received_amount"], 100.0)
    check("订单财务概览：未回款", res["data"]["unreceived_amount"], 0.0)


# ---------------------------------------------------------------------------
# 4. 并发生成计划 → 一套
# ---------------------------------------------------------------------------
async def scenario_concurrent_generate(admin: str) -> None:
    print()
    print("=== 4. 并发生成应收计划 → 只落一套 ===")
    generate_body = {
        "ratios": [0.3, 0.7],
        "first_due_date": DUE,
        "second_due_date": DUE,
    }
    results = race(
        ("POST", f"/orders/{IDS['order_b']}/receivables/generate", admin, {**generate_body, "request_key": f"{PREFIX}-gen-a"}),
        ("POST", f"/orders/{IDS['order_b']}/receivables/generate", admin, {**generate_body, "request_key": f"{PREFIX}-gen-b"}),
    )
    codes = sorted(res.get("code") for _, res in results)
    check_true(
        "并发不同键：一个生成成功、另一个被「已有计划」挡住",
        codes == [0, 40002],
        f"codes={codes}",
    )
    check("订单 B 的计划条数（3:7 两期，只落一套）", await count_plans(IDS["order_b"]), 2)

    # 同一把键并发：第二次要回放第一次的结果，而不是再插一套
    results = race(
        ("POST", f"/orders/{IDS['order_b2']}/receivables/generate", admin, {**generate_body, "request_key": f"{PREFIX}-gen-same"}),
        ("POST", f"/orders/{IDS['order_b2']}/receivables/generate", admin, {**generate_body, "request_key": f"{PREFIX}-gen-same"}),
    )
    outcomes = sorted((res.get("code")) for _, res in results)
    check_true(
        "并发同键：一个生成成功，另一个回放或明确报处理中",
        outcomes[0] in (0, 40901) and outcomes[1] == 0,
        f"codes={outcomes}",
    )
    check("订单 B2 的计划条数仍是 2", await count_plans(IDS["order_b2"]), 2)


# ---------------------------------------------------------------------------
# 5. 确认/驳回竞态
# ---------------------------------------------------------------------------
async def scenario_confirm_reject_race(admin: str) -> None:
    print()
    print("=== 5. 确认 / 驳回竞态：终态唯一、账目一致 ===")
    status, res = call(
        "POST",
        "/payments",
        admin,
        {
            "receivable_plan_id": IDS["plan_e"],
            "received_date": date.today().isoformat(),
            "received_amount": 100,
            "payment_method": "银行转账",
            "request_key": f"{PREFIX}-race-pay",
        },
    )
    payment_id = res["data"]["id"]

    results = race(
        ("POST", f"/payments/{payment_id}/confirm", admin, {}),
        ("POST", f"/payments/{payment_id}/reject", admin, {}),
    )
    statuses = sorted(status for status, _ in results)
    check("一个成功、一个被状态拦下", statuses, [200, 400])

    record = await payment_row(payment_id)
    check_true("终态是干净的 confirmed/rejected 之一", record.status in ("confirmed", "rejected"), record.status)
    plan_status, _, confirmed = await plan_row(IDS["plan_e"])
    if record.status == "confirmed":
        check("确认胜出：节点 paid、已确认 100", (plan_status, float(confirmed)), ("paid", 100.0))
    else:
        check("驳回胜出：节点未收钱（不得留下 partial）", (plan_status, float(confirmed)), ("pending", 0.0))


# ---------------------------------------------------------------------------
# 6. 币种
# ---------------------------------------------------------------------------
async def scenario_currency(admin: str) -> None:
    print()
    print("=== 6. 币种：继承来源、跨币种明确拒绝 ===")
    status, res = call(
        "POST",
        "/payments",
        admin,
        {
            "receivable_plan_id": IDS["plan_c"],
            "received_date": date.today().isoformat(),
            "received_amount": 400,
            "payment_method": "电汇",
            "request_key": f"{PREFIX}-usd-1",
        },
    )
    check("不传币种时继承 USD 节点（修前会落成 CNY）", res["data"]["currency"], "USD")
    record = await payment_row(res["data"]["id"])
    check("库里存的币种也是 USD", record.currency, "USD")

    status, res = call(
        "POST",
        "/payments",
        admin,
        {
            "receivable_plan_id": IDS["plan_c"],
            "received_date": date.today().isoformat(),
            "received_amount": 600,
            "currency": "CNY",
            "request_key": f"{PREFIX}-usd-2",
        },
    )
    check("跨币种登记被拒绝的 HTTP 状态", status, 422)
    check("拒绝错误码", res.get("code"), 40001)
    check_true("拒绝文案点明跨币种", "跨币种" in (res.get("message") or ""), res.get("message", "")[:60])
    check("被拒的那笔没有落库", await count_payments(IDS["plan_c"]), 1)


def main() -> None:
    print(f"API_BASE={BASE}")
    print(f"DB={_db_name}")
    admin = login("admin", "admin123")
    # 全程**一个事件循环**：asyncpg 的连接绑定在创建它的循环上，
    # 多次 asyncio.run 会让第二次复用到别的循环的连接而报错。
    asyncio.run(run_all(admin))


async def run_all(admin: str) -> None:
    await build_fixtures()
    try:
        await scenario_legacy_reproduction()
        await scenario_request_key(admin)
        await scenario_concurrent_confirm(admin)
        await scenario_concurrent_generate(admin)
        await scenario_confirm_reject_race(admin)
        await scenario_currency(admin)
    finally:
        await cleanup()

    print()
    if FAILURES:
        print(f"FAILED（{len(FAILURES)}）：" + "；".join(FAILURES))
        raise SystemExit(1)
    print("全部通过 OK")


if __name__ == "__main__":
    main()
