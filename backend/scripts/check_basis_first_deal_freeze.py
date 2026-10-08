"""新客「首次成交日期」与归属月份一起冻结（返修 R07）。

**只在隔离库跑**：库名必须含 test（或 CI=true）。

## 这条修的是什么

新客的**归属月份**早就冻在基准快照里了，但明细上的"首次成交日期"是每次打开
报表**实时重算**的（取该客户当前最早的非取消订单）。于是原首单被取消后：

- 月份：还冻在 **2020-03**（快照，不变）；
- 日期：重算跳到了下一张单 **2020-06-10**。

同一行里两个数互相打架，而且事后无从解释当初那一版是怎么算的。修法是让日期与
来源订单**跟着月份一起冻**；旧版快照没冻日期就如实显示"未知"，**不回头实时重算顶上**。

## 怎么验

用一个很久以前的年份（2020）造快照，避开别的套件用的年份：

1. 造一张 2020-03 的订单 → 冻结 2020 年基准 → 快照里记下日期与来源订单；
2. **取消**那张订单，再造一张 2020-06 的订单（让"实时重算"会跳到 6 月）；
3. 读快照：仍是 2020-03 / 那个日期（冻结语义，本就不该变）；
4. 调下钻：3 月明细的日期**取快照那一天**（旧写法会给 6 月那天）；
5. 把快照的日期段清掉（模拟本字段上线前冻的老快照）→ 下钻日期为**空**
   （界面显示"未记录"），**不是**重算出来的 6 月那天。

跑法（需要后端在跑）：
    cd backend
    API_BASE=http://127.0.0.1:8001/api/v1 DATABASE_URL=...crm_sales_agent_test \\
      PYTHONPATH=. .venv/bin/python scripts/check_basis_first_deal_freeze.py
"""

import asyncio
import os
from datetime import datetime
from urllib.parse import urlparse

from sqlalchemy import select, text
from _test_support import require_isolated_db

require_isolated_db()

from app.core.config import settings
from app.core.database import SessionLocal
from scripts.check_review_regressions import BASE, call, login

YEAR = 2020
FIRST_AT = "2020-03-15T10:00:00+00:00"
LATER_AT = "2020-06-10T10:00:00+00:00"
FAILURES: list[str] = []
CREATED_ORDERS: list[int] = []


def check(label: str, condition: object, expected: object = True) -> None:
    ok = condition == expected
    print(f'  {"OK  " if ok else "FAIL"} {label}：{condition!r}' + ("" if ok else f"（应为 {expected!r}）"))
    if not ok:
        FAILURES.append(label)


