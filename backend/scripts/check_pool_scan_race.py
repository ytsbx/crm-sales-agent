"""真实本机PG：公海扫描旧快照及锁交错；只清理本轮合成ID。"""
import os
from urllib.parse import urlparse
from _test_support import require_isolated_db
name=require_isolated_db()
url=urlparse(os.environ['DATABASE_URL'])
if url.hostname not in ('127.0.0.1','localhost') or not url.scheme.startswith('postgresql') or not (name.startswith(('crm_iso','crm_check','crm_test')) or name.endswith('_test')):
    raise SystemExit('only explicit local disposable PostgreSQL allowed')
os.environ.update(WECOM_PUSH_OFF='1',DINGTALK_PUSH_OFF='1',SCHEDULER_ENABLED='0',SCHEDULER_OA_SYNC_ENABLED='0',DINGTALK_OA_STREAM_ENABLED='0')
import asyncio
from datetime import UTC,datetime,timedelta
from uuid import uuid4
from sqlalchemy import select,delete,text
from starlette.requests import Request
from app.modules.product.model import Product, Sku
from app.modules.order.model import SalesOrder, SalesOrderItem, OrderStatusHistory
from app.modules.order.router import create_order
from app.modules.order.schema import OrderCreate
from app.modules.sample.model import SampleRequest, SampleItem
from app.modules.sample.router import create_sample
from app.modules.sample.schema import SampleCreate
from app.modules.followup.model import FollowUp
from app.modules.notification.model import BusinessEvent
__import__('app.main')  # Register all mapped models without running app lifespan.
from app.core.database import SessionLocal,engine
from app.core.deps import CurrentUser
from app.core.audit import AuditLog
from app.modules.customer.model import Customer,CustomerOwnerHistory
from app.modules.user.model import User
from app.modules.notification.model import Notification
from app.modules.settings.model import PublicPoolRule,PublicPoolRecycleCandidate as Candidate
from app.modules.settings import service as settings
from app.modules.customer import service as customers
req=Request({'type':'http','headers':[],'client':('127.0.0.1',1)})
NOW=datetime.now(UTC)
uids=[];cids=[];rids=[];pids=[];skids=[]
class Pause:
    def __init__(self,s):self.s=s;self.entered=asyncio.Event();self.resume=asyncio.Event();self.once=False
    def __getattr__(self,k):return getattr(self.s,k)
    async def execute(self,statement,*args,**kwargs):
        result=await self.s.execute(statement,*args,**kwargs)
        desc=getattr(statement,'column_descriptions',[])
        if not self.once and len(desc)==1 and desc[0].get('expr') is Customer and 'customers.pool_status' in str(statement):
            self.once=True;self.entered.set();await self.resume.wait()
        return result
async def setup():
    async with SessionLocal() as s:
        assert not (await s.execute(select(Customer.id).limit(1))).first(),'requires empty isolated customers'
        assert not (await s.execute(select(PublicPoolRule.id).limit(1))).first(),'requires no rules'
        for _ in range(3):
            u=User(username='scan-review-'+uuid4().hex,name='synthetic',password_hash='synthetic',status='active',created_at=NOW,updated_at=NOW)
            s.add(u);await s.flush();uids.append(u.id)
        rule=PublicPoolRule(level='B',days=30,enabled=True);s.add(rule);await s.flush();rids.append(rule.id)
        await s.commit()
async def scenario(mode):
    async with SessionLocal() as s:
        c=Customer(name='scan-'+mode+'-'+uuid4().hex,owner_id=uids[0],pool_status='private',level='B',last_followup_at=NOW-timedelta(days=100),last_contact_unknown=False,created_at=NOW-timedelta(days=200),updated_at=NOW)
        s.add(c);await s.flush();cid=c.id;cids.append(cid);await s.commit()
    async with SessionLocal() as scan,SessionLocal() as writer:
        pause=Pause(scan);job=asyncio.create_task(settings.run_public_pool_recycle(pause,operator_id=uids[2]))
        await asyncio.wait_for(pause.entered.wait(),5)
        c=await writer.get(Customer,cid)
        if mode=='transfer':
            user=CurrentUser(await writer.get(User,uids[2]),roles=['admin'],permissions={'customer:assign'},data_scope='all')
            await customers.transfer_customer(writer,user,c,uids[1],'synthetic transfer during scan')
        elif mode=='delete':await customers.delete_customer(writer,c)
        else:c.last_followup_at=NOW;await writer.flush()
        await writer.commit();pause.resume.set()
        report=await asyncio.wait_for(job,10)
    async with SessionLocal() as s:
        rows=(await s.execute(select(Candidate).where(Candidate.customer_id==cid))).scalars().all()
        notices=(await s.execute(select(Notification).where(Notification.business_id==cid,Notification.user_id.in_(uids)))).scalars().all()
        assert report['nominated_count']==0 and report['notified_count']==0 and not rows and not notices,(mode,report,rows,notices)
        print(mode+': stale snapshot creates no candidate or notice')
    # Isolate next scenario by soft-deleting this synthetic customer.
    async with SessionLocal() as s:
        c=await s.get(Customer,cid);c.deleted_at=NOW;await s.commit()
