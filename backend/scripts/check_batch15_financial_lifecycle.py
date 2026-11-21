"""第十五批真 PostgreSQL：同来源并发核定、取消凭证/交期、月结替代生命周期。"""
import asyncio
from datetime import UTC,datetime,timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select,text,delete
from starlette.requests import Request
from _test_support import require_isolated_db
require_isolated_db()

from app.core.database import SessionLocal
from app.core.deps import CurrentUser
from app.core.errors import AppError
from app.core.timebase import today_business
from app.modules.erp import reconcile
from app.modules.integration.model import IntegrationDiff,FinancialAdjustment
from app.modules.order import router as orders,schedule
from app.modules.order.model import SalesOrder,OrderScheduleChange,OrderMilestone,OrderStatusHistory
from app.modules.payment.model import PaymentRecord
from app.modules.customer.model import Customer
from app.modules.user.model import User
from app.modules.contract import service as contracts
from app.modules.contract.model import ContractDocument,ContractTemplate
from app.modules.file.model import FileRecord,BusinessFile
from app.core.audit import AuditLog
from app.modules.task.model import Task


FIX={}


async def main():
    tag='CHK15-'+uuid4().hex[:8]
    FIX['tag']=tag
    async with SessionLocal() as s:
        actor=(await s.execute(select(User).where(User.status=='active').limit(1))).scalars().first()
        customer=(await s.execute(select(Customer).where(Customer.deleted_at.is_(None)).limit(1))).scalars().first()
        user=CurrentUser(actor,{'order:manage'},['admin'],'all')
        diffs=[]
        for amount in ('100','200'):
            d=IntegrationDiff(diff_key=tag+amount,domain='order',system_type='ERP',shop_id='S',
                object_type='aftersale',external_id=tag,diff_type='aftersale_needs_review',status='open',
                incoming_value={'amount':amount,'currency':'CNY'})
            s.add(d);diffs.append(d)
        await s.commit();first_id,second_id=[d.id for d in diffs]
    async with SessionLocal() as first:
        d=await first.get(IntegrationDiff,first_id)
        await reconcile.confirm_diff(first,d,resolution='manual',note='初次核定',operator_id=user.id)
        async def second_confirm():
            async with SessionLocal() as s:
                await s.execute(text("SET LOCAL application_name='batch15-source-confirm'"))
                d=await s.get(IntegrationDiff,second_id)
                result=await reconcile.confirm_diff(s,d,resolution='manual',note='金额更正',operator_id=user.id)
                await s.commit();return result
        task=asyncio.create_task(second_confirm())
        try:
            async with SessionLocal() as observer:
                for _ in range(100):
                    waiting=(await observer.execute(text("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE application_name='batch15-source-confirm' AND wait_event_type='Lock')"))).scalar_one()
                    if waiting:break
                    assert not task.done(),'第二个事务应等待首笔提交'
                    await asyncio.sleep(.03)
                else:raise AssertionError('未观察到锁等待')
            await first.commit()
            result=await asyncio.wait_for(task,10)
            assert result['financial_adjustment']['amount']=='200.00'
        finally:
            if not task.done():task.cancel();await asyncio.gather(task,return_exceptions=True)
    async with SessionLocal() as s:
        ledgers=(await s.execute(select(FinancialAdjustment).where(FinancialAdjustment.source_key==
            reconcile.build_adjustment_source_key(domain='order',system_type='ERP',shop_id='S',object_type='aftersale',subject=tag)))).scalars().all()
        assert len(ledgers)==1 and ledgers[0].amount==Decimal('200')
    print('PASS concurrent versions -> one pending ledger, amount 200, no unique-key 500',flush=True)

    async with SessionLocal() as s:
        o=SalesOrder(order_no=tag,customer_id=customer.id,owner_id=user.id,sales_owner_id=user.id,
            total_amount=100,currency='CNY',status='pending',delivery_kind='shipping',transit_days=0)
        s.add(o);await s.flush();FIX["order_id"]=o.id
        from app.modules.order.milestones import MILESTONE_NODES
        change=await schedule.create_change(s,o,user=user,new_delivery_date=today_business()+timedelta(days=30),
            reason='正常排期',plan_offsets={key:1 for key,_,_ in MILESTONE_NODES})
        p=PaymentRecord(order_id=o.id,received_date=today_business(),received_amount=1,currency='CNY',status='pending',
            voucher_note='中'*255,created_by=user.id,created_at=datetime.now(UTC))
        s.add(p);await s.commit();FIX["payment_id"]=p.id
        await orders.cancel_order(o.id,Request({'type':'http','method':'POST','path':'/','headers':[],
            'client':('127.0.0.1',1)}),user,s)
        assert p.status=='rejected' and p.voucher_note=='中'*255
        assert change.status=='cancelled'
        try:await schedule.confirm_change(s,o,change,user=user,remark='取消后不得确认')
        except AppError as e:assert e.http_status==422
        else:raise AssertionError('取消订单的变更仍可确认')
    print('PASS order cancel with full 255-character voucher -> note intact, schedule closed',flush=True)

    async with SessionLocal() as s:
        template=(await s.execute(select(ContractTemplate).where(ContractTemplate.doc_type=='monthly').limit(1))).scalars().first()
        if template is None:
            template=ContractTemplate(doc_type='monthly',name=tag,body='{{customer.name}}',version=1,enabled=True,created_by=user.id,created_at=datetime.now(UTC))
            s.add(template);await s.flush()
        parent=ContractDocument(doc_no=tag+'-P',doc_type='monthly',title='旧月结',customer_id=customer.id,
            template_id=template.id,content_snapshot='原件',status='signed',effective_date=today_business()-timedelta(days=10),
            expiry_date=today_business()+timedelta(days=5),created_at=datetime.now(UTC),created_by=user.id)
        s.add(parent);await s.flush();FIX["parent_id"]=parent.id
        reminder=Task(title='旧月结续签',status='pending',owner_id=user.id,customer_id=customer.id,source='system',
            source_business_type='contract',source_business_id=parent.id,source_key='contract:monthly:'+str(parent.id)+':'+str(parent.expiry_date))
        s.add(reminder)
        from app.modules.contract.schema import DocumentCreate
        child,_=await contracts.generate_document(s,payload=DocumentCreate(template_id=template.id,
            customer_id=customer.id,parent_id=parent.id,supersede_parent=True,effective_date=today_business()+timedelta(days=1),
            expiry_date=today_business()+timedelta(days=365)),user_id=user.id)
        FIX["child_id"]=child.id
        assert reminder.status=='pending' and not contracts.is_superseded(parent)
        f=FileRecord(storage_provider='local',object_key=tag,file_name='signed.pdf',size=1,uploaded_by=user.id)
        s.add(f);await s.flush();FIX["file_id"]=f.id
        await contracts.sign_document(s,child,file_id=f.id,note='签回')
        assert reminder.status=='pending'
        await contracts.refresh_supersession(s,parent.id,today=today_business()+timedelta(days=1))
        assert reminder.status=='cancelled'
        await contracts.void_document(s,child,reason='新协议撤销')
        assert reminder.status=='pending' and not contracts.is_superseded(parent)
        await s.commit()
    print('PASS monthly draft -> future signed -> effective -> void, old reminder restores',flush=True)


