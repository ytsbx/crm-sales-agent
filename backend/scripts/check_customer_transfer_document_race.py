import os,asyncio,tempfile,hashlib
from urllib.parse import urlparse
from _test_support import require_isolated_db
_name = require_isolated_db()
_url = urlparse(os.environ['DATABASE_URL'])
if _url.hostname not in ('127.0.0.1', 'localhost') or not _url.scheme.startswith('postgresql') or not (_name.startswith(('crm_iso','crm_check','crm_test')) or _name.endswith('_test')):
    raise SystemExit('只允许显式本机一次性PostgreSQL')
from pathlib import Path
from uuid import uuid4
from datetime import UTC,datetime
scratch=tempfile.TemporaryDirectory(prefix='crm-contract-review-')
os.environ.update(FILE_ROOT=scratch.name,WECOM_PUSH_OFF='1',DINGTALK_PUSH_OFF='1',SCHEDULER_ENABLED='0',SCHEDULER_OA_SYNC_ENABLED='0',DINGTALK_OA_STREAM_ENABLED='0')
from sqlalchemy import select,delete,text
from sqlalchemy.exc import DBAPIError
from starlette.requests import Request
__import__('app.main')  # Register models without starting application lifespan.
from app.core.database import SessionLocal,engine
from app.core.deps import CurrentUser
from app.core.errors import AppError
from app.core.audit import AuditLog
from app.modules.customer.model import Customer,CustomerOwnerHistory
from app.modules.customer.service import transfer_customer
from app.modules.user.model import User
from app.modules.order.model import SalesOrder, OrderMilestone, OrderStatusHistory
from app.modules.order import router as order_router
from app.modules.payment import router as payment_router, service as payment_service
from app.modules.payment.model import PaymentRecord, PaymentAllocation, ReceivablePlan
from app.modules.payment.schema import PaymentAction
from app.core.timebase import today_business
from app.modules.contract.model import ContractDocument
from app.modules.contract import router
from app.modules.contract.schema import DocumentSign,DocumentVoid
from app.modules.file.model import FileRecord,BusinessFile
from app.modules.dingtalk.model import OaInstance
from app.modules.followup.model import FollowUp
from app.modules.notification.model import Notification,BusinessEvent
now=datetime.now(UTC);uids=[];cids=[];oids=[];dids=[];fids=[]
req=Request({'type':'http','headers':[],'client':('127.0.0.1',1)})
class Gate:
    def __init__(self,s,model=ContractDocument):self.s=s;self.model=model;self.entered=asyncio.Event();self.resume=asyncio.Event();self.once=False
    def __getattr__(self,k):return getattr(self.s,k)
    async def execute(self,stmt,*a,**kw):
        result=await self.s.execute(stmt,*a,**kw)
        cols=getattr(stmt,'column_descriptions',[])
        if not self.once and cols and cols[0].get('expr') is self.model and getattr(stmt,'_for_update_arg',None) is not None:
            self.once=True;self.entered.set();await asyncio.wait_for(self.resume.wait(),10)
        return result
async def fixture():
    async with SessionLocal() as s:
        c=Customer(name='synthetic contract '+uuid4().hex,owner_id=uids[0],pool_status='private');s.add(c);await s.flush();cid=c.id;cids.append(cid)
        order=SalesOrder(order_no='ISO-CON-'+uuid4().hex[:12],customer_id=cid,owner_id=uids[0],sales_owner_id=uids[0],currency='CNY',total_amount=1,status='pending');s.add(order);await s.flush();oids.append(order.id)
        files=[]
        for kind in ('original','signed-A','signed-B'):
            content=b'%PDF-1.4\nsynthetic '+kind.encode();key=uuid4().hex+'.pdf';Path(scratch.name,key).write_bytes(content)
            f=FileRecord(object_key=key,file_name=kind+'.pdf',mime_type='application/pdf',size=len(content),checksum=hashlib.sha256(content).hexdigest(),uploaded_by=uids[0]);s.add(f);await s.flush();files.append(f.id);fids.append(f.id)
        doc=ContractDocument(doc_no='ISO-DOC-'+uuid4().hex[:12],doc_type='contract',title='synthetic contract',customer_id=cid,order_id=order.id,content_snapshot='synthetic',status='draft',filled_data={'_original_file_id':files[0]},created_by=uids[0],created_at=now);s.add(doc);await s.flush();dids.append(doc.id)
        s.add(OaInstance(inquiry_id=doc.id,inquiry_version=1,oa_type='contract',submit_round=1,idempotency_key='isolated-contract-'+uuid4().hex,instance_id='SYNTHETIC-'+uuid4().hex,status='approved',created_at=now));await s.commit()
        return cid,order.id,doc.id,files
