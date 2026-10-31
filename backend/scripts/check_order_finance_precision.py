"""本机隔离PG/正式API：订单财务概览保持到分，旧数字字段兼容。"""
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

import asyncio
from decimal import Decimal
from uuid import uuid4

import httpx
from sqlalchemy import select, text
from app.core.database import SessionLocal, engine
from app.core.timebase import today_business
from app.main import app
from app.modules.customer.model import Customer
from app.modules.order.model import SalesOrder
from app.modules.user.model import User

async def main():
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,raise_app_exceptions=False),base_url='http://isolated') as c:
            r=await c.post('/api/v1/auth/login',json={'username':'admin','password':'admin123'});assert r.status_code==200,r.text
            c.headers['Authorization']='Bearer '+r.json()['data']['access_token']
            async def req(method,path,payload=None):
                r=await c.request(method,'/api/v1'+path,json=payload);assert r.status_code==200,(path,r.status_code,r.text)
                return r.json()['data']
            async with SessionLocal() as s:
                owner=(await s.execute(select(User).where(User.username=='admin'))).scalar_one()
                customer=Customer(name='隔离财务精度'+uuid4().hex[:8],owner_id=owner.id)
                s.add(customer);await s.flush()
                order=SalesOrder(order_no='ISO-FIN-'+uuid4().hex[:22],customer_id=customer.id,owner_id=owner.id,sales_owner_id=owner.id,status='pending',currency='CNY',total_amount=Decimal('99999999999999.99'))
                s.add(order);await s.commit();oid=order.id
            p=await req('POST',f'/orders/{oid}/receivables',{'plan_name':'隔离大额应收','due_date':'2026-12-01','amount':'99999999999999.99'})
            for i in range(3):
                payment=await req('POST','/payments',{'receivable_plan_id':p['id'],'received_amount':'99999999999999.99','received_date':today_business().isoformat(),'request_key':uuid4().hex})
                if i<2:await req('POST',f"/payments/{payment['id']}/confirm",{})
            summary=await req('GET',f'/orders/{oid}/finance-summary')
            expected={k:'99999999999999.99' for k in ('order_amount','planned_amount','allocated_amount','unallocated_amount','pending_confirm_amount')}
            expected.update(received_amount='199999999999999.98',unreceived_amount='-99999999999999.99',unsettled_plan_amount='0.00')
            async with SessionLocal() as s:
                stored=(await s.execute(text("SELECT (SELECT total_amount FROM sales_orders WHERE id=:o),(SELECT sum(received_amount) FROM payment_records WHERE order_id=:o AND status='confirmed'),(SELECT sum(amount) FROM payment_allocations WHERE payment_id IN (SELECT id FROM payment_records WHERE order_id=:o))"),{'o':oid})).one()
                assert stored==(Decimal(expected['order_amount']),Decimal(expected['received_amount']),Decimal(expected['allocated_amount'])),stored
            for k,v in expected.items():assert summary.get(k+'_exact')==v,(k,v,summary.get(k+'_exact'))
            assert all(isinstance(summary[k],(int,float)) for k in expected),summary
            print('PASS finance summary exact fields match SQL, legacy numbers retained; order',oid,flush=True)
    finally:await engine.dispose()
asyncio.run(main())
