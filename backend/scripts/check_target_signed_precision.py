"""隔离PG/真实ASGI结账：签单/回款/复购汇总、明细和冻结读取必须保留到分。

只创建带随机账号的合成历史事实，保留以便复核，不清库、不覆盖既有期间。
"""
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
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import httpx
from sqlalchemy import select, text

from app.core.database import SessionLocal, engine
from app.core.deps import CurrentUser, get_current_user
from app.main import app
from app.modules.analytics.model import ActualSnapshot, ActualSnapshotItem, BasisSnapshot, SalesTarget
from app.modules.customer.model import Customer
from app.modules.order.model import SalesOrder
from app.modules.payment.model import PaymentRecord
from app.modules.user.model import User


async def unused_period(session, *, new_basis=False):
    for year in (range(2001, 2010) if new_basis else range(2010, 2020)):
        if new_basis:
            if await session.get(BasisSnapshot, year) is not None:
                continue
            existing = (await session.execute(text(
                "SELECT count(*) FROM sales_orders WHERE extract(year from created_at)=:y"
            ), {"y": year})).scalar_one()
            if existing:
                continue
        for month in range(1, 13):
            period = f"{year}-{month:02d}"
            counts = await session.execute(text(
                "SELECT (SELECT count(*) FROM analytics_actual_snapshots WHERE period=:p)"
                "+(SELECT count(*) FROM sales_targets WHERE period=:p)"
                "+(SELECT count(*) FROM sales_orders WHERE "
                "to_char(created_at AT TIME ZONE 'Asia/Shanghai','YYYY-MM')=:p)"
            ), {"p": period})
            if counts.scalar_one() == 0:
                return period, datetime(year, month, 15, 4, tzinfo=UTC)
    raise AssertionError("没有可用的空历史期间，拒绝覆盖既有数据")


async def check_freeze_limits():
    """真实PG/ASGI：合法上限可结账，超限的首次结账/重算不留半份存档。"""
    async with SessionLocal() as session:
        period, at = await unused_period(session)
        user = User(username="iso-limit-"+uuid4().hex, name="隔离结账范围", password_hash="!", status="active")
        session.add(user)
        await session.flush()
        actor = CurrentUser(user, {"settings:manage", "analytics:view"}, ["admin"], "all")
        customer = Customer(name="隔离结账范围客户", owner_id=user.id, created_at=at)
        session.add(customer)
        await session.flush()
        order = SalesOrder(order_no="ISO-"+uuid4().hex[:20], customer_id=customer.id,
                           owner_id=user.id, sales_owner_id=user.id, currency="CNY", status="pending",
                           total_amount=Decimal("99999999999999.99"), created_at=at)
        session.add(order)
        await session.commit()
    app.dependency_overrides[get_current_user] = lambda: actor

    async def stored_state(p):
        async with SessionLocal() as verify:
            summaries = (await verify.execute(text(
                "SELECT scope_key,metric,actual_value,metric_basis_version,frozen_at,frozen_by,note "
                "FROM analytics_actual_snapshots WHERE period=:p ORDER BY scope_key,metric"
            ), {"p": p})).all()
            details = (await verify.execute(text(
                "SELECT id,metric,record_id,amount,metric_basis_version,frozen_at,frozen_by "
                "FROM analytics_actual_snapshot_items WHERE period=:p ORDER BY id"
            ), {"p": p})).all()
        return summaries, details

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://isolated") as client:
        r = await client.post("/api/v1/sales-targets/actuals/freeze", params={"period":period})
        assert r.status_code == 200, r.text
        original = await stored_state(period)
        assert any(row.actual_value == Decimal("99999999999999.99") for row in original[0])
        async with SessionLocal() as session:
            extra = SalesOrder(order_no="ISO-"+uuid4().hex[:20], customer_id=customer.id,
                               owner_id=user.id, sales_owner_id=user.id, currency="CNY", status="pending",
                               total_amount=Decimal("0.01"), created_at=at)
            session.add(extra)
            await session.commit()
        r = await client.post("/api/v1/sales-targets/actuals/refreeze", params={"period":period,"reason":"隔离超限重算验证"})
        assert r.status_code == 422 and "金额超出可保存范围" in r.json()["message"], r.text
        assert await stored_state(period) == original, "超限重算不能改写、删除或重建旧存档"
        async with SessionLocal() as session:
            extra_row = await session.get(SalesOrder, extra.id)
            extra_row.status = "cancelled"  # 仅修改本测试合成事实，回到合法上限。
            fresh_period, fresh_at = await unused_period(session)
            session.add_all([SalesOrder(order_no="ISO-"+uuid4().hex[:20], customer_id=customer.id,
                                         owner_id=user.id, sales_owner_id=user.id, currency="CNY", status="pending",
                                         total_amount=amount, created_at=fresh_at)
                             for amount in (Decimal("99999999999999.99"), Decimal("0.01"))])
            await session.commit()
        r = await client.post("/api/v1/sales-targets/actuals/freeze", params={"period":fresh_period})
        assert r.status_code == 422 and fresh_period in r.json()["message"], r.text
        assert await stored_state(fresh_period) == ([], []), "首次结账超限不得留下部分存档"
        assert await stored_state(period) == original
        r = await client.post("/api/v1/sales-targets/actuals/refreeze", params={"period":period,"reason":"隔离合法上限恢复验证"})
        assert r.status_code == 200, r.text
        assert any(row.actual_value == Decimal("99999999999999.99") for row in (await stored_state(period))[0])
    print(f"PASS limits {period}/{fresh_period}: maximum accepted; overflow 422; previous snapshots unchanged", flush=True)


