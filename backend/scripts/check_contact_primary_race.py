"""本机隔离PG/正式API：主联系人并发选择保持唯一且按最后成功请求写入。"""
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
from uuid import uuid4

import httpx
from sqlalchemy import select, text
from app.core.database import SessionLocal, engine
from app.main import app
from app.modules.customer.model import Customer, Contact
from app.modules.user.model import User

async def main():
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,raise_app_exceptions=False),base_url='http://isolated') as c:
            r=await c.post('/api/v1/auth/login',json={'username':'admin','password':'admin123'});assert r.status_code==200
            c.headers['Authorization']='Bearer '+r.json()['data']['access_token']
            async with SessionLocal() as s:
                u=(await s.execute(select(User).where(User.username=='admin'))).scalar_one()
                customer=Customer(name='隔离主联系人竞争'+uuid4().hex[:8],owner_id=u.id)
                s.add(customer);await s.flush()
                contacts=[Contact(customer_id=customer.id,name='隔离A',is_primary=True),Contact(customer_id=customer.id,name='隔离B',is_primary=False)]
                s.add_all(contacts);await s.commit();cid=customer.id;ids=[x.id for x in contacts]
            async def waiters(count):
                for _ in range(150):
                    async with SessionLocal() as s:
                        n=(await s.execute(text("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND wait_event_type='Lock' AND query ILIKE '%customers%' AND query ILIKE '%FOR UPDATE%'"))).scalar_one()
                    if n>=count:return
                    await asyncio.sleep(.02)
                raise AssertionError((count,n))
            tasks=[]
            try:
                async with SessionLocal() as lock:
                    await lock.execute(text('SELECT id FROM customers WHERE id=:id FOR UPDATE'),{'id':cid})
                    tasks.append(asyncio.create_task(c.post(f'/api/v1/contacts/{ids[1]}/set-primary',json={})));await waiters(1)
                    tasks.append(asyncio.create_task(c.post(f'/api/v1/contacts/{ids[0]}/set-primary',json={})));await waiters(2)
                    assert all(not task.done() for task in tasks);await lock.commit()
                responses=await asyncio.wait_for(asyncio.gather(*tasks),10)
                assert all(r.status_code==200 for r in responses),[(r.status_code,r.text) for r in responses]
                async with SessionLocal() as s:
                    rows=(await s.execute(text('SELECT id,is_primary,deleted_at FROM contacts WHERE customer_id=:c ORDER BY id'),{'c':cid})).all()
                print('two actual lock waiters; B then A responses',[(r.status_code,r.json()['data']['is_primary']) for r in responses],'SQL',rows,flush=True)
                assert sum(row.is_primary for row in rows)==1 and rows[0].is_primary,rows
                print('PASS latest explicit selection remains sole primary',flush=True)
            finally:
                for task in tasks:
                    if not task.done():task.cancel()
                await asyncio.gather(*tasks,return_exceptions=True)
    finally:await engine.dispose()
async def check_moved_contact():
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,raise_app_exceptions=False),base_url='http://isolated') as c:
            r=await c.post('/api/v1/auth/login',json={'username':'admin','password':'admin123'});assert r.status_code==200
            c.headers['Authorization']='Bearer '+r.json()['data']['access_token']
            for entry in ('change-customer','bind-customer'):
                async with SessionLocal() as s:
                    u=(await s.execute(select(User).where(User.username=='admin'))).scalar_one()
                    a,b=[Customer(name='隔离改挂'+uuid4().hex[:8],owner_id=u.id) for _ in range(2)]
                    s.add_all([a,b]);await s.flush()
                    old=Contact(customer_id=a.id,name='原客户主',is_primary=True)
                    moving=Contact(customer_id=a.id,name='改挂人',is_primary=False)
                    target=Contact(customer_id=b.id,name='目标客户主',is_primary=True)
                    s.add_all([old,moving,target]);await s.commit();aid,bid,mid=a.id,b.id,moving.id
                async def waiters(count):
                    for _ in range(150):
                        async with SessionLocal() as s:
                            n=(await s.execute(text("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND wait_event_type='Lock' AND query ILIKE '%customers%' AND query ILIKE '%FOR UPDATE%'"))).scalar_one()
                        if n>=count:return
                        await asyncio.sleep(.02)
                    raise AssertionError((count,n))
                tasks=[]
                try:
                    async with SessionLocal() as lock:
                        await lock.execute(text('SELECT id FROM customers WHERE id=:id FOR UPDATE'),{'id':aid})
                        tasks.append(asyncio.create_task(c.post(f'/api/v1/contacts/{mid}/{entry}',json={'customer_id':bid,'is_primary':False})));await waiters(1)
                        tasks.append(asyncio.create_task(c.post(f'/api/v1/contacts/{mid}/set-primary',json={})));await waiters(2)
                        assert all(not t.done() for t in tasks);await lock.commit()
                    responses=await asyncio.wait_for(asyncio.gather(*tasks),10)
                    assert [r.status_code for r in responses]==[200,409],[(r.status_code,r.text) for r in responses]
                    async with SessionLocal() as s:
                        rows=(await s.execute(text('SELECT id,customer_id,is_primary FROM contacts WHERE customer_id IN (:a,:b) ORDER BY id'),{'a':aid,'b':bid})).all()
                        assert [(row.customer_id,row.is_primary) for row in rows]==[(aid,True),(bid,False),(bid,True)],rows
                        audit=(await s.execute(text("SELECT action FROM audit_logs WHERE business_type='contact' AND business_id=:id ORDER BY id"),{'id':mid})).scalars().all()
                        assert audit==[entry.replace('-','_')],audit
                    assert '已改挂' in responses[1].json()['message']
                    retry=await c.post(f'/api/v1/contacts/{mid}/set-primary',json={})
                    assert retry.status_code==200,retry.text
                    async with SessionLocal() as s:
                        final=(await s.execute(text('SELECT id,customer_id,is_primary FROM contacts WHERE customer_id IN (:a,:b) ORDER BY id'),{'a':aid,'b':bid})).all()
                        assert [(row.customer_id,row.is_primary) for row in final]==[(aid,True),(bid,True),(bid,False)],final
                        actions=(await s.execute(text("SELECT action FROM audit_logs WHERE business_type='contact' AND business_id=:id ORDER BY id"),{'id':mid})).scalars().all()
                        assert actions==[entry.replace('-','_'),'set_primary'],actions
                    print('PASS',entry,'then stale selection409; original primaries unchanged; fresh retry200 selects moved contact in new customer only',flush=True)
                finally:
                    for task in tasks:
                        if not task.done():task.cancel()
                    await asyncio.gather(*tasks,return_exceptions=True)
    finally:await engine.dispose()
async def run_all():
    await main()
    await check_moved_contact()

if __name__ == "__main__":
    asyncio.run(run_all())
