"""隔离PG：发货明细不去重，逐行到分，汇总/下钻/结账保持一致。"""
import os
from urllib.parse import urlsplit

from _test_support import require_isolated_db

db_name = require_isolated_db()
db_url = urlsplit(os.environ["DATABASE_URL"])
if not db_url.scheme.startswith("postgresql") or db_url.hostname not in ("127.0.0.1", "localhost") or not (
    db_name.startswith(("crm_iso", "crm_check", "crm_test")) or db_name.endswith("_test")
):
    raise SystemExit("只允许本机一次性隔离数据库")
os.environ.update(
    WECOM_PUSH_OFF="1", DINGTALK_PUSH_OFF="1", SCHEDULER_ENABLED="false",
    DINGTALK_OA_STREAM_ENABLED="false", SCHEDULER_OA_SYNC_ENABLED="false",
)

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import httpx
from sqlalchemy import select, text

from app.core.database import SessionLocal, engine
from app.core.money import line_amount
from app.core.deps import CurrentUser, get_current_user
from app.main import app
from app.modules.analytics.model import ActualSnapshot, ActualSnapshotItem, SalesTarget
from app.modules.customer.model import Customer
from app.modules.order.model import SalesOrder, SalesOrderItem, OrderShipmentBatch, OrderShipmentBatchItem
from app.modules.user.model import User


async def main():
    previous = app.dependency_overrides.get(get_current_user)
    try:
        cases = [
            (("10", "10"), ("1", "1"), False),
            (("10", "11"), ("1", "1"), False),
            (("10", "10"), ("1", "1"), True),
            (("0.015", "0.005"), ("1", "3"), False),
            (("0.015", "0.005"), ("1", "3"), True),
            (("999999999999.9999",), ("100",), False),
        ]
        for prices, quantities, split in cases:
            expected = sum((line_amount(Decimal(qty), Decimal(price))
                            for price,qty in zip(prices, quantities)), Decimal(0))
            async with SessionLocal() as session:
                for year, month in ((year, month) for year in range(2013, 2019) for month in range(1, 13)):
                    period = f"{year}-{month:02d}"
                    count = (await session.execute(text(
                        "SELECT (SELECT count(*) FROM analytics_actual_snapshots WHERE period=:p)"
                        "+(SELECT count(*) FROM sales_targets WHERE period=:p)"
                        "+(SELECT count(*) FROM order_shipment_batches WHERE to_char(actual_ship_date,'YYYY-MM')=:p)"
                        "+(SELECT count(*) FROM sales_orders WHERE to_char(created_at AT TIME ZONE 'Asia/Shanghai','YYYY-MM')=:p)"
                        "+(SELECT count(*) FROM payment_records WHERE to_char(confirmed_at AT TIME ZONE 'Asia/Shanghai','YYYY-MM')=:p)"
                    ), {"p": period})).scalar_one()
                    if not count:
                        break
                else:
                    raise AssertionError("没有空隔离期间，拒绝覆盖")
                at = datetime(year, month, 15, 4, tzinfo=UTC)
                user = User(username="iso-ship-"+uuid4().hex, name="隔离发货汇总", password_hash="!", status="active")
                session.add(user)
                await session.flush()
                actor = CurrentUser(user, {"settings:manage", "analytics:view"}, ["admin"], "all")
                reader = CurrentUser(user, {"analytics:view", "customer:view"}, ["salesperson"], "self")
                customer = Customer(name="隔离发货明细客户", owner_id=user.id, created_at=at)
                session.add(customer)
                await session.flush()
                order = SalesOrder(order_no="ISO-"+uuid4().hex[:20], customer_id=customer.id,
                                   owner_id=user.id, sales_owner_id=user.id, currency="CNY", status="shipped",
                                   total_amount=Decimal(expected), created_at=at)
                session.add(order)
                await session.flush()
                batches = [OrderShipmentBatch(order_id=order.id, batch_no=i+1, status="shipped",
                                               actual_ship_date=at.date(), created_at=at)
                           for i in range(2 if split else 1)]
                lines = [SalesOrderItem(order_id=order.id, quantity=Decimal(qty),
                                        unit_price=Decimal(price), amount=line_amount(Decimal(qty), Decimal(price)))
                         for price,qty in zip(prices, quantities)]
                session.add_all(batches+lines)
                await session.flush()
                session.add_all([OrderShipmentBatchItem(batch_id=batches[i if split else 0].id,
                                                        order_item_id=line.id, planned_qty=line.quantity,
                                                        shipped_qty=line.quantity) for i,line in enumerate(lines)])
                session.add(SalesTarget(period=period, sales_target=Decimal(0),
                                        repeat_customer_target=Decimal(0), new_customer_target=0))
                await session.commit()
                batch_ids = {batch.id for batch in batches}
            app.dependency_overrides[get_current_user] = lambda: reader
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://isolated") as client:
                for frozen in (False, True):
                    if frozen:
                        app.dependency_overrides[get_current_user] = lambda: actor
                        result = await client.post("/api/v1/sales-targets/actuals/freeze", params={"period":period})
                        assert result.status_code == 200, result.text
                        app.dependency_overrides[get_current_user] = lambda: reader
                    report = await client.get("/api/v1/sales-targets", params={"year":year})
                    assert report.status_code == 200, report.text
                    for uid in (user.id, None):
                        row = next(r for r in report.json()["data"]["rows"] if r["period"]==period and r.get("user_id")==uid)
                        assert Decimal(row["shipped_actual_exact"]) == expected, (prices, split, frozen, row)
                        if expected < Decimal("1000000000000"):
                            assert Decimal(str(row["shipped_actual"])) == expected
                        assert row["actual_frozen"] is frozen
                    annual = await client.get("/api/v1/sales-targets/bases", params={"year":year})
                    assert annual.status_code == 200, annual.text
                    point = next(item for item in annual.json()["data"]["shipped"] if item["month"] == period[5:])
                    assert Decimal(point["value_exact"]) == expected
                    if expected < Decimal("1000000000000"):
                        assert Decimal(str(point["value"])) == expected
                    detail = await client.get("/api/v1/sales-targets/drilldown", params={"period":period,"metric":"shipped","user_id":user.id})
                    assert detail.status_code == 200, detail.text
                    data = detail.json()["data"]
                    assert Decimal(data["total_exact"]) == expected and data["count"] == len(batches), data
                    assert sum((Decimal(item["amount_exact"]) for item in data["items"]), Decimal(0)) == expected
                    if expected < Decimal("1000000000000"):
                        assert Decimal(str(data["total"])) == expected
                    assert isinstance(data["total"], (int, float))
                    assert {r["id"] for r in data["items"]} == batch_ids
                async with SessionLocal() as verify:
                    for key in (f"user:{user.id}", "company"):
                        snapshot = await verify.get(ActualSnapshot, (period, key, "shipped"))
                        assert snapshot.actual_value == Decimal(expected)
                    items = (await verify.execute(select(ActualSnapshotItem).where(
                        ActualSnapshotItem.period==period, ActualSnapshotItem.metric=="shipped",
                    ))).scalars().all()
                    assert sum((row.amount for row in items), Decimal(0)) == Decimal(expected)
            print(f"PASS {period} prices={prices} split={split}: live/frozen personal/company and details={expected}", flush=True)
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_current_user,None)
        else:
            app.dependency_overrides[get_current_user] = previous
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