async def cleanup():
    if not FIX:
        return
    async with SessionLocal() as s:
        tag=FIX['tag']
        key=reconcile.build_adjustment_source_key(domain='order',system_type='ERP',shop_id='S',object_type='aftersale',subject=tag)
        await s.execute(delete(FinancialAdjustment).where(FinancialAdjustment.source_key==key))
        await s.execute(delete(IntegrationDiff).where(IntegrationDiff.external_id==tag))
        oid=FIX.get('order_id')
        if oid:
            await s.execute(delete(AuditLog).where(AuditLog.business_type=='order',AuditLog.business_id==oid))
            await s.execute(delete(AuditLog).where(AuditLog.business_type=='payment',AuditLog.business_id==FIX.get('payment_id')))
            for model in (OrderScheduleChange,OrderMilestone,OrderStatusHistory,PaymentRecord):
                await s.execute(delete(model).where(model.order_id==oid))
            await s.execute(delete(SalesOrder).where(SalesOrder.id==oid))
        ids=[FIX[k] for k in ('child_id','parent_id') if k in FIX]
        if ids:
            await s.execute(delete(Task).where(Task.source_business_type=='contract',Task.source_business_id.in_(ids)))
            await s.execute(delete(BusinessFile).where(BusinessFile.business_type=='contract',BusinessFile.business_id.in_(ids)))
            if FIX.get('child_id'):
                await s.execute(delete(ContractDocument).where(ContractDocument.id==FIX['child_id']))
            await s.execute(delete(ContractDocument).where(ContractDocument.id==FIX.get('parent_id')))
        if FIX.get('file_id'):
            await s.execute(delete(FileRecord).where(FileRecord.id==FIX['file_id']))
        await s.execute(delete(ContractTemplate).where(ContractTemplate.name==tag))
        await s.commit()
    print('CLEANED batch15 fixture objects',flush=True)


async def run_and_cleanup():
    try:
        await main()
    finally:
        await cleanup()


asyncio.run(run_and_cleanup())
