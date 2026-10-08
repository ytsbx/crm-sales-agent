"""取消订单：规则只认一个出口（2026-10-06 收口）。

**只在隔离库跑**：库名必须含 test（或 CI=true），且推送开关全关。

## 为什么有这个套件

界面上原来**没有**"取消订单"按钮。全系统唯一能取消订单的地方，是订单详情页
「更新履约状态」那个下拉里选「已取消」。

后端其实有一个专门的取消接口 `POST /orders/{id}/cancel`，规矩写得很全：
已确认回款直接拒绝、待确认回款随单驳回、未回清的应收计划置为已取消
（**停止催收与逾期提醒**）、未发货批次随单取消。

**但前端从来没接它**（函数写在 `shared/api/order.ts` 里，零调用）。
于是走"更新状态"这条路，上面四件事**一件都不会发生**：
订单显示已取消，催收却照旧发；已经收到过钱的订单也能取消。

现在是两条一起收：
1. 前端把「取消订单」独立成按钮，走专用接口，弹窗写清影响；
2. 后端 `POST /orders/{id}/status` **拒绝** `cancelled` —— 换个入口也绕不过去。

验收（对应审查方一贯的"换个入口绕过规则"这一类）：
- `/status` 传 `cancelled` → 422，且订单状态**没变**；
- `/status` 传正常状态仍然可用（别改出"因噎废食"）；
- `/cancel` 的四条连带规则各测一条；
- 已取消的订单不能再取消（终态）；
- 审计 action 是 `cancel` 且记下连带发生了多少笔。

跑法（需要后端在跑）：
    cd backend
    API_BASE=http://127.0.0.1:8001/api/v1 DATABASE_URL=...crm_sales_agent_test \\
      PYTHONPATH=. .venv/bin/python scripts/check_order_cancel_rules.py
"""

import asyncio
import json
import os
import time
import urllib.error
import urllib.request
from datetime import UTC, date, datetime, timedelta
from _test_support import require_api_base, require_isolated_db

require_isolated_db()

import app.main  # noqa: F401  保证所有模型都注册进 metadata
from sqlalchemy import text

from app.core.database import SessionLocal
from app.modules.customer.model import Customer
from app.modules.order.model import OrderShipmentBatch, SalesOrder
from app.modules.payment.model import PaymentRecord, ReceivablePlan
from app.modules.user.model import User

FAILURES: list[str] = []
BASE = require_api_base()
PREFIX = "CHKCANCEL"
STAMP = str(int(time.time()))
#: 业务规则拒绝
REJECTED = (400, 422)


def check(label: str, actual, expected) -> None:
    good = actual == expected
    print(f'  {"OK  " if good else "FAIL"} {label}: {actual!r}（期望 {expected!r}）')
    if not good:
        FAILURES.append(label)


