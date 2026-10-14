#!/usr/bin/env python3
"""真库检查：**回款分配**的算法与"已收"口径（第十批回款分配）。

## 覆盖什么

主人 2026-10-13 拍板的口径：

  1. 一笔银行流水仍是一行 `payment_records`（超收**不拆记录**）；
  2. 超出该期应收的部分**按到期日从早到晚**自动抵后续期；
  3. **每期必须实际分到钱才算完成** —— 不能因为订单总额够了就把各期都标完成；
  4. 人工调整**整份替换**，且之后系统自动分配**不再覆盖**人工结论；
  5. 多收的钱在全部应收填满之后**不硬塞**给某一期（挂"待分配"）。

## 一条容易被写错、且实测踩过的判据（③ 段）

"已收"要读**分配表**（`payment_allocations`），但**没有分配行的回款**要有兜底
（历史补数/夹具直接插回款时就是那样）。坑在于兜底的判据：

  · ✅ 正确的是"**这笔回款在整个系统里一条分配都没有**"；
  · ❌ 我第一版写成"对**这一期**没有分配" → 人工把 200 全挪到二期之后，
    一期就没有分配行了，于是那 200 又按 `receivable_plan_id` 被算回一期
    —— **一期凭空显示已收 200**，而它一分钱都没分到（双计）。
    这个洞是**浏览器端到端验证**时才暴露出来的，单测与接口套件都没覆盖。

## 跑法

    DB=crm_check_allocation
    psql -h 127.0.0.1 -U postgres -d postgres -c "CREATE DATABASE $DB OWNER crm;"
    cd backend && export DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5432/$DB
    PYTHONPATH=. .venv/bin/python -m alembic upgrade head
    PYTHONPATH=. .venv/bin/python -m scripts.seed
    PYTHONPATH=. .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8079 &
    API_BASE=http://127.0.0.1:8079/api/v1 PYTHONPATH=. DATABASE_URL=$DATABASE_URL \\
      .venv/bin/python scripts/check_allocation_rules.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _test_support import require_isolated_db  # noqa: E402

require_isolated_db()

from app.core.database import SessionLocal  # noqa: E402
from app.modules.customer.model import Customer  # noqa: E402
from app.modules.order.model import OrderMilestone, SalesOrder  # noqa: E402
from app.modules.order import milestones as milestones_svc  # noqa: E402
from app.modules.payment import service as payment_service  # noqa: E402
from app.modules.payment.model import (  # noqa: E402
    PaymentAllocation,
    PaymentRecord,
    ReceivablePlan,
)
from app.modules.user.model import User  # noqa: E402,F401  # 注册映射用
from sqlalchemy import select  # noqa: E402

PREFIX = "CHKALLOC"
RESULTS: list[tuple[str, bool, str]] = []


def check_true(label: str, value, detail: str = "") -> None:
    ok = bool(value)
    RESULTS.append((label, ok, detail))
    print(f"  {'OK  ' if ok else 'FAIL'} {label}{('：' + detail) if detail else ''}")


def check(label: str, value, expected) -> None:
    ok = value == expected
    RESULTS.append((label, ok, f"{value!r}（期望 {expected!r}）"))
    print(f"  {'OK  ' if ok else 'FAIL'} {label}: {value!r}（期望 {expected!r}）")


async def build_order(session, *, plan_amounts: list[str]) -> tuple[int, list[int]]:
    """造一张单 + 若干期（应收金额按传入顺序、到期日依次推后 10 天）。"""
    tag = f"{PREFIX}{uuid.uuid4().hex[:8]}"
    customer = Customer(name=tag, owner_id=1, status="active", level="A")
    session.add(customer)
    await session.flush()
    total = sum((Decimal(x) for x in plan_amounts), Decimal(0))
    order = SalesOrder(
        order_no=tag,
        customer_id=customer.id,
        total_amount=total,
        currency="CNY",
        status="pending",
        owner_id=1,
        created_by=1,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add(order)
    await session.flush()
    ids: list[int] = []
    for index, amount in enumerate(plan_amounts):
        plan = ReceivablePlan(
            order_id=order.id,
            plan_name=f"第{index + 1}期",
            due_date=date.today() + timedelta(days=10 * index),
            amount=Decimal(amount),
            currency="CNY",
            status="pending",
            created_at=datetime.now(UTC),
        )
        session.add(plan)
        await session.flush()
        ids.append(plan.id)
    return order.id, ids


async def add_payment(session, order_id: int, plan_id: int, amount: str) -> PaymentRecord:
    record = PaymentRecord(
        order_id=order_id,
        receivable_plan_id=plan_id,
        received_date=date.today() - timedelta(days=1),
        received_amount=Decimal(amount),
        currency="CNY",
        status="confirmed",
        confirmed_by=1,
        confirmed_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
    )
    session.add(record)
    await session.flush()
    return record


async def received(session, plan_id: int) -> Decimal:
    """某一期已分配到的金额。

    ⚠️ 返回 Decimal、**不要转成字符串再比**： 的字符串是 '0' 而不是
    '0.00'，按字符串比会出现实现是对的、断言却红的假失败（我第一版就是这么错的）。
    """
    return await payment_service.confirmed_amount(session, plan_id)


async def main() -> int:  # noqa: C901
    print("\n=== ① 超收按到期日从早到晚自动抵后续期 ===")
    async with SessionLocal() as session:
        order_id, (p1, p2) = await build_order(session, plan_amounts=["100", "200"])
        record = await add_payment(session, order_id, p1, "200")
        got = await payment_service.allocate_payment(session, record, operator_id=1)
        await session.flush()
        check("① 分配出去的总额 = 回款额", got, Decimal("200"))
        check("① 一期分到（等于它的应收，不超）", await received(session, p1), Decimal("100"))
        check("① 二期分到（超出的 100 抵过来）", await received(session, p2), Decimal("100"))
        await session.rollback()

    print("\n=== ② 全部应收填满后仍有余额 → 不硬塞给某一期 ===")
    async with SessionLocal() as session:
        order_id, (p1, p2) = await build_order(session, plan_amounts=["100", "100"])
        record = await add_payment(session, order_id, p1, "250")
        await payment_service.allocate_payment(session, record, operator_id=1)
        await session.flush()
        check("② 一期不超过自己的应收", await received(session, p1), Decimal("100"))
        check("② 二期不超过自己的应收", await received(session, p2), Decimal("100"))
        rows = (
            await session.execute(
                select(PaymentAllocation).where(PaymentAllocation.payment_id == record.id)
            )
        ).scalars().all()
        allocated = sum((Decimal(str(r.amount)) for r in rows), Decimal(0))
        check("② 只分出去 200，剩 50 挂待分配", allocated, Decimal("200"))
        await session.rollback()

    print("\n=== ③ ★ 人工把分配全挪走之后，原期不得再算这笔钱（兜底判据的坑） ===")
    async with SessionLocal() as session:
        order_id, (p1, p2) = await build_order(session, plan_amounts=["100", "200"])
        record = await add_payment(session, order_id, p1, "200")
        await payment_service.allocate_payment(session, record, operator_id=1)
        await session.flush()
        check("③ 挪之前：一期已收", await received(session, p1), Decimal("100"))
        # 人工整份替换：200 全给二期（模拟界面上的"调整分配"）
        await session.execute(
            payment_service.delete(PaymentAllocation).where(
                PaymentAllocation.payment_id == record.id
            )
        )
        session.add(
            PaymentAllocation(
                payment_id=record.id,
                plan_id=p2,
                amount=Decimal("200"),
                source="manual",
                created_by=1,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        await session.flush()
        check(
            "★③ 挪走之后一期必须是 0（否则就是双计：它一分没分到却显示已收）",
            await received(session, p1),
            Decimal("0"),
        )
        check("③ 二期拿到全部 200", await received(session, p2), Decimal("200"))
        await session.rollback()

    print("\n=== ④ 人工分配不被后续自动分配覆盖 ===")
    async with SessionLocal() as session:
        order_id, (p1, p2) = await build_order(session, plan_amounts=["100", "200"])
        record = await add_payment(session, order_id, p1, "200")
        await payment_service.allocate_payment(session, record, operator_id=1)
        await session.flush()
        # 人工把 200 全给二期
        await session.execute(
            payment_service.delete(PaymentAllocation).where(
                PaymentAllocation.payment_id == record.id
            )
        )
        session.add(
            PaymentAllocation(
                payment_id=record.id,
                plan_id=p2,
                amount=Decimal("200"),
                source="manual",
                created_by=1,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        await session.flush()
        # 再跑一次自动分配（模拟"跟单节点同步时的懒平衡"）
        await payment_service.allocate_payment(session, record, operator_id=1)
        await session.flush()
        check("④ 一期仍是 0（人工结论未被覆盖）", await received(session, p1), Decimal("0"))
        check("④ 二期仍是 200", await received(session, p2), Decimal("200"))
        await session.rollback()

    print("\n=== ⑤ 每期必须实际分到钱：收款节点按分配额判 ===")
    async with SessionLocal() as session:
        order_id, (p1, p2) = await build_order(session, plan_amounts=["100", "100"])
        record = await add_payment(session, order_id, p1, "200")
        await payment_service.allocate_payment(session, record, operator_id=1)
        await session.flush()
        await milestones_svc.ensure_initialized(session, order_id, None, created_by=1)
        await session.flush()
        # 两期加起来正好被这 200 填满 → 节点应完成
        await milestones_svc.sync_payment_milestones(session, order_id)
        await session.flush()
        node = (
            await session.execute(
                select(OrderMilestone).where(
                    OrderMilestone.order_id == order_id, OrderMilestone.node == "payment"
                )
            )
        ).scalar_one()
        check_true("⑤ 两期都被填满 → 收款节点完成", node.actual_date is not None, str(node.actual_date))
        # 把二期的钱挪回一期（一期应收只有 100，多出来的挂待分配）→ 二期就缺口了
        await session.execute(
            payment_service.delete(PaymentAllocation).where(
                PaymentAllocation.payment_id == record.id
            )
        )
        session.add(
            PaymentAllocation(
                payment_id=record.id,
                plan_id=p1,
                amount=Decimal("200"),
                source="manual",
                created_by=1,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        await session.flush()
        await milestones_svc.sync_payment_milestones(session, order_id)
        await session.flush()
        node = (
            await session.execute(
                select(OrderMilestone).where(
                    OrderMilestone.order_id == order_id, OrderMilestone.node == "payment"
                )
            )
        ).scalar_one()
        check_true(
            "★⑤ 二期没分到钱 → 收款节点必须退回未完成",
            node.actual_date is None,
            f"实际日={node.actual_date}（有值就是'总额够了就算收齐'的老毛病）",
        )
        await session.rollback()

    failed = [r for r in RESULTS if not r[1]]
    print()
    if failed:
        print(f"发现 {len(failed)} 项不符合预期：")
        for label, _ok, detail in failed:
            print(f"  - {label}：{detail}")
        return 1
    print(f"全部通过（{len(RESULTS)} 条）：超收按到期日抵后续期、"
          "每期必须实际分到钱、人工结论不被覆盖")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
