"""本机隔离PG/正式API：发货计划稳定请求键、并发/提交异常、当前范围与旧调用兼容。"""
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

from sqlalchemy.ext.asyncio import AsyncSession
from app.core.security import hash_password
from app.modules.user.model import Role,Permission,role_permissions,user_roles
from app.modules.order import router
from app.modules.order.model import SalesOrderItem

async def main():
    original_commit=AsyncSession.commit
    original_audit=router.write_audit
    resume=asyncio.Event();worker=None
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,raise_app_exceptions=False),base_url='http://isolated') as c:
            async with SessionLocal() as s:
                stamp=uuid4().hex[:12]
                role=Role(code='iso-shipkey-'+stamp,name='隔离排批防重',data_scope='self')
                u=User(username='iso-shipkey-'+stamp,name='隔离排批用户',password_hash=hash_password('iso-ship-key-password'),status='active')
                s.add_all([role,u]);await s.flush()
                pids=dict((await s.execute(select(Permission.code,Permission.id))).all())
                for code in ['order:view','order:manage']:await s.execute(role_permissions.insert().values(role_id=role.id,permission_id=pids[code]))
                await s.execute(user_roles.insert().values(user_id=u.id,role_id=role.id))
                customer=Customer(name='隔离排批防重'+stamp,owner_id=u.id);s.add(customer);await s.commit();uid,cid=u.id,customer.id
            r=await c.post('/api/v1/auth/login',json={'username':u.username,'password':'iso-ship-key-password'});assert r.status_code==200,r.text
            c.headers['Authorization']='Bearer '+r.json()['data']['access_token']
            async def fixture():
                async with SessionLocal() as s:
                    o=SalesOrder(order_no='ISO-KEY-'+uuid4().hex[:20],customer_id=cid,owner_id=uid,status='in_production',currency='CNY',total_amount=Decimal('100'))
                    s.add(o);await s.flush()
                    item=SalesOrderItem(order_id=o.id,sku_snapshot='隔离明细',quantity=Decimal('100'),unit_price=Decimal('1'),amount=Decimal('100'));s.add(item);await s.commit()
                    return o.id,item.id
            async def rows(oid):
                async with SessionLocal() as s:
                    return (await s.execute(text('SELECT to_jsonb(b) FROM order_shipment_batches b WHERE order_id=:o ORDER BY id'),{'o':oid})).scalars().all(),(await s.execute(text("SELECT count(*) FROM audit_logs WHERE business_type='order' AND business_id=:o AND action='create_shipment_batch'"),{'o':oid})).scalar_one()
            async def key_count(key):
                async with SessionLocal() as s:
                    return (await s.execute(text("SELECT count(*) FROM request_keys WHERE user_id=:u AND action='order:create_shipment' AND request_key=:k"),{'u':uid,'k':key})).scalar_one()
            for mode in ['commit_reply_lost','audit_failure']:
                oid,iid=await fixture();key=uuid4().hex
                payload={'request_key':key,'items':[{'order_item_id':iid,'planned_qty':'4'}]};path=f'/api/v1/orders/{oid}/shipments'
                fired=False
                async def commit_lost(self):
                    nonlocal fired
                    await original_commit(self)
                    if not fired:fired=True;raise RuntimeError('isolated committed shipment plan reply lost')
                async def audit_fail(*args,**kwargs):
                    nonlocal fired
                    if kwargs.get('action')=='create_shipment_batch' and kwargs.get('business_id')==oid:
                        fired=True;raise RuntimeError('isolated shipment plan audit failure')
                    return await original_audit(*args,**kwargs)
                try:
                    if mode=='commit_reply_lost':AsyncSession.commit=commit_lost
                    else:router.write_audit=audit_fail
                    r=await c.post(path,json=payload);assert r.status_code==500 and fired,r.text
                finally:AsyncSession.commit=original_commit;router.write_audit=original_audit
                if mode=='audit_failure':assert await rows(oid)==([],0) and await key_count(key)==0
                else:assert len((await rows(oid))[0])==1 and await key_count(key)==1
                r=await c.post(path,json=payload);assert r.status_code==200,r.text
                result=r.json()['data'];saved=await rows(oid);assert len(saved[0])==1 and saved[1]==1
                for _ in range(2):
                    r=await c.post(path,json=payload);assert r.status_code==200 and r.json()['data']==result,r.text
                    assert await rows(oid)==saved
                r=await c.post(path,json={**payload,'items':[{'order_item_id':iid,'planned_qty':'5'}]});assert r.status_code==409,r.text
                assert await rows(oid)==saved
                # 同键跨订单必须冲突；不能回放另一个订单的批次。
                other,otheritem=await fixture()
                r=await c.post(f'/api/v1/orders/{other}/shipments',json={**payload,'items':[{'order_item_id':otheritem,'planned_qty':'4'}]});assert r.status_code==409,r.text
                assert await rows(other)==([],0)
                # 不同键相同内容仍是合法另排一批；无键老调用和header键均保持。
                r=await c.post(path,json={**payload,'request_key':uuid4().hex});assert r.status_code==200 and r.json()['data']['batch_id']!=result['batch_id'],r.text
                no_key={'items':payload['items']}
                r=await c.post(path,json=no_key);assert r.status_code==200,r.text
                hkey=uuid4().hex
                a=await c.post(path,json=no_key,headers={'X-Request-Key':hkey});b=await c.post(path,json=no_key,headers={'X-Request-Key':hkey})
                assert a.status_code==b.status_code==200 and a.json()['data']==b.json()['data']
                assert len((await rows(oid))[0])==4
                # 回放仍检查当前归属，不能用旧键从不可见订单取回结果。
                async with SessionLocal() as s:
                    admin=(await s.execute(select(User).where(User.username=='admin'))).scalar_one()
                    o=await s.get(SalesOrder,oid);o.owner_id=admin.id;await s.commit()
                r=await c.post(path,json=payload);assert r.status_code==403,r.text
                assert len((await rows(oid))[0])==4
                print('PASS',mode,'retry original batch/audit/key, conflict altered/cross-order, new key & legacy/header supported, current scope enforced',flush=True)
            # 真实订单锁等待：首请求暂停在审计处，第二请求确实被该后端PID阻塞。
            oid,iid=await fixture();key=uuid4().hex;payload={'request_key':key,'items':[{'order_item_id':iid,'planned_qty':'4'}]};path=f'/api/v1/orders/{oid}/shipments'
            ready=asyncio.Event();resume.clear();pid=None
            async def pause_audit(*args,**kwargs):
                nonlocal pid
                if kwargs.get('action')=='create_shipment_batch' and kwargs.get('business_id')==oid:
                    pid=(await args[0].execute(text('SELECT pg_backend_pid()'))).scalar_one();ready.set();await asyncio.wait_for(resume.wait(),10)
                return await original_audit(*args,**kwargs)
            second=None
            try:
                router.write_audit=pause_audit
                worker=asyncio.create_task(c.post(path,json=payload));await asyncio.wait_for(ready.wait(),10)
                second=asyncio.create_task(c.post(path,json=payload))
                waiting=False
                for _ in range(100):
                    async with SessionLocal() as s:
                        waiting=(await s.execute(text('SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE :pid=ANY(pg_blocking_pids(pid)))'),{'pid':pid})).scalar_one()
                    if waiting:break
                    await asyncio.sleep(.02)
                assert waiting,'second request did not actually wait on first order lock'
                resume.set();responses=await asyncio.wait_for(asyncio.gather(worker,second),10)
                assert all(r.status_code==200 for r in responses),[(r.status_code,r.text) for r in responses]
                assert responses[0].json()['data']==responses[1].json()['data']
                saved=await rows(oid);assert len(saved[0])==1 and saved[1]==1 and await key_count(key)==1
                print('PASS actual order-lock wait; concurrent same-key requests both return sole batch, one audit/key; bounded completion',flush=True)
            finally:
                resume.set();router.write_audit=original_audit
                for task in [worker,second]:
                    if task and not task.done():task.cancel();await asyncio.gather(task,return_exceptions=True)
    finally:
        resume.set();AsyncSession.commit=original_commit;router.write_audit=original_audit
        await engine.dispose()
asyncio.run(main())