class Gate:
    def __init__(self, session, entity, locked):
        self.s=session;self.entity=entity;self.locked=locked
        self.entered=asyncio.Event();self.resume=asyncio.Event();self.once=False
    def __getattr__(self,k):return getattr(self.s,k)
    async def execute(self,stmt,*args,**kwargs):
        result=await self.s.execute(stmt,*args,**kwargs)
        desc=getattr(stmt,'column_descriptions',[])
        if not self.once and desc and desc[0].get('expr') is self.entity and (getattr(stmt,'_for_update_arg',None) is not None)==self.locked:
            self.once=True;self.entered.set();await asyncio.wait_for(self.resume.wait(),10)
        return result
async def new_customer():
    async with SessionLocal() as s:
        c=Customer(name='scan-lock-'+uuid4().hex,owner_id=uids[0],pool_status='private',level='B',last_followup_at=NOW-timedelta(days=100),last_contact_unknown=False,created_at=NOW-timedelta(days=200),updated_at=NOW)
        s.add(c);await s.flush();cid=c.id;cids.append(cid);await s.commit();return cid
async def retire(cid):
    async with SessionLocal() as s:
        c=await s.get(Customer,cid);c.deleted_at=NOW;await s.commit()
async def wait_enter(gate,task):
    try:await asyncio.wait_for(gate.entered.wait(),5)
    except BaseException:
        if task.done():await task
        raise
async def busy_catchup():
    cid=await new_customer()
    async with SessionLocal() as writer,SessionLocal() as scanner:
        await writer.execute(select(Customer).where(Customer.id==cid).with_for_update())
        result=await asyncio.wait_for(settings.run_public_pool_recycle(scanner,uids[2]),5)
        assert result['nominated_count']==0
        await writer.rollback()
        result=await settings.run_public_pool_recycle(scanner,uids[2]);assert result['nominated_count']==1
    await retire(cid);print('busy customer skipped without waiting, next scan catches up')
async def late_candidate(approval=False):
    cid=await new_customer()
    async with SessionLocal() as first,SessionLocal() as second,SessionLocal() as approver:
        initial=Gate(first,Customer,False)
        job=asyncio.create_task(settings.run_public_pool_recycle(initial,uids[2]))
        await wait_enter(initial,job)
        created=await settings.run_public_pool_recycle(second,uids[2]);assert created['nominated_count']==1
        if approval:
            row=await approver.get(Candidate,created['candidates'][0]['candidate_id'])
            gate=Gate(approver,Candidate,True)
            approving=asyncio.create_task(settings.decide_candidate(gate,candidate=row,decision='approve',operator_id=uids[2],allow_early=True,note='isolated lock regression'))
            await wait_enter(gate,approving)
        initial.resume.set()
        result=await asyncio.wait_for(job,5)
        assert result['nominated_count']==0 and result['already_open_count']==1 and result['notified_count']==0
        if approval:
            gate.resume.set();await asyncio.wait_for(approving,5)
            async with SessionLocal() as verify:
                assert (await verify.get(Candidate,row.id)).status=='executed'
        async with SessionLocal() as verify:
            count=(await verify.execute(select(Candidate).where(Candidate.customer_id==cid))).scalars().all()
            assert len(count)==1
    await retire(cid);print('late open candidate rechecked; '+('candidate-first approval finishes without lock cycle' if approval else 'no duplicate notice'))