async def actor(s,regular=False):return CurrentUser(await s.get(User,uids[0]),{'order:manage','order:view','customer:assign','payment:manage','payment:view'},['salesperson'] if regular else ['admin'],'all')
async def operation(s,kind,did,files,user,second=False):
    try:
        if kind=='sign':await router.sign_document(did,DocumentSign(file_id=files[2 if second else 1]),req,user,s)
        else:await router.void_document(did,DocumentVoid(reason='synthetic lifecycle review'),req,user,s)
        return 200
    except AppError as e:await s.rollback();return e.http_status
    except DBAPIError as e:await s.rollback();return getattr(e.orig,'sqlstate',type(e).__name__)
async def waiting(pid,blocker,job):
    async with SessionLocal() as s:
        for _ in range(100):
            assert not job.done(),'second request did not wait'
            if blocker in await s.scalar(text('select pg_blocking_pids(:pid)'),{'pid':pid}):return
            await asyncio.sleep(.01)
        raise AssertionError('real PG wait not observed')
async def state_case(first,second,regular,expected,status):
    cid,oid,did,files=await fixture()
    async with SessionLocal() as a,SessionLocal() as b:
        pa=await a.scalar(text('select pg_backend_pid()'));pb=await b.scalar(text('select pg_backend_pid()'))
        gate=Gate(a);usera=await actor(a);userb=await actor(b,regular)
        one=asyncio.create_task(operation(gate,first,did,files,usera));await asyncio.wait_for(gate.entered.wait(),5)
        two=asyncio.create_task(operation(b,second,did,files,userb,True));await waiting(pb,pa,two);gate.resume.set()
        result=await asyncio.wait_for(asyncio.gather(one,two,return_exceptions=True),10);assert result==expected,(first,second,result)
    async with SessionLocal() as s:
        doc=await s.get(ContractDocument,did);assert doc.status==status
        links=(await s.execute(select(BusinessFile).where(BusinessFile.business_type=='contract',BusinessFile.business_id==did,BusinessFile.category=='signed'))).scalars().all()
        signed=first=='sign';assert len(links)==int(signed) and bool(doc.signed_at)==signed
        if signed:assert links[0].file_id==files[1]
        audits=(await s.execute(select(AuditLog).where(AuditLog.business_type=='contract',AuditLog.business_id==did,AuditLog.action.in_(('sign','void'))))).scalars().all()
        assert len(audits)==result.count(200)
        events=(await s.execute(select(BusinessEvent).where(BusinessEvent.customer_id==cid))).scalars().all();assert len(events)==result.count(200)
        for fid in files:
            record=await s.get(FileRecord,fid);assert Path(scratch.name,record.object_key).read_bytes()
    print(first+' -> '+second+' regular_second='+str(regular)+': '+str(result)+' final='+status+' signed_refs='+str(len(links))+' audits/events/files correct',flush=True)
async def receipt(oid):
    async with SessionLocal() as s:
        plan=ReceivablePlan(order_id=oid,plan_name='synthetic period',due_date=today_business(),amount=1,currency='CNY',status='pending',created_at=now)
        s.add(plan);await s.flush()
        row=PaymentRecord(order_id=oid,receivable_plan_id=plan.id,received_amount=1,currency='CNY',received_date=today_business(),status='pending',allocation_mode='auto',created_by=uids[0],created_at=now)
        s.add(row);await s.flush();pid=row.id;await s.commit();return pid
