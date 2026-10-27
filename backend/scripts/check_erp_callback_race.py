"""真实本机一次性PG/ASGI：重复ERP回调的查询自动flush冲突也应按重放处理。"""
import os
import asyncio
import secrets
from uuid import uuid4
from urllib.parse import urlparse
from _test_support import require_isolated_db

_name = require_isolated_db()
_url = urlparse(os.environ['DATABASE_URL'])
if _url.hostname not in ('127.0.0.1', 'localhost') or not _url.scheme.startswith('postgresql') or not (_name.startswith(('crm_iso', 'crm_check', 'crm_test')) or _name.endswith('_test')):
    raise SystemExit('只允许显式本机一次性PostgreSQL')
os.environ.update(ERP_PROVIDER='jushuitan', ERP_BASE_URL='http://127.0.0.1:1', ERP_APP_KEY='isolated',
                  ERP_APP_SECRET='isolated', ERP_WEBHOOK_SECRET=secrets.token_urlsafe(48),
                  WECOM_PUSH_OFF='1', DINGTALK_PUSH_OFF='1', SCHEDULER_ENABLED='0',
                  SCHEDULER_OA_SYNC_ENABLED='0', DINGTALK_OA_STREAM_ENABLED='0')
import httpx
from fastapi import FastAPI,Request
from sqlalchemy import select,delete
__import__('app.main')  # Register models; no app lifespan or external request.
from app.core.database import SessionLocal,engine,get_db
from app.core.errors import register_exception_handlers
from app.modules.customer.model import Customer
from app.modules.order.model import SalesOrder,OrderStatusHistory
from app.modules.integration.model import IntegrationLog
from app.modules.erp import service
from app.modules.erp.router import router
cid=oid=None;event_key=None;gates={}
class Gate:
    def __init__(self,s):self.s=s;self.entered=asyncio.Event();self.resume=asyncio.Event();self.once=False
    def __getattr__(self,k):return getattr(self.s,k)
    async def execute(self,stmt,*a,**kw):
        result=await self.s.execute(stmt,*a,**kw)
        desc=getattr(stmt,'column_descriptions',[])
        if not self.once and desc and desc[0].get('expr') is IntegrationLog:
            self.once=True;self.entered.set();await asyncio.wait_for(self.resume.wait(),10)
        return result
app=FastAPI();register_exception_handlers(app);app.include_router(router)
async def isolated_db(request:Request):
    async with SessionLocal() as s:
        tag=request.headers.get('X-Isolated-Tag')
        gate=Gate(s);gates[tag]=gate
        if tag not in ('A','B'):gate.resume.set()
        try:yield gate
        except BaseException as error:
            original=getattr(error,'orig',None)
            cause=getattr(original,'__cause__',None)
            if original is not None:
                print('driver diagnostics:',type(original).__name__,
                      'orig_constraint=',getattr(original,'constraint_name',None),
                      'cause_type=',type(cause).__name__,
                      'cause_constraint=',getattr(cause,'constraint_name',None),flush=True)
            await s.rollback();raise
app.dependency_overrides[get_db]=isolated_db
async def main(initial_status='pending'):
    global cid,oid,event_key
    gates.clear()
    try:
        async with SessionLocal() as s:
            c=Customer(name='synthetic ERP replay '+uuid4().hex);s.add(c);await s.flush();cid=c.id
            order=SalesOrder(order_no='ISO-ERP-'+uuid4().hex[:12],customer_id=cid,status=initial_status,total_amount=0,currency='CNY');s.add(order);await s.flush();oid=order.id;order_no=order.order_no;await s.commit()
        event_key=service.build_status_event_key(order_no=order_no,erp_order_id=None,raw_status='Confirmed',remark=None)
        body={'order_no':order_no,'status':'Confirmed'}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,raise_app_exceptions=False),base_url='http://isolated') as client:
            async def send(tag):return await client.post('/webhooks/erp/order-status',json=body,headers={'X-ERP-Secret':os.environ['ERP_WEBHOOK_SECRET'],'X-Isolated-Tag':tag})
            a=asyncio.create_task(send('A'))
            while 'A' not in gates:await asyncio.sleep(0)
            await asyncio.wait_for(gates['A'].entered.wait(),5)
            b=asyncio.create_task(send('B'))
            while 'B' not in gates:await asyncio.sleep(0)
            await asyncio.wait_for(gates['B'].entered.wait(),5)
            gates['A'].resume.set();ra=await asyncio.wait_for(a,10)
            gates['B'].resume.set();rb=await asyncio.wait_for(b,10)
            print(initial_status+': duplicate concurrent callbacks HTTP statuses=',ra.status_code,rb.status_code,flush=True)
            print('second response=',rb.text,flush=True)
            async with SessionLocal() as s:
                logs=(await s.execute(select(IntegrationLog).where(IntegrationLog.event_key==event_key))).scalars().all()
                history=(await s.execute(select(OrderStatusHistory).where(OrderStatusHistory.order_id==oid))).scalars().all()
                order=await s.get(SalesOrder,oid)
                print('durable rows: logs='+str(len(logs))+' history='+str(len(history))+' order='+order.status,flush=True)
                cancelled=initial_status=='cancelled'
                assert len(logs)==(2 if cancelled else 1)
                assert all(log.status==('failed' if cancelled else 'success') for log in logs)
                assert len(history)==(1 if initial_status=='pending' else 0)
                assert order.status==('cancelled' if cancelled else 'in_production')
            assert ra.status_code==rb.status_code==200,'duplicate event must return replay rather than unhandled autoflush conflict'
            if initial_status=='cancelled':
                assert ra.json()['data']['blocked_reason'] and rb.json()['data']['blocked_reason']
            else:
                assert rb.json()['data']['replayed'] is True
                replay=await send('C')
                assert replay.status_code==200 and replay.json()['data']['replayed'] is True
                async with SessionLocal() as verify:
                    assert len((await verify.execute(select(IntegrationLog).where(IntegrationLog.event_key==event_key))).scalars().all())==1
            bad=await client.post('/webhooks/erp/order-status',json=body,headers={'X-ERP-Secret':'wrong'})
            assert bad.status_code==403 and not gates[None].once
            print(initial_status+': replay/lifecycle/authentication assertions passed',flush=True)
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(IntegrationLog).where(IntegrationLog.event_key==event_key))
            await s.execute(delete(OrderStatusHistory).where(OrderStatusHistory.order_id==oid))
            await s.execute(delete(SalesOrder).where(SalesOrder.id==oid))
            await s.execute(delete(Customer).where(Customer.id==cid));await s.commit()
        await engine.dispose()
async def run():
    for status in ('pending','in_production','cancelled'):await main(status)

if __name__ == '__main__':
    asyncio.run(run())
