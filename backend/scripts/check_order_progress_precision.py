"""本机隔离PG/正式建单：客户动态金额与订单Decimal一致。"""
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
from app.main import app
from app.modules.customer.model import Customer
from app.modules.order.model import SalesOrder
from app.modules.user.model import User

from app.modules.product.model import Product,Sku
from datetime import UTC,datetime
from app.modules.quote.model import Quote,QuoteVersion,QuoteItem,QuoteCharge

async def main():
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,raise_app_exceptions=False),base_url='http://isolated') as c:
            r=await c.post('/api/v1/auth/login',json={'username':'admin','password':'admin123'});assert r.status_code==200,r.text
            c.headers['Authorization']='Bearer '+r.json()['data']['access_token']
            async with SessionLocal() as s:
                user=(await s.execute(select(User).where(User.username=='admin'))).scalar_one()
                customer=Customer(name='隔离订单动态精度'+uuid4().hex[:8],owner_id=user.id)
                product=Product(name='隔离精度产品'+uuid4().hex[:8],status='active')
                s.add_all([customer,product]);await s.flush()
                sku=Sku(product_id=product.id,sku_code='ISO-MONEY-'+uuid4().hex[:15],name='隔离精度规格',status='active')
                s.add(sku);await s.commit();cid,skuid=customer.id,sku.id
            for quantity,price,expected in [('100','999999999999.9999','99999999999999.99'),('1','1234.56','1234.56'),('1','0.01','0.01')]:
                r=await c.post('/api/v1/orders',json={'customer_id':cid,'currency':'CNY','items':[{'sku_id':skuid,'quantity':quantity,'unit_price':price}]})
                assert r.status_code==200,r.text
                data=r.json()['data'];oid=data['order_id'];order_no=data['order_no']
                async with SessionLocal() as s:
                    amount=(await s.execute(select(SalesOrder.total_amount).where(SalesOrder.id==oid))).scalar_one()
                    events=(await s.execute(text('SELECT content FROM followups WHERE order_id=:o ORDER BY id'),{'o':oid})).scalars().all()
                    assert amount==Decimal(expected),amount
                    assert events==[f'【系统】{order_no} 金额 ¥{Decimal(expected):,.2f}（手工建单）'],events
                    count=(await s.execute(text("SELECT count(*) FROM business_events WHERE event_key=:k"),{'k':f'order:create:{oid}'})).scalar_one()
                    assert count==1,count
                print('PASS manual order',expected,'SQL total and exact progress text; one event',flush=True)
    finally:await engine.dispose()
async def check_quote_convert():
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,raise_app_exceptions=False),base_url='http://isolated') as c:
            r=await c.post('/api/v1/auth/login',json={'username':'admin','password':'admin123'});assert r.status_code==200,r.text
            c.headers['Authorization']='Bearer '+r.json()['data']['access_token']
            async with SessionLocal() as s:
                user=(await s.execute(select(User).where(User.username=='admin'))).scalar_one()
                customer=Customer(name='隔离转单动态精度'+uuid4().hex[:8],owner_id=user.id)
                s.add(customer);await s.commit();cid,uid=customer.id,user.id
            for quantity,price,expected in [('100','999999999999.9999','99999999999999.99'),('1','1234.56','1234.56'),('1','0.01','0.01')]:
                async with SessionLocal() as s:
                    q=Quote(quote_no='ISO-CONV-'+uuid4().hex[:20],customer_id=cid,owner_id=uid,status='accepted')
                    s.add(q);await s.flush()
                    v=QuoteVersion(quote_id=q.id,version_no=1,approval_status='approved',created_at=datetime.now(UTC),currency='CNY',subtotal_amount=Decimal(expected),total_amount=Decimal(expected),sent_at=datetime.now(UTC),accepted_at=datetime.now(UTC))
                    s.add(v);await s.flush();q.current_version_id=v.id
                    s.add(QuoteItem(quote_version_id=v.id,sku_name_snapshot='隔离完整成本定制',quantity=Decimal(quantity),quoted_price=Decimal(price),cost_snapshot=Decimal('0'),cost_present_snapshot=True,cost_complete_snapshot=True))
                    s.add(QuoteCharge(quote_version_id=v.id,charge_type='logistics',amount=Decimal('0'),logistics_confirmed_at=datetime.now(UTC)))
                    await s.commit();vid=v.id
                r=await c.post(f'/api/v1/quote-versions/{vid}/convert-to-order?confirm=true',json={})
                assert r.status_code==200,r.text
                async with SessionLocal() as s:
                    order=(await s.execute(select(SalesOrder).where(SalesOrder.quote_version_id==vid))).scalar_one()
                    assert order.total_amount==Decimal(expected),order.total_amount
                    plans=(await s.execute(text('SELECT amount FROM receivable_plans WHERE order_id=:o'),{'o':order.id})).scalars().all()
                    assert plans==[Decimal(expected)],plans
                    events=(await s.execute(text('SELECT content FROM followups WHERE order_id=:o ORDER BY id'),{'o':order.id})).scalars().all()
                    print('SQL order/plans',str(order.total_amount),[str(p) for p in plans],'dynamic',events,flush=True)
                    assert events==[f'【系统】{order.order_no} 金额 ¥{Decimal(expected):,.2f}（由报价转单）'],events
                    count=(await s.execute(text("SELECT count(*) FROM business_events WHERE event_key=:k"),{'k':f'order:create:{order.id}'})).scalar_one()
                    assert count==1,count
                r=await c.post(f'/api/v1/quote-versions/{vid}/convert-to-order?confirm=true',json={})
                assert r.status_code==409,r.text
                async with SessionLocal() as s:
                    assert (await s.execute(text('SELECT count(*) FROM sales_orders WHERE quote_version_id=:v'),{'v':vid})).scalar_one()==1
                print('PASS quote convert',expected,'SQL total and receivable, exact progress, one event, replay409 no second order',flush=True)
    finally:await engine.dispose()

async def run_all():
    await main()
    await check_quote_convert()

asyncio.run(run_all())