async def transfer_case(kind, first, own_scope=False):
    cid,oid,did,files=await fixture();pid=await receipt(oid)
    async with SessionLocal() as business,SessionLocal() as writer:
        pb=await business.scalar(text('select pg_backend_pid()'));pw=await writer.scalar(text('select pg_backend_pid()'))
        user=await actor(business)
        if own_scope:user=CurrentUser(await business.get(User,uids[0]),{'order:manage','order:view','payment:manage','payment:view'},['salesperson'],'self')
        # Preload all scope objects, so a lock wait must refresh the identity map.
        await business.get(Customer,cid);await business.get(SalesOrder,oid);await business.get(ContractDocument,did)
        bgate=Gate(business,FileRecord if kind=='sign' else SalesOrder)
        wgate=Gate(writer,Customer)
        async def changing():
            try:
                c=await writer.get(Customer,cid)
                await transfer_customer(wgate if first=='transfer' else writer,await actor(writer),c,uids[1],'isolated transfer serialization')
                await writer.commit();return 200
            except DBAPIError as e:await writer.rollback();return getattr(e.orig,'sqlstate',type(e).__name__)
        async def doing():
            try:
                if kind=='sign':return await operation(bgate if first=='business' else business,'sign',did,files,user)
                await payment_router.confirm_payment(pid,PaymentAction(),req,user,bgate if first=='business' else business);return 200
            except AppError as e:await business.rollback();return e.http_status
            except DBAPIError as e:await business.rollback();return getattr(e.orig,'sqlstate',type(e).__name__)
        if first=='business':
            one=asyncio.create_task(doing());await asyncio.wait_for(bgate.entered.wait(),5)
            two=asyncio.create_task(changing());await waiting(pw,pb,two);bgate.resume.set()
        else:
            one=asyncio.create_task(changing());await asyncio.wait_for(wgate.entered.wait(),5)
            two=asyncio.create_task(doing());await waiting(pb,pw,two);wgate.resume.set()
        results=await asyncio.wait_for(asyncio.gather(one,two,return_exceptions=True),10)
        expected=[200,403] if own_scope else [200,200]
        assert results==expected,(kind,first,own_scope,results)
    async with SessionLocal() as s:
        c=await s.get(Customer,cid);order=await s.get(SalesOrder,oid);doc=await s.get(ContractDocument,did);payment=await s.get(PaymentRecord,pid)
        assert c.owner_id==order.owner_id==uids[1]
        signed=(kind=='sign' and not own_scope)
        links=(await s.execute(select(BusinessFile).where(BusinessFile.business_type=='contract',BusinessFile.business_id==did,BusinessFile.category=='signed'))).scalars().all()
        assert (doc.status=='signed')==signed and len(links)==int(signed)
        confirmed=(kind=='confirm' and not own_scope)
        assert (payment.status=='confirmed')==confirmed
        totals=(await payment_service.allocation_totals(s,[oid])).get(oid,{'received_amount':0,'allocated_amount':0})
        assert totals['received_amount']==int(confirmed) and totals['allocated_amount']==int(confirmed)
        action='sign' if kind=='sign' else 'confirm';business_type='contract' if kind=='sign' else 'payment';bid=did if kind=='sign' else pid
        audits=(await s.execute(select(AuditLog.id).where(AuditLog.business_type==business_type,AuditLog.business_id==bid,AuditLog.action==action))).all()
        assert len(audits)==int(not own_scope)
        events=(await s.execute(select(BusinessEvent.id).where(BusinessEvent.customer_id==cid))).all();assert len(events)==int(not own_scope)
    print(kind+' / '+first+' first / self='+str(own_scope)+': '+str(results)+' scope, files, cash, audit, events correct',flush=True)
async def compatibility_case():
    cid,oid,did,files=await fixture();other_cid,_,_,_=await fixture()
    async with SessionLocal() as business,SessionLocal() as writer:
        gate=Gate(business,FileRecord);user=await actor(business)
        one=asyncio.create_task(operation(gate,'sign',did,files,user));await asyncio.wait_for(gate.entered.wait(),5)
        # Ordinary progress updates (NO KEY UPDATE) must remain compatible with KEY SHARE.
        from app.modules.customer.service import touch_progress
        await asyncio.wait_for(touch_progress(writer,cid),2);await writer.commit()
        c=await writer.get(Customer,other_cid)
        await asyncio.wait_for(transfer_customer(writer,await actor(writer),c,uids[1],'different customer'),2);await writer.commit()
        gate.resume.set();assert await asyncio.wait_for(one,10)==200
    print('same-customer progress update + other-customer transfer: no new blocking',flush=True)