def check_true(label: str, condition: bool, detail: str = "") -> None:
    print(f'  {"OK  " if condition else "FAIL"} {label}' + (f"：{detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


def check_in(label: str, actual, expected: tuple) -> None:
    good = actual in expected
    print(f'  {"OK  " if good else "FAIL"} {label}: {actual!r}（期望属于 {expected}）')
    if not good:
        FAILURES.append(label)


def call(method: str, path: str, token: str | None = None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode() or "{}")
        except Exception:
            return exc.code, {}


def login(username: str, password: str) -> str:
    _, res = call("POST", "/auth/login", body={"username": username, "password": password})
    if res.get("code") != 0:
        raise SystemExit(f"登录失败（{username}）：{res.get('message')}")
    return res["data"]["access_token"]


async def cleanup() -> None:
    """自底向上清干净（回款 → 应收 → 批次 → 状态历史 → 订单 → 客户）。"""
    async with SessionLocal() as s:
        orders = "(select id from sales_orders where order_no like :n)"
        for sql in (
            "delete from payment_records where order_id in " + orders,
            "delete from receivable_plans where order_id in " + orders,
            "delete from order_shipment_batches where order_id in " + orders,
            "delete from order_status_history where order_id in " + orders,
            "delete from sales_orders where order_no like :n",
            "delete from customers where name like :p",
        ):
            await s.execute(text(sql), {"n": f"{PREFIX}%", "p": f"{PREFIX}%"})
        await s.commit()


IDS: dict = {}


async def build_fixtures() -> None:
    """四个订单，分别覆盖取消的四条连带规则。"""
    async with SessionLocal() as s:
        owner = (
            await s.execute(text("select id from users where username = 'admin'"))
        ).scalar_one()

        customer = Customer(
            name=f"{PREFIX}客户-{STAMP}", owner_id=owner, pool_status="private",
            status="active", created_by=owner, level="A",
        )
        s.add(customer)
        await s.flush()
        IDS["customer"] = customer.id

        today = date.today()
        now = datetime.now(UTC)

        def make_order(name: str, **kw) -> SalesOrder:
            order = SalesOrder(
                order_no=f"{PREFIX}{name}{STAMP}",
                customer_id=customer.id,
                total_amount=10000,
                currency="CNY",
                status="pending",
                owner_id=owner,
                **kw,
            )
            s.add(order)
            return order

        # ① 有**已确认**回款 → 取消应当被拒（钱不能静默作废）
        o_paid = make_order("-PAID")
        # ② 未回清的应收计划（一笔已结清、一笔未收）→ 取消后未清的作废、已结清的不动
        o_plan = make_order("-PLAN")
        # ③ 待确认回款 → 随单驳回
        o_pending = make_order("-PEND")
        # ④ 计划中的发货批次 → 随单取消
        o_batch = make_order("-BATCH")
        await s.flush()
        IDS.update(
            o_paid=o_paid.id, o_plan=o_plan.id,
            o_pending=o_pending.id, o_batch=o_batch.id,
        )

        def add_plan(order: SalesOrder, name: str, status: str, amount: float) -> ReceivablePlan:
            plan = ReceivablePlan(
                order_id=order.id, plan_name=name, due_date=today + timedelta(days=30),
                amount=amount, currency="CNY", status=status, created_at=now,
            )
            s.add(plan)
            return plan

        # ① 已确认回款
        plan_paid = add_plan(o_paid, "定金", "partial", 3000)
        await s.flush()
        s.add(
            PaymentRecord(
                order_id=o_paid.id, receivable_plan_id=plan_paid.id,
                received_date=today, received_amount=3000, currency="CNY",
                status="confirmed", created_at=now,
            )
        )

        # ② 一笔已结清 + 一笔未收
        paid_plan = add_plan(o_plan, "已结清", "paid", 5000)
        open_plan = add_plan(o_plan, "尾款", "pending", 5000)
        await s.flush()
        IDS["plan_paid"] = paid_plan.id
        IDS["plan_open"] = open_plan.id

        # ③ 待确认回款
        plan_pending = add_plan(o_pending, "定金", "pending", 3000)
        await s.flush()
        payment = PaymentRecord(
            order_id=o_pending.id, receivable_plan_id=plan_pending.id,
            received_date=today, received_amount=3000, currency="CNY",
            status="pending", created_at=now,
            voucher_note=f"{PREFIX}客户转账凭证已上传",
        )
        s.add(payment)
        await s.flush()
        IDS["payment_pending"] = payment.id
        IDS["plan_pending"] = plan_pending.id

        # ④ 计划中的批次（表里只有 order_id / created_at 是必填）
        batch = OrderShipmentBatch(order_id=o_batch.id, status="planned", created_at=now)
        s.add(batch)
        await s.flush()
        IDS["batch"] = batch.id

        await s.commit()


async def order_status(order_id: int) -> str | None:
    async with SessionLocal() as s:
        return (
            await s.execute(
                text("select status from sales_orders where id = :i"), {"i": order_id}
            )
        ).scalar_one_or_none()


async def plan_status(plan_id: int) -> str | None:
    async with SessionLocal() as s:
        return (
            await s.execute(
                text("select status from receivable_plans where id = :i"), {"i": plan_id}
            )
        ).scalar_one_or_none()


async def payment_status(payment_id: int) -> str | None:
    async with SessionLocal() as s:
        return (
            await s.execute(
                text("select status from payment_records where id = :i"), {"i": payment_id}
            )
        ).scalar_one_or_none()


async def batch_status(batch_id: int) -> str | None:
    async with SessionLocal() as s:
        return (
            await s.execute(
                text("select status from order_shipment_batches where id = :i"),
                {"i": batch_id},
            )
        ).scalar_one_or_none()


# ---------------------------------------------------------------- 用例

async def section1_one_way_in(token: str) -> None:
    print("\n== 一、规则只认一个出口：更新状态不许改「已取消」 ==")
    oid = IDS["o_plan"]

    status, res = call("POST", f"/orders/{oid}/status", token, {"status": "cancelled"})
    check_in("用更新履约状态改成「已取消」→ 被拒", status, REJECTED)
    check_true(
        "拒绝理由指向专门的取消入口",
        "取消订单" in (res.get("message") or ""),
        (res.get("message") or "")[:90],
    )
    check("被拒后订单状态没变（仍是待生产）", await order_status(oid), "pending")

    # 反向对照：正常状态照样能改，别改出"因噎废食"
    status, res = call("POST", f"/orders/{oid}/status", token, {"status": "in_production"})
    check("正常状态（生产中）仍然可以更新", status, 200)
    check("更新后状态已变", await order_status(oid), "in_production")
    # 文案要带订单号：这句是全局浮层，切页面还会挂几秒，没主语就对不上号
    check_true(
        "成功提示里带订单号（不然用户不知道是哪个订单）",
        PREFIX in (res.get("message") or ""),
        (res.get("message") or "")[:90],
    )


async def section2_rules(token: str) -> None:
    print("\n== 二、四条连带规则 ==")

    # ① 已确认回款 → 拒绝取消
    status, res = call("POST", f"/orders/{IDS['o_paid']}/cancel", token)
    check_in("有已确认回款的订单 → 拒绝取消", status, REJECTED)
    check_true(
        "说清是钱的问题", "回款" in (res.get("message") or ""),
        (res.get("message") or "")[:90],
    )
    check("被拒后订单没被取消", await order_status(IDS["o_paid"]), "pending")

    # ② 未回清的应收 → 一并作废；已结清的不动
    status, _ = call("POST", f"/orders/{IDS['o_plan']}/cancel", token)
    check("未回清应收的订单可以取消", status, 200)
    check("订单状态变成已取消", await order_status(IDS["o_plan"]), "cancelled")
    check("未回清的应收计划 → 作废（停催收）", await plan_status(IDS["plan_open"]), "cancelled")
    check("已结清的应收计划 → **不动**", await plan_status(IDS["plan_paid"]), "paid")

    # ③ 待确认回款 → 随单驳回
    status, _ = call("POST", f"/orders/{IDS['o_pending']}/cancel", token)
    check("有待确认回款的订单可以取消", status, 200)
    check("待确认回款 → 随单驳回", await payment_status(IDS["payment_pending"]), "rejected")
    async with SessionLocal() as s:
        note = (
            await s.execute(
                text("select voucher_note from payment_records where id = :i"),
                {"i": IDS["payment_pending"]},
            )
        ).scalar_one()
    check_true("驳回原因写在凭证备注里", "订单取消" in (note or ""), (note or "")[:80])

    # ④ 计划中的批次 → 随单取消
    status, _ = call("POST", f"/orders/{IDS['o_batch']}/cancel", token)
    check("有计划批次的订单可以取消", status, 200)
    check("计划中的批次 → 随单取消", await batch_status(IDS["batch"]), "cancelled")


async def section3_terminal_and_audit(token: str) -> None:
    print("\n== 三、终态不可重复 + 审计留痕 ==")
    oid = IDS["o_plan"]
    status, _ = call("POST", f"/orders/{oid}/cancel", token)
    check_in("已取消的订单不能再次取消", status, REJECTED)

    status, _ = call("POST", f"/orders/{oid}/status", token, {"status": "shipped"})
    check_in("已取消的订单不能改回其他状态", status, REJECTED)

    async with SessionLocal() as s:
        row = (
            await s.execute(
                text(
                    "select before_data, after_data from audit_logs "
                    "where business_type = 'order' and business_id = :i and action = 'cancel' "
                    "order by id desc limit 1"
                ),
                {"i": oid},
            )
        ).first()
    check_true("取消有独立审计（action=cancel）", row is not None)
    after = json.dumps(row[1], ensure_ascii=False) if row and row[1] else ""
    check_true(
        "审计里记下连带作废了几笔应收",
        "cancelled_receivable_plans" in after,
        after[:110],
    )
    check_true(
        "审计里记下商机成交状态未回退（口径要留痕）",
        "opportunity" in after,
        after[:110],
    )


async def main() -> None:
    url = os.environ.get("DATABASE_URL", "")
    assert "test" in url or os.environ.get("CI") == "true", (
        "必须在隔离库跑：DATABASE_URL 里要含 test"
    )
    print(f"目标：{BASE}")
    await cleanup()
    await build_fixtures()
    token = login("admin", "admin123")

    try:
        await section1_one_way_in(token)
        await section2_rules(token)
        await section3_terminal_and_audit(token)
    finally:
        await cleanup()

    print("\n" + "=" * 60)
    if FAILURES:
        print(f"失败 {len(FAILURES)} 项：")
        for item in FAILURES:
            print("  -", item)
        raise SystemExit(1)
    print("全部通过")


if __name__ == "__main__":
    asyncio.run(main())