def check_true(label: str, condition: bool, detail: str = "") -> None:
    print(f'  {"OK  " if condition else "FAIL"} {label}' + (f"：{detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


async def make_order(token: str, sku_id: int, customer_id: int) -> int:
    status, res = call(
        "POST", "/orders", token=token,
        body={
            "customer_id": customer_id,
            "delivery_date": "2020-12-31",
            "items": [{"sku_id": sku_id, "quantity": 10, "unit_price": 100}],
        },
    )
    assert status == 200, res
    order_id = res["data"]["order_id"]
    CREATED_ORDERS.append(order_id)
    return order_id


async def stamp(order_id: int, when_iso: str) -> None:
    """把订单的创建时间挪到指定的过去时刻（我们要它落在 2020 年）。

    ⚠️ 必须传 `datetime` 对象：`created_at` 是带时区的 timestamp 列，
    直接绑字符串 asyncpg 会拒（`expected a datetime... got 'str'`）。
    """
    async with SessionLocal() as session:
        await session.execute(
            text("update sales_orders set created_at = :w where id = :i"),
            {"w": datetime.fromisoformat(when_iso), "i": order_id},
        )
        await session.commit()


async def snapshot_of(year: int) -> dict:
    async with SessionLocal() as session:
        row = (
            await session.execute(
                text(
                    "select first_deal_month, first_deal_detail "
                    "from analytics_basis_snapshots where year = :y"
                ),
                {"y": year},
            )
        ).first()
    if row is None:
        return {}
    return {"month": row[0] or {}, "detail": row[1] or {}}


def new_customer_item(items: list[dict], customer_id: int) -> dict | None:
    return next(
        (x for x in items if x.get("record_type") == "customer" and x.get("id") == customer_id),
        None,
    )


async def cleanup() -> None:
    ids = ",".join(str(x) for x in CREATED_ORDERS) or "0"
    statements = [
        f"delete from order_shipment_batch_items where batch_id in (select id from order_shipment_batches where order_id in ({ids}))",
        f"delete from order_shipment_batches where order_id in ({ids})",
        f"delete from sales_order_items where order_id in ({ids})",
        f"delete from receivable_plans where order_id in ({ids})",
        f"delete from order_status_history where order_id in ({ids})",
        f"delete from order_milestones where order_id in ({ids})",
        f"delete from sales_orders where id in ({ids})",
        f"delete from analytics_basis_snapshots where year = {YEAR}",
    ]
    async with SessionLocal() as session:
        for sql in statements:
            try:
                await session.execute(text(sql))
            except Exception as error:  # noqa: BLE001 - 清理尽力而为
                print(f"    （清理跳过一句：{type(error).__name__}: {error}）")
                await session.rollback()
        await session.commit()


async def main() -> int:
    import app.main
    _ = app.main

    assert urlparse(BASE).hostname in {"127.0.0.1", "localhost", "::1"}, BASE
    db = urlparse(settings.database_url)
    assert db.hostname in {"127.0.0.1", "localhost", "::1"}, db
    assert "test" in db.path.lower() or os.getenv("CI") == "true", db

    admin = login("admin", "admin123")

    from app.modules.customer.model import Customer

    async with SessionLocal() as session:
        customer = (
            await session.execute(
                select(Customer).where(Customer.deleted_at.is_(None)).order_by(Customer.id).limit(1)
            )
        ).scalars().first()
    assert customer is not None, "隔离库要先跑 scripts/seed.py"
    cid = customer.id
    key = str(cid)

    status, res = call("GET", "/pricing/sku-options", token=admin)
    sku_id = res["data"][0]["id"]

    try:
        print(f"=== 1. 造一张 {FIRST_AT[:7]} 的订单，冻成 {YEAR} 年基准 ===")
        first_order = await make_order(admin, sku_id, cid)
        await stamp(first_order, FIRST_AT)
        status, res = call("POST", f"/sales-targets/bases/refreeze?year={YEAR}", token=admin)
        check("冻结该年基准", status, 200)

        snap = await snapshot_of(YEAR)
        check("快照记下归属月", snap.get("month", {}).get(key), "2020-03")
        entry = (snap.get("detail") or {}).get(key) or {}
        check_true("快照把首次成交日期一起冻了",
                   str(entry.get("at", "")).startswith("2020-03-15"), str(entry))
        check("并记下了来源订单", entry.get("order_id"), first_order)

        print("=== 2. 取消原首单，再造一张 6 月的单（让「实时重算」会跳到 6 月）===")
        async with SessionLocal() as session:
            await session.execute(
                text("update sales_orders set status = 'cancelled' where id = :i"),
                {"i": first_order},
            )
            await session.commit()
        later_order = await make_order(admin, sku_id, cid)
        await stamp(later_order, LATER_AT)

        print("=== 3. 快照自身不受影响（冻结语义）===")
        snap = await snapshot_of(YEAR)
        check("归属月仍是 2020-03", (snap.get("month") or {}).get(key), "2020-03")
        entry = (snap.get("detail") or {}).get(key) or {}
        check_true("日期仍是原来那天",
                   str(entry.get("at", "")).startswith("2020-03-15"), str(entry.get("at")))
        check("来源订单还是原来那张", entry.get("order_id"), first_order)

        print("=== 4. 下钻明细的日期取快照，不是重算（R07 的核心判据）===")
        status, res = call(
            "GET", "/sales-targets/drilldown?period=2020-03&metric=new_customer", token=admin
        )
        check("下钻接口可用", status, 200)
        items = (res.get("data") or {}).get("items") or []
        mine = new_customer_item(items, cid)
        check_true("3 月的明细里有这个客户", mine is not None, str(items)[:200])
        if mine is not None:
            check_true(
                "日期是**冻住的那天**（旧写法这里会给 2020-06-10）",
                str(mine.get("date") or "").startswith("2020-03-15"),
                str(mine.get("date")),
            )

        print("=== 5. 老快照（没冻日期）如实说「未知」，不拿重算的顶上 ===")
        async with SessionLocal() as session:
            await session.execute(
                text("update analytics_basis_snapshots set first_deal_detail = null where year = :y"),
                {"y": YEAR},
            )
            await session.commit()
        status, res = call(
            "GET", "/sales-targets/drilldown?period=2020-03&metric=new_customer", token=admin
        )
        items = (res.get("data") or {}).get("items") or []
        mine = new_customer_item(items, cid)
        check_true("客户仍在那个月的明细里（归属月没变）", mine is not None, str(items)[:160])
        if mine is not None:
            check_true(
                "但日期是空的（旧写法这里会给重算出来的 2020-06-10）",
                not mine.get("date"),
                repr(mine.get("date")),
            )

    finally:
        await cleanup()
        print()
        print(f"（已清理夹具：订单 {CREATED_ORDERS}、{YEAR} 年基准快照）")

    print()
    if FAILURES:
        print(f"FAILED {len(FAILURES)} 项：" + "、".join(FAILURES))
        return 1
    print("OK 新客首次成交：日期与来源订单跟归属月一起冻；老快照如实说「未知」")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(asyncio.run(main()))
