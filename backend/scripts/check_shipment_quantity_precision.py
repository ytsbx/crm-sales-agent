"""本机隔离PG/正式API：发货批次数量字符串、数据库及明细展示精度。"""
import os
import sys
from urllib.parse import urlsplit
sys.path.insert(0,"scripts")
from _test_support import require_isolated_db

db_name = require_isolated_db()
db_url = urlsplit(os.environ["DATABASE_URL"])
if not db_url.scheme.startswith("postgresql") or db_url.hostname not in ("127.0.0.1", "localhost") or not (
    db_name.startswith(("crm_iso", "crm_check", "crm_test")) or db_name.endswith("_test")
):
    raise SystemExit("只允许本机一次性隔离数据库")
os.environ.update(WECOM_PUSH_OFF="1",DINGTALK_PUSH_OFF="1",SCHEDULER_ENABLED="false",
                  DINGTALK_OA_STREAM_ENABLED="false",SCHEDULER_OA_SYNC_ENABLED="false")

import asyncio,json
from decimal import Decimal,ROUND_HALF_UP
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import select,text
from app.core.database import SessionLocal,engine
from app.main import app
from app.modules.customer.model import Customer
from app.modules.order.model import SalesOrder,SalesOrderItem
from app.modules.user.model import User

async def main():
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,raise_app_exceptions=False),base_url='http://isolated') as c:
            r=await c.post('/api/v1/auth/login',json={'username':'admin','password':'admin123'});assert r.status_code==200
            c.headers['Authorization']='Bearer '+r.json()['data']['access_token']
            async with SessionLocal() as s:
                uid=(await s.execute(select(User.id).where(User.username=='admin'))).scalar_one()
                customer=Customer(name='隔离发货数量精度'+uuid4().hex[:10],owner_id=uid);s.add(customer);await s.commit();cid=customer.id
            for ordered,quantity,price in [('9999999999999.999','9999999999999.999','0.0001'),('0.002','0.001','100'),('2.500','1.250','100')]:
                async with SessionLocal() as s:
                    amount=(Decimal(ordered)*Decimal(price)).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
                    order=SalesOrder(order_no='ISO-QTY-'+uuid4().hex[:20],customer_id=cid,owner_id=uid,sales_owner_id=uid,status='in_production',currency='CNY',total_amount=amount)
                    s.add(order);await s.flush()
                    item=SalesOrderItem(order_id=order.id,sku_snapshot='隔离数量规格',quantity=Decimal(ordered),unit_price=Decimal(price),amount=amount)
                    s.add(item);await s.commit();oid,iid=order.id,item.id
                submitted=quantity
                if len(sys.argv)>1 and quantity=='9999999999999.999':submitted=json.loads(Path(sys.argv[1]).read_text())['items'][0]['planned_qty']
                payload={'request_key':uuid4().hex,'items':[{'order_item_id':iid,'planned_qty':submitted}]}
                r=await c.post(f'/api/v1/orders/{oid}/shipments',json=payload);assert r.status_code==200,r.text
                bid=r.json()['data']['batch_id']
                async with SessionLocal() as s:
                    stored=(await s.execute(text('select planned_qty from order_shipment_batch_items where batch_id=:b'),{'b':bid})).scalar_one()
                r=await c.get(f'/api/v1/orders/{oid}/shipments');assert r.status_code==200,r.text
                data=r.json()['data'];unplanned=data['items'][0]['unplanned_exact']
                print('submitted',repr(submitted),'SQL planned',str(stored),'unplanned',unplanned,flush=True)
                assert stored==Decimal(quantity),(stored,quantity)
                assert Decimal(unplanned)==Decimal(ordered)-Decimal(quantity)
                row=data['batches'][0]['items'][0]
                assert Decimal(row['planned_qty_exact'])==Decimal(quantity) and Decimal(row['shipped_qty_exact'])==0
                replay=await c.post(f'/api/v1/orders/{oid}/shipments',json=payload)
                assert replay.status_code==200 and replay.json()['data']['batch_id']==bid,replay.text
                async with SessionLocal() as s:
                    count=(await s.execute(text("select count(*) from audit_logs where business_type='order' and business_id=:o and action='create_shipment_batch'"),{'o':oid})).scalar_one()
                    assert count==1
                r=await c.post(f'/api/v1/orders/{oid}/shipments/{bid}/ship',json={});assert r.status_code==200,r.text
                r=await c.get(f'/api/v1/orders/{oid}/shipments');assert r.status_code==200,r.text
                data=r.json()['data'];row=data['batches'][0]['items'][0]
                assert Decimal(row['shipped_qty_exact'])==Decimal(quantity)
                assert Decimal(data['summary']['ordered_exact'])==Decimal(ordered)
                assert Decimal(data['summary']['shipped_exact'])==Decimal(quantity)
                assert Decimal(data['summary']['remaining_exact'])==Decimal(ordered)-Decimal(quantity)
                assert data['summary']['all_shipped']==(Decimal(ordered)==Decimal(quantity))
                async with SessionLocal() as s:
                    shipped=(await s.execute(text('select shipped_qty from order_shipment_batch_items where batch_id=:b'),{'b':bid})).scalar_one()
                    assert shipped==Decimal(quantity)
                print('PASS exact plan/replay/automatic shipment/database/remaining',quantity,flush=True)
    finally:await engine.dispose()
if __name__=='__main__':asyncio.run(main())