async def main():
    previous = app.dependency_overrides.get(get_current_user)
    try:
        for metric, amounts in [(metric, amounts) for metric in ("signed", "received", "repeat_net")
                                for amounts in [("99999999999999.99",), ("1.01", "2.02")]]:
            summary_metric = "sales" if metric == "signed" else metric
            exact_field = "repeat_customer_actual_exact" if metric == "repeat_net" else summary_metric + "_actual_exact"
            expected = sum(map(Decimal, amounts), Decimal(0))
            async with SessionLocal() as session:
                period, at = await unused_period(session, new_basis=metric == "repeat_net")
                user = User(username="iso-signed-" + uuid4().hex, name="隔离签单精度",
                            password_hash="!", status="active")
                session.add(user)
                await session.flush()
                actor = CurrentUser(user, {"settings:manage", "analytics:view"}, ["admin"], "all")
                customer = Customer(name="隔离签单客户", owner_id=user.id,
                                    created_at=at - timedelta(days=400 if metric == "repeat_net" else 1))
                session.add(customer)
                await session.flush()
                orders = [SalesOrder(order_no="ISO-" + uuid4().hex[:20],
                                    customer_id=customer.id, owner_id=user.id,
                                    sales_owner_id=user.id, currency="CNY", status="pending",
                                    total_amount=Decimal(amount), created_at=at)
                          for amount in amounts]
                session.add_all(orders)
                await session.flush()
                if metric == "received":
                    session.add_all([
                        PaymentRecord(order_id=order.id, received_date=at.date(),
                                      received_amount=order.total_amount, currency="CNY",
                                      status="confirmed", confirmed_at=at, confirmed_by=user.id,
                                      created_by=user.id, created_at=at)
                        for order in orders
                    ])
                if metric == "repeat_net":
                    session.add(SalesOrder(order_no="ISO-CANCEL-"+uuid4().hex[:16], customer_id=customer.id,
                                           owner_id=user.id, sales_owner_id=user.id, currency="CNY", status="cancelled",
                                           total_amount=Decimal("7.77"), created_at=at))
                    session.add(SalesOrder(order_no="ISO-"+uuid4().hex[:20], customer_id=customer.id,
                                           owner_id=user.id, sales_owner_id=user.id, currency="CNY", status="pending",
                                           total_amount=Decimal(1), created_at=datetime(at.year-1, 12, 15, 4, tzinfo=UTC)))
                session.add(SalesTarget(period=period, user_id=None, department_id=None,
                                        sales_target=Decimal("1.49"), repeat_customer_target=Decimal("1.49"),
                                        new_customer_target=0))
                await session.commit()
                ids = [order.id for order in orders]
            reader = CurrentUser(user, {"analytics:view", "customer:view"}, ["salesperson"], "self")
            app.dependency_overrides[get_current_user] = lambda: actor
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://isolated") as client:
                app.dependency_overrides[get_current_user] = lambda: reader
                live = await client.get("/api/v1/sales-targets", params={"year": int(period[:4])})
                assert live.status_code == 200, live.text
                for uid in (actor.id, None):
                    live_row = next(item for item in live.json()["data"]["rows"]
                                    if item["period"] == period and item.get("user_id") == uid)
                    assert Decimal(live_row[exact_field]) == expected
                    target_value = Decimal("1.49") if uid is None else Decimal(0)
                    assert Decimal(live_row["sales_variance_exact"]) == (expected if metric == "received" else 0) - target_value
                    assert Decimal(live_row["repeat_customer_variance_exact"]) == (expected if metric == "repeat_net" else 0) - target_value
                    assert not live_row["actual_frozen"]
                annual = await client.get("/api/v1/sales-targets/bases", params={"year": int(period[:4])})
                assert annual.status_code == 200, annual.text
                point = next(item for item in annual.json()["data"][metric] if item["month"] == period[5:])
                assert Decimal(point["value_exact"]) == expected
                sales_stats = await client.get("/api/v1/analytics/sales-users")
                assert sales_stats.status_code == 200, sales_stats.text
                performance = next(item for item in sales_stats.json()["data"] if item["user_id"] == actor.id)
                assert Decimal(performance["order_amount_exact"]) == expected + (Decimal(1) if metric == "repeat_net" else 0)
                assert Decimal(performance["received_amount_exact"]) == (expected if metric == "received" else 0)
                live_detail = await client.get("/api/v1/sales-targets/drilldown", params={
                    "period": period, "metric": metric, "user_id": actor.id,
                })
                assert live_detail.status_code == 200, live_detail.text
                detail = live_detail.json()["data"]
                assert detail["source"] == "live" and Decimal(detail["total_exact"]) == expected
                assert sum((Decimal(item["amount_exact"]) for item in detail["items"]), Decimal(0)) == expected
                app.dependency_overrides[get_current_user] = lambda: actor
                r = await client.post("/api/v1/sales-targets/actuals/freeze", params={"period": period})
                assert r.status_code == 200, r.text
                async with SessionLocal() as verify:
                    snap = await verify.get(ActualSnapshot, (period, f"user:{actor.id}", summary_metric))
                    assert snap is not None and snap.actual_value == expected, (period, snap.actual_value, expected)
                    company = await verify.get(ActualSnapshot, (period, "company", summary_metric))
                    assert company is not None and company.actual_value == expected
                    rows = (await verify.execute(select(ActualSnapshotItem).where(
                        ActualSnapshotItem.period == period, ActualSnapshotItem.metric == metric,
                    ))).scalars().all()
                    if metric in ("signed", "repeat_net"):
                        assert {row.record_id for row in rows} == set(ids)
                    else:
                        payments = (await verify.execute(select(PaymentRecord).where(
                            PaymentRecord.order_id.in_(ids),
                        ))).scalars().all()
                        assert {row.record_id for row in rows} == {pay.id for pay in payments}
                        assert sum((pay.received_amount for pay in payments), Decimal(0)) == expected
                    assert sum((row.amount for row in rows), Decimal(0)) == expected
                    originals = (await verify.execute(select(SalesOrder.total_amount).where(
                        SalesOrder.id.in_(ids),
                    ))).scalars().all()
                    assert sum(originals, Decimal(0)) == expected
                app.dependency_overrides[get_current_user] = lambda: reader
                r = await client.get("/api/v1/sales-targets", params={"year": int(period[:4])})
                assert r.status_code == 200, r.text
                row = next(item for item in r.json()["data"]["rows"]
                           if item["period"] == period and item.get("user_id") == actor.id)
                assert Decimal(row[exact_field]) == expected
                assert row["actual_frozen"]
                company_row = next(item for item in r.json()["data"]["rows"]
                                   if item["period"] == period and item.get("user_id") is None)
                assert Decimal(company_row[exact_field]) == expected
                r = await client.get("/api/v1/sales-targets/drilldown", params={
                    "period": period, "metric": metric, "user_id": actor.id,
                })
                assert r.status_code == 200, r.text
                data = r.json()["data"]
                assert data["source"] == "snapshot" and Decimal(data["total_exact"]) == expected
                assert sum((Decimal(item["amount_exact"]) for item in data["items"]), Decimal(0)) == expected
                assert isinstance(data["total"], (float, int)), "旧JSON数字字段保持兼容"
            print(f"PASS {metric} {period}: source, stored summary/items and snapshot reads = {expected}", flush=True)
        await check_freeze_limits()
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_current_user, None)
        else:
            app.dependency_overrides[get_current_user] = previous
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