async def cancel_case():
    cid,oid,did,files=await fixture();pid=await receipt(oid)
    async with SessionLocal() as cancelling,SessionLocal() as confirming:
        pc=await cancelling.scalar(text('select pg_backend_pid()'));pp=await confirming.scalar(text('select pg_backend_pid()'))
        gate=Gate(cancelling,SalesOrder)
        async def cancel():
            await order_router.cancel_order(oid,req,await actor(cancelling),gate);return 200
        async def confirm():
            try:await payment_router.confirm_payment(pid,PaymentAction(),req,await actor(confirming),confirming);return 200
            except AppError as e:await confirming.rollback();return e.http_status
        one=asyncio.create_task(cancel());await asyncio.wait_for(gate.entered.wait(),5)
        two=asyncio.create_task(confirm());await waiting(pp,pc,two);gate.resume.set()
        results=await asyncio.wait_for(asyncio.gather(one,two,return_exceptions=True),10)
        assert results==[200,400],results
    async with SessionLocal() as s:
        assert (await s.get(SalesOrder,oid)).status=='cancelled'
        assert (await s.get(PaymentRecord,pid)).status=='rejected'
        assert not (await payment_service.allocation_totals(s,[oid])).get(oid)
    print('order cancel -> confirmation holding customer KEY SHARE: 200/400 (cancel rejected pending payment), no deadlock',flush=True)
async def cleanup():
    async with SessionLocal() as s:
        for model,condition in (
            (Notification,Notification.user_id.in_(uids)),(FollowUp,FollowUp.customer_id.in_(cids)),(BusinessEvent,BusinessEvent.customer_id.in_(cids)),(AuditLog,AuditLog.operator_id.in_(uids)),
            (BusinessFile,BusinessFile.business_type=='contract'),
        ):
            if model is BusinessFile:condition=condition & BusinessFile.business_id.in_(dids)
            await s.execute(delete(model).where(condition))
        await s.execute(delete(OaInstance).where(OaInstance.oa_type=='contract',OaInstance.inquiry_id.in_(dids)))
        await s.execute(delete(ContractDocument).where(ContractDocument.id.in_(dids)))
        await s.execute(delete(FileRecord).where(FileRecord.id.in_(fids)))
        payment_ids=select(PaymentRecord.id).where(PaymentRecord.order_id.in_(oids))
        await s.execute(delete(PaymentAllocation).where(PaymentAllocation.payment_id.in_(payment_ids)))
        await s.execute(delete(PaymentRecord).where(PaymentRecord.order_id.in_(oids)))
        await s.execute(delete(ReceivablePlan).where(ReceivablePlan.order_id.in_(oids)))
        await s.execute(delete(OrderStatusHistory).where(OrderStatusHistory.order_id.in_(oids)))
        await s.execute(delete(OrderMilestone).where(OrderMilestone.order_id.in_(oids)))
        await s.execute(delete(SalesOrder).where(SalesOrder.id.in_(oids)))
        await s.execute(delete(CustomerOwnerHistory).where(CustomerOwnerHistory.customer_id.in_(cids)))
        await s.execute(delete(Customer).where(Customer.id.in_(cids)))
        await s.execute(delete(User).where(User.id.in_(uids)));await s.commit()
async def main():
    try:
        async with SessionLocal() as s:
            for _ in range(2):
                u=User(username='contract-review-'+uuid4().hex,name='synthetic',password_hash='!',status='active');s.add(u);await s.flush();uids.append(u.id)
            await s.commit()
        for args in (('sign','void',False,[200,200],'void'),('void','sign',False,[200,400],'void'),('sign','sign',False,[200,400],'signed'),('sign','void',True,[200,403],'signed'),('void','void',False,[200,400],'void')):await state_case(*args)
        for kind in ('sign','confirm'):
            for first in ('business','transfer'):await transfer_case(kind,first)
            await transfer_case(kind,'transfer',True)
        await compatibility_case()
        await cancel_case()
    finally:await cleanup();await engine.dispose()
try:asyncio.run(main())
finally:scratch.cleanup()