async def held_scan():
    cid=await new_customer()
    async with SessionLocal() as first,SessionLocal() as other,SessionLocal() as writer:
        gate=Gate(first,Customer,True)
        scan_pid=await first.scalar(text('select pg_backend_pid()'))
        job=asyncio.create_task(settings.run_public_pool_recycle(gate,uids[2]))
        await wait_enter(gate,job)
        result=await asyncio.wait_for(settings.run_public_pool_recycle(other,uids[2]),5)
        assert result['nominated_count']==0
        c=await writer.get(Customer,cid)
        user=CurrentUser(await writer.get(User,uids[2]),roles=['admin'],permissions={'customer:assign'},data_scope='all')
        writer_pid=await writer.scalar(text('select pg_backend_pid()'))
        async def transfer():
            await customers.transfer_customer(writer,user,c,uids[1],'isolated held scan')
            await writer.commit()
        transfer_job=asyncio.create_task(transfer())
        async with SessionLocal() as observer:
            for _ in range(100):
                assert not transfer_job.done(),'transfer did not wait for scan customer lock'
                blocked=await observer.scalar(text('select pg_blocking_pids(:pid)'),{'pid':writer_pid})
                if scan_pid in blocked:break
                await asyncio.sleep(0.01)
            else:raise AssertionError('expected real PG lock wait not observed')
        gate.resume.set();result=await asyncio.wait_for(job,5);assert result['nominated_count']==1
        await asyncio.wait_for(transfer_job,5)
        async with SessionLocal() as verify:
            assert (await verify.get(Customer,cid)).owner_id==uids[1]
    await retire(cid);print('second scan skips lock; transfer actually waits (pg_blocking_pids), then finishes')
async def protection_entry(mode):
    async with SessionLocal() as s:
        c=Customer(name='protection-'+mode+'-'+uuid4().hex,owner_id=uids[0],pool_status='private',level='B',last_followup_at=NOW-timedelta(days=60),last_contact_unknown=False,created_at=NOW-timedelta(days=200),updated_at=NOW)
        s.add(c);await s.flush();cid=c.id;cids.append(cid);await s.commit()
    async with SessionLocal() as scan,SessionLocal() as writer:
        gate=Pause(scan);task=asyncio.create_task(settings.run_public_pool_recycle(gate,uids[0]))
        await asyncio.wait_for(gate.entered.wait(),5)
        user=CurrentUser(await writer.get(User,uids[0]),roles=['admin'],permissions={'order:assign','sample:manage','sample:view'},data_scope='all')
        if mode=='order':
            await create_order(OrderCreate(customer_id=cid,items=[{'sku_id':skids[0],'quantity':'1','unit_price':'1'}]),req,user,writer)
        else:
            await create_sample(SampleCreate(customer_id=cid,use_dingtalk=mode=='oa_draft',items=[{'sku_id':skids[0],'quantity':'1'}]),req,user,writer)
        # Routes really commit and record progress (or intentionally leave an OA draft).
        async with SessionLocal() as verify:
            c=await verify.get(Customer,cid)
            protected=(await settings.protection_detail(verify)).get(cid,[])
            assert protected
            print(mode,'progress_updated=',c.last_progress_at is not None,'current_protection=',protected,flush=True)
        gate.resume.set();report=await asyncio.wait_for(task,10)
    async with SessionLocal() as s:
        rows=(await s.execute(select(Candidate).where(Candidate.customer_id==cid))).scalars().all()
        print(mode,'nominated=',report['nominated_count'],'notified=',report['notified_count'],flush=True)
        assert report['nominated_count']==0 and report['notified_count']==0 and not rows, 'new business must prevent stale nomination'
        if mode=='oa_draft':assert report['protected_count']==1
        c=await s.get(Customer,cid);assert c.owner_id==uids[0];c.deleted_at=NOW;await s.commit()

async def draft_waits_for_scan():
    cid=await new_customer()
    async with SessionLocal() as scanner,SessionLocal() as writer:
        gate=Gate(scanner,Customer,True)
        scan_pid=await scanner.scalar(text('select pg_backend_pid()'))
        job=asyncio.create_task(settings.run_public_pool_recycle(gate,uids[2]))
        await wait_enter(gate,job)
        user=CurrentUser(await writer.get(User,uids[0]),roles=['admin'],permissions={'sample:manage'},data_scope='all')
        writer_pid=await writer.scalar(text('select pg_backend_pid()'))
        creating=asyncio.create_task(create_sample(SampleCreate(customer_id=cid,use_dingtalk=True,items=[{'sku_id':skids[0],'quantity':'1'}]),req,user,writer))
        async with SessionLocal() as observer:
            for _ in range(100):
                assert not creating.done(),'new sample unexpectedly bypassed customer FK lock'
                blocked=await observer.scalar(text('select pg_blocking_pids(:pid)'),{'pid':writer_pid})
                if scan_pid in blocked:break
                await asyncio.sleep(0.01)
            else:raise AssertionError('sample creation lock wait not observed')
        gate.resume.set();report=await asyncio.wait_for(job,5);assert report['nominated_count']==1
        await asyncio.wait_for(creating,5)
    await retire(cid);print('scan first: OA draft really waits on FK/customer lock and commits afterwards, no deadlock')

