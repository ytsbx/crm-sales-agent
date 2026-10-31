"""本地隔离PG：三层部门/外部门五指标在实时和无源存档下遵守原范围。"""
import os
import sys
sys.path.insert(0,"scripts")
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
from sqlalchemy import text

from app.core.database import SessionLocal, engine
from app.core.deps import CurrentUser, get_current_user
from app.main import app
from app.modules.analytics.model import SalesTarget, BasisSnapshot
from app.modules.customer.model import Customer
from app.modules.order.model import SalesOrder, SalesOrderItem, OrderShipmentBatch, OrderShipmentBatchItem
from app.modules.payment.model import PaymentRecord
from app.modules.user.model import Department, User


async def main():
    previous = app.dependency_overrides.get(get_current_user)
    try:
        async with SessionLocal() as session:
            for year in range(1990, 2025):
                if await session.get(BasisSnapshot, year) is not None:
                    continue
                period = f"{year}-02"
                count = (await session.execute(text(
                    "SELECT (SELECT count(*) FROM analytics_actual_snapshots WHERE period=:p)"
                    "+(SELECT count(*) FROM sales_targets WHERE period=:p)"
                    "+(SELECT count(*) FROM sales_orders WHERE to_char(created_at AT TIME ZONE 'Asia/Shanghai','YYYY-MM')=:p)"
                    "+(SELECT count(*) FROM payment_records WHERE to_char(confirmed_at AT TIME ZONE 'Asia/Shanghai','YYYY-MM')=:p)"
                    "+(SELECT count(*) FROM order_shipment_batches WHERE to_char(actual_ship_date,'YYYY-MM')=:p)"
                ), {"p": period})).scalar_one()
                if not count:
                    break
            else:
                raise AssertionError("没有空隔离期间，拒绝覆盖")
            at = datetime(year, 2, 15, 4, tzinfo=UTC)
            departments=[]
            for index in range(4):
                department=Department(name="隔离精确范围"+uuid4().hex[:8], status="active",
                                      parent_id=departments[index-1].id if index in (1,2) else None)
                session.add(department)
                await session.flush()
                departments.append(department)
            users=[]
            orders=[]
            amounts=list(map(Decimal,("10.11","20.22","30.33","40.44")))
            for department,amount in zip(departments,amounts):
                user=User(username="iso-scope-"+uuid4().hex,name="隔离范围用户",password_hash="!",
                          status="active",department_id=department.id)
                session.add(user)
                await session.flush()
                customer=Customer(name="隔离范围客户",owner_id=user.id,created_at=at)
                session.add(customer)
                await session.flush()
                order=SalesOrder(order_no="ISO-"+uuid4().hex[:20],customer_id=customer.id,
                                 owner_id=user.id,sales_owner_id=user.id,currency="CNY",status="pending",
                                 total_amount=amount,created_at=at)
                session.add(order)
                await session.flush()
                line=SalesOrderItem(order_id=order.id,quantity=Decimal(1),unit_price=amount,amount=amount)
                batch=OrderShipmentBatch(order_id=order.id,batch_no=1,status="shipped",actual_ship_date=at.date(),created_at=at)
                session.add_all([line,batch])
                await session.flush()
                session.add(OrderShipmentBatchItem(batch_id=batch.id,order_item_id=line.id,planned_qty=Decimal(1),shipped_qty=Decimal(1)))
                session.add(PaymentRecord(order_id=order.id,received_date=at.date(),received_amount=amount,currency="CNY",status="confirmed",confirmed_at=at,created_at=at))
                session.add(SalesOrder(order_no="ISO-"+uuid4().hex[:20],customer_id=customer.id,owner_id=user.id,
                                       sales_owner_id=user.id,currency="CNY",status="pending",total_amount=Decimal(1),
                                       created_at=datetime(year-1,12,15,4,tzinfo=UTC)))
                newcomer=Customer(name="隔离首单客户",owner_id=user.id,created_at=at)
                session.add(newcomer)
                await session.flush()
                new_order=SalesOrder(order_no="ISO-"+uuid4().hex[:20],customer_id=newcomer.id,owner_id=user.id,
                                     sales_owner_id=user.id,currency="CNY",status="pending",total_amount=Decimal(1),created_at=at)
                session.add(new_order)
                orders.append(new_order)
                users.append(user)
                orders.append(order)
                session.add(SalesTarget(period=period,user_id=user.id,sales_target=Decimal(1),
                                        new_customer_target=0,repeat_customer_target=Decimal(0)))
                session.add(SalesTarget(period=period,department_id=department.id,sales_target=Decimal(1),
                                        new_customer_target=0,repeat_customer_target=Decimal(0)))
            session.add(SalesTarget(period=period,sales_target=Decimal(1),
                                    new_customer_target=0,repeat_customer_target=Decimal(0)))
            await session.commit()
        async def stored_snapshot():
            async with SessionLocal() as verify:
                return (await verify.execute(text(
                    "SELECT scope_key,metric,actual_value,metric_basis_version,frozen_at,frozen_by "
                    "FROM analytics_actual_snapshots WHERE period=:p ORDER BY scope_key,metric"
                ), {"p": period})).all()
        root=users[0]
        permissions={"customer:view","analytics:view","settings:manage"}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://isolated") as client:
            for frozen in (False,True):
                if frozen:
                    app.dependency_overrides[get_current_user]=lambda:CurrentUser(root,permissions,["admin"],"all")
                    response=await client.post("/api/v1/sales-targets/actuals/freeze",params={"period":period})
                    assert response.status_code==200,response.text
                    original_snapshot=await stored_snapshot()
                    assert any(row.scope_key==f"dept:{departments[0].id}" and row.metric=="sales" and row.actual_value==Decimal("63.66") for row in original_snapshot)
                    # 源事实撤销后只剩存档；不改生产/演示/非本套件订单。
                    async with SessionLocal() as session:
                        for order in orders:
                            current=await session.get(SalesOrder,order.id)
                            current.status="cancelled"
                        await session.commit()
                for scope,visible in (("self",{0}),("department",{0}),("department_and_sub",{0,1,2}),("all",{0,1,2,3})):
                    actor=CurrentUser(root,permissions,["salesperson"],scope)
                    app.dependency_overrides[get_current_user]=lambda:actor
                    response=await client.get("/api/v1/sales-targets",params={"year":year})
                    assert response.status_code==200,response.text
                    rows=[row for row in response.json()["data"]["rows"] if row["period"]==period]
                    allowed_users={users[i].id for i in visible}
                    allowed_depts=set() if scope=="self" else {departments[i].id for i in visible}
                    assert {row["user_id"] for row in rows if row.get("user_id") is not None}==allowed_users
                    assert {row["department_id"] for row in rows if row.get("department_id") is not None}==allowed_depts
                    company=[row for row in rows if row.get("user_id") is None and row.get("department_id") is None]
                    assert len(company)==1 and Decimal(company[0]["sales_actual_exact"])==sum(amounts)+len(amounts)
                    for row in rows:
                        assert row["actual_frozen"] is frozen
                        if row.get("user_id") is not None:
                            index=next(i for i,u in enumerate(users) if u.id==row["user_id"])
                            assert Decimal(row["sales_actual_exact"])==amounts[index]+1
                    for team in [row for row in rows if row.get("department_id") is not None]:
                        department_index=next(i for i,d in enumerate(departments) if d.id==team["department_id"])
                        member_indices=({i for i in range(department_index,3)} if department_index<3 else {3}) & visible
                        for metric,field in (("signed","sales_actual_exact"),("received","received_actual_exact"),
                                             ("shipped","shipped_actual_exact"),("repeat_net","repeat_customer_actual_exact"),
                                             ("new_customer","new_customer_actual")):
                            expected=sum((amounts[i]+(1 if metric=="signed" else 0) for i in member_indices),Decimal(0)) if metric!="new_customer" else len(member_indices)
                            assert Decimal(str(team[field]))==expected,(scope,frozen,metric,team[field],expected)
                            team_detail=await client.get("/api/v1/sales-targets/drilldown",params={"period":period,"metric":metric,"department_id":team["department_id"]})
                            assert team_detail.status_code==200,team_detail.text
                            data=team_detail.json()["data"]
                            if metric=="new_customer":
                                assert data["count"]==expected
                            else:
                                assert Decimal(data["total_exact"])==expected
                            assert {item["owner_id"] for item in data["items"]}=={users[i].id for i in member_indices}
                    # 下钻始终按原数据范围；公司汇总不开放公司全部单据。
                    detail=await client.get("/api/v1/sales-targets/drilldown",params={"period":period,"metric":"signed"})
                    assert detail.status_code==200,detail.text
                    data=detail.json()["data"]
                    assert data["source"]==("snapshot" if frozen else "live")
                    assert {item["id"] for item in data["items"]}=={order.id for order in orders if order.owner_id in allowed_users}
                    assert Decimal(data["total_exact"])==sum(amounts[i]+1 for i in visible)
                    for index in range(4):
                        r=await client.get("/api/v1/sales-targets/drilldown",params={"period":period,"metric":"signed","user_id":users[index].id})
                        assert r.status_code==(200 if index in visible else 403),(scope,index,r.text)
                        r=await client.get("/api/v1/sales-targets/drilldown",params={"period":period,"metric":"signed","department_id":departments[index].id})
                        assert r.status_code==(200 if departments[index].id in allowed_depts else 403),(scope,index,r.text)
                    print(f"PASS {period} {scope} frozen={frozen}: exact rows/detail filtered, unauthorized owner/department denied",flush=True)
        assert await stored_snapshot()==original_snapshot, "按查看范围读取不得改写完整原存档"
        print("PASS all five frozen metrics match permitted team members after synthetic source cancellation",flush=True)
        async with SessionLocal() as session:
            missing_user=(await session.execute(text("SELECT greatest(coalesce((SELECT max(id) FROM users),0),coalesce((SELECT max(user_id) FROM sales_targets),0))+100000"))).scalar_one()
            missing_dept=(await session.execute(text("SELECT greatest(coalesce((SELECT max(id) FROM departments),0),coalesce((SELECT max(department_id) FROM sales_targets),0))+100000"))).scalar_one()
            before=(await session.execute(text("SELECT (SELECT count(*) FROM sales_targets),(SELECT count(*) FROM audit_logs WHERE business_type='sales_target')"))).one()
            before_target=(await session.execute(text("SELECT new_customer_target,sales_target,remark,updated_at FROM sales_targets WHERE period=:p AND user_id=:u"),{"p":period,"u":root.id})).one()
        app.dependency_overrides[get_current_user]=lambda:CurrentUser(root,permissions,["admin"],"all")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://isolated") as client:
            base={"period":period,"new_customer_target":0,"sales_target":"1.01","repeat_customer_target":"0.00"}
            for field,value in (("user_id",missing_user),("department_id",missing_dept)):
                response=await client.post("/api/v1/sales-targets/upsert",json={**base,field:value})
                assert response.status_code==404 and "不存在" in response.json()["message"],response.text
            for field in ("user_id","department_id"):
                for value in (0,-1,9223372036854775808):
                    response=await client.post("/api/v1/sales-targets/upsert",json={**base,field:value})
                    assert response.status_code in (400,422),response.text
            for field,value,message in (("new_customer_target",2147483648,"2147483647"),
                                        ("remark","注"*256,"255")):
                response=await client.post("/api/v1/sales-targets/upsert",json={**base,"user_id":root.id,field:value})
                assert response.status_code==400 and message in response.json()["message"],response.text
            response=await client.post("/api/v1/sales-targets/upsert",json={**base,"user_id":root.id,"department_id":departments[0].id})
            assert response.status_code==422
            async with SessionLocal() as session:
                after=(await session.execute(text("SELECT (SELECT count(*) FROM sales_targets),(SELECT count(*) FROM audit_logs WHERE business_type='sales_target')"))).one()
                assert after==before,"无效输入不得新增目标或成功审计"
                after_target=(await session.execute(text("SELECT new_customer_target,sales_target,remark,updated_at FROM sales_targets WHERE period=:p AND user_id=:u"),{"p":period,"u":root.id})).one()
                assert after_target==before_target,"无效输入不得修改已有目标或版本时间"
                historical_user=await session.get(User,users[3].id)
                historical_department=await session.get(Department,departments[3].id)
                historical_user.status="inactive"
                historical_department.status="inactive"
                await session.commit()
            for payload in ({**base,"user_id":root.id},{**base,"department_id":departments[0].id},
                            base,{**base,"user_id":users[3].id},{**base,"department_id":departments[3].id}):
                response=await client.post("/api/v1/sales-targets/upsert",json=payload)
                assert response.status_code==200,response.text
            response=await client.post("/api/v1/sales-targets/upsert",json={**base,"user_id":root.id,
                                        "new_customer_target":2147483647,"remark":"注"*255})
            assert response.status_code==200,response.text
            target_id=response.json()["data"]["target_id"]
            async with SessionLocal() as session:
                stored=(await session.execute(text("SELECT new_customer_target,remark FROM sales_targets WHERE id=:id"),{"id":target_id})).one()
                assert stored==(2147483647,"注"*255),stored
        print("PASS target scope/input: missing and oversized values rejected without writes; valid scopes/inactive history and int32/Unicode length limits persisted",flush=True)

    finally:
        if previous is None:
            app.dependency_overrides.pop(get_current_user,None)
        else:
            app.dependency_overrides[get_current_user]=previous
        await engine.dispose()


if __name__=="__main__":
    asyncio.run(main())