async def cleanup():
    async with SessionLocal() as s:
        oids=select(SalesOrder.id).where(SalesOrder.customer_id.in_(cids))
        sids=select(SampleRequest.id).where(SampleRequest.customer_id.in_(cids))
        for model,condition in (
            (FollowUp,FollowUp.customer_id.in_(cids)),
            (BusinessEvent,BusinessEvent.customer_id.in_(cids)),
            (SalesOrderItem,SalesOrderItem.order_id.in_(oids)),
            (OrderStatusHistory,OrderStatusHistory.order_id.in_(oids)),
            (SalesOrder,SalesOrder.customer_id.in_(cids)),
            (SampleItem,SampleItem.sample_request_id.in_(sids)),
            (SampleRequest,SampleRequest.customer_id.in_(cids)),
        ):
            await s.execute(delete(model).where(condition))
        await s.execute(delete(Notification).where(Notification.user_id.in_(uids)))
        await s.execute(delete(AuditLog).where(AuditLog.operator_id.in_(uids)))
        await s.execute(delete(Candidate).where(Candidate.customer_id.in_(cids)))
        await s.execute(delete(CustomerOwnerHistory).where(CustomerOwnerHistory.customer_id.in_(cids)))
        await s.execute(delete(Customer).where(Customer.id.in_(cids)))
        await s.execute(delete(PublicPoolRule).where(PublicPoolRule.id.in_(rids)))
        await s.execute(delete(Sku).where(Sku.id.in_(skids)))
        await s.execute(delete(Product).where(Product.id.in_(pids)))
        await s.execute(delete(User).where(User.id.in_(uids)));await s.commit()
async def main():
    try:
        await setup()
        for mode in ('transfer','delete','activity'):await scenario(mode)
        await busy_catchup();await late_candidate();await late_candidate(approval=True);await held_scan()
        async with SessionLocal() as s:
            p=Product(name='synthetic pool protection',status='active');s.add(p);await s.flush();pids.append(p.id)
            sku=Sku(product_id=p.id,sku_code='scan-'+uuid4().hex,name='synthetic',status='active');s.add(sku);await s.flush();skids.append(sku.id);await s.commit()
        for mode in ('order','sample','oa_draft'):await protection_entry(mode)
        await draft_waits_for_scan()
    finally:await cleanup();await engine.dispose()
async def run_in_fresh_database():
    """全局扫描不能借用CI共用库；父进程创建临时空库，迁移后交给子进程。"""
    import subprocess
    import sys
    from pathlib import Path
    import asyncpg
    from sqlalchemy.engine import make_url

    source = make_url(os.environ['DATABASE_URL'])
    database = 'crm_iso_pool_scan_' + uuid4().hex[:20]
    admin = await asyncpg.connect(host=source.host, port=source.port or 5432,
                                  user=source.username, password=source.password, database=source.database)
    created = False
    try:
        await admin.execute(f'CREATE DATABASE "{database}"')
        created = True
        env = dict(os.environ, DATABASE_URL=source.set(database=database).render_as_string(hide_password=False),
                   CRM_POOL_SCAN_DB_CHILD='1')
        backend = Path(__file__).resolve().parents[1]
        env['PYTHONPATH'] = str(backend)
        migration = await asyncio.create_subprocess_exec(
            sys.executable, '-m', 'alembic', 'upgrade', 'head', cwd=backend, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        try:
            _, error = await asyncio.wait_for(migration.communicate(), 60)
        finally:
            if migration.returncode is None:
                migration.kill()
                await migration.wait()
        if migration.returncode:
            raise RuntimeError('隔离迁移失败：' + error.decode(errors='replace')[-2000:])
        check = await asyncio.create_subprocess_exec(sys.executable, str(Path(__file__).resolve()), cwd=backend, env=env)
        try:
            code = await asyncio.wait_for(check.wait(), 60)
        finally:
            if check.returncode is None:
                check.kill()
                await check.wait()
        if code:
            raise RuntimeError(f'独立库公海扫描回归失败，退出码{code}')
    finally:
        if created:
            await admin.execute(f'DROP DATABASE "{database}" WITH (FORCE)')
        await admin.close()


if __name__ == '__main__':
    if os.environ.get('CRM_POOL_SCAN_DB_CHILD') == '1':
        asyncio.run(main())
    else:
        asyncio.run(run_in_fresh_database())
