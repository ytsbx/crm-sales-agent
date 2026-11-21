"""第十五批：真实服务 + 隔离 SQLite；来源核定竞争另用 PostgreSQL 验证。"""
import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.requests import Request

from conftest import SyncSessionAsAsync, make_engine
from app.core.audit import AuditLog
from app.core.base import Base
from app.core.deps import CurrentUser
from app.core.errors import AppError
from app.core.timebase import today_business
from app.modules.contract import service as contract
from app.modules.contract.model import ContractDocument, ContractTemplate
from app.modules.contract.schema import DocumentCreate
from app.modules.customer.model import Customer
from app.modules.file.model import BusinessFile, FileRecord
from app.modules.notification.model import Notification
from app.modules.order import schedule, router as orders
from app.modules.order.model import SalesOrder, OrderScheduleChange, OrderShipmentBatch, OrderStatusHistory
from app.modules.payment.model import PaymentRecord
from app.modules.settings.model import SystemSetting
from app.modules.task.model import Task
from app.modules.user.model import User


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def db():
    engine=make_engine()
    Base.metadata.create_all(engine,tables=[x.__table__ for x in (
        ContractDocument,ContractTemplate,Task,Notification,AuditLog,FileRecord,BusinessFile,
        OrderScheduleChange,OrderShipmentBatch,OrderStatusHistory,SystemSetting)])
    s=SyncSessionAsAsync(Session(engine))
    user=User(id=1,username='actor',password_hash='x',name='经办人',status='active')
    customer=Customer(name='测试客户',owner_id=1)
    template=ContractTemplate(doc_type='monthly',name='测试月结',version=1,body='{{customer.name}}',enabled=True,created_at=datetime.now(UTC))
    for row in (user,customer,template):s.add(row)
    run(s.flush())
    return s,CurrentUser(user,set(),['admin'],'all'),customer,template


def document(db, *, parent=None, status='signed', effective=None, replace=True):
    s,user,c,t=db
    d=ContractDocument(doc_no='CT-'+uuid4().hex[:8],doc_type='monthly',title='月结',
        customer_id=c.id,template_id=t.id,content_snapshot='正文',status=status,
        effective_date=effective or today_business(),expiry_date=today_business()+timedelta(days=5),
        parent_id=parent.id if parent else None,
        filled_data={'_supersedes':{'id':parent.id,'doc_no':parent.doc_no}} if parent and replace else {},
        created_by=user.id,created_at=datetime.now(UTC))
    s.add(d);run(s.flush());return d


def reminder(db, doc, *, status='pending', note=None):
    s,user,c,t=db
    task=Task(title='月结续签',customer_id=c.id,owner_id=user.id,status=status,source='system',
        source_business_type='contract',source_business_id=doc.id,
        source_key='contract:monthly:'+str(doc.id)+':'+str(doc.expiry_date),completion_note=note)
    s.add(task);run(s.flush());return task


def sign(db,doc):
    s,*_=db
    f=FileRecord(storage_provider='local',object_key=uuid4().hex,file_name='signed.pdf',size=1,
                 mime_type='application/pdf',created_at=datetime.now(UTC))
    s.add(f);run(s.flush())
    run(contract.sign_document(s,doc,file_id=f.id,note='实测签回'))


def test_generation_only_records_intent_does_not_cancel_parent(db,monkeypatch):
    s,user,c,t=db;parent=document(db);task=reminder(db,parent)
    async def number(*a,**kw):return 'CT-'+uuid4().hex[:8]
    from app.modules.settings import numbering
    monkeypatch.setattr(numbering,'generate_for',number)
    doc,_=run(contract.generate_document(s,user_id=user.id,payload=DocumentCreate(
        customer_id=c.id,template_id=t.id,parent_id=parent.id,supersede_parent=True,
        effective_date=today_business(),expiry_date=today_business()+timedelta(days=365))))
    assert doc.status=='draft' and doc.filled_data['_supersedes']['id']==parent.id
    assert task.status=='pending' and not contract.is_superseded(parent)
    sign(db,doc)
    assert task.status=='cancelled' and contract.is_superseded(parent)


def test_signed_future_replacement_waits_until_effective_day(db):
    s,*_=db;parent=document(db);task=reminder(db,parent)
    tomorrow=today_business()+timedelta(days=1)
    child=document(db,parent=parent,status='draft',effective=tomorrow)
    sign(db,child)
    assert task.status=='pending' and not contract.is_superseded(parent)
    assert run(contract.refresh_supersession(s,parent.id,today=tomorrow))
    assert task.status=='cancelled' and contract.is_superseded(parent)
    assert '_supersession_task_states' not in contract.serialize_document(parent)['filled_data']
    run(contract.refresh_supersession(s,parent.id,today=tomorrow))
    assert len(parent.filled_data['_supersession_task_states'])==1


def test_void_replacement_restores_only_its_auto_cancelled_task(db):
    s,*_=db;parent=document(db);pending=reminder(db,parent,status='doing',note='原经办说明')
    child=document(db,parent=parent,status='draft');sign(db,child)
    # 不同到期周期的历史任务：系统替代不得重开已完成/人工取消项。
    done=Task(title='已完成',status='done',source_business_type='contract',source_business_id=parent.id,source_key='done-'+uuid4().hex)
    cancelled=Task(title='人工取消',status='cancelled',completion_note='人工处理',source_business_type='contract',source_business_id=parent.id,source_key='cancel-'+uuid4().hex)
    s.add(done);s.add(cancelled);run(s.flush())
    run(contract.void_document(s,child,reason='新协议取消'))
    assert not contract.is_superseded(parent)
    assert pending.status=='doing' and pending.completion_note=='原经办说明'
    assert done.status=='done' and cancelled.status=='cancelled'


def test_unchecked_renewal_never_supersedes_parent(db):
    s,*_=db;parent=document(db);task=reminder(db,parent)
    child=document(db,parent=parent,status='draft',replace=False);sign(db,child)
    assert not run(contract.refresh_supersession(s,parent.id)) and task.status=='pending'


def test_other_effective_replacement_keeps_parent_superseded(db):
    s,*_=db;parent=document(db);task=reminder(db,parent)
    a=document(db,parent=parent,status='draft');sign(db,a)
    b=document(db,parent=parent,status='draft');sign(db,b)
    run(contract.void_document(s,b,reason='只撤销第二份'))
    assert contract.is_superseded(parent) and parent.filled_data['_superseded_by']['id']==a.id
    assert task.status=='cancelled'


def test_scan_rechecks_legacy_pointer_to_draft(db):
    s,*_=db;parent=document(db);child=document(db,parent=parent,status='draft')
    parent.filled_data={'_superseded_by':{'id':child.id,'doc_no':child.doc_no}}
    run(s.flush())
    assert run(contract.notify_expiring_monthly(s))==1
    assert not contract.is_superseded(parent)
    assert run(contract.notify_expiring_monthly(s))==0


def test_scan_detects_effective_signed_child_without_parent_marker(db):
    s,*_=db;parent=document(db)
    child=document(db,parent=parent,status='signed')
    child.expiry_date=today_business()+timedelta(days=365);run(s.flush())
    assert run(contract.notify_expiring_monthly(s))==0
    assert contract.is_superseded(parent)


def test_legacy_replacement_without_effective_date_does_not_stop_old_reminder(db):
    s,*_=db;parent=document(db);child=document(db,parent=parent,status='signed')
    child.effective_date=None
    parent.filled_data={'_superseded_by':{'id':child.id,'doc_no':child.doc_no}}
    run(s.flush())
    assert not run(contract.refresh_supersession(s,parent.id))
    assert not contract.is_superseded(parent)


def test_void_new_agreement_does_not_reopen_expired_old_reminder(db):
    s,*_=db;parent=document(db);task=reminder(db,parent)
    child=document(db,parent=parent,status='draft');sign(db,child)
    parent.expiry_date=today_business()-timedelta(days=1);run(s.flush())
    run(contract.void_document(s,child,reason='新协议撤销'))
    assert task.status=='cancelled'


@pytest.mark.parametrize('kind',['missing_parent','wrong_template'])
def test_invalid_replacement_selection_is_rejected_before_generation(db,kind):
    s,user,c,t=db;parent=document(db)
    if kind=='wrong_template':t.doc_type='contract';run(s.flush())
    with pytest.raises(AppError,match='月结协议'):
        run(contract.generate_document(s,user_id=user.id,payload=DocumentCreate(
            template_id=t.id,customer_id=c.id,supersede_parent=True,
            parent_id=None if kind=='missing_parent' else parent.id,
            effective_date=today_business(),expiry_date=today_business()+timedelta(days=365))))
    assert len(run(s.execute(select(ContractDocument))).scalars().all())==1


def order(db,status='pending'):
    s,u,c,t=db
    o=SalesOrder(order_no='SO-'+uuid4().hex[:8],customer_id=c.id,owner_id=u.id,status=status,
        total_amount=Decimal('100'),currency='CNY',delivery_kind='shipping',transit_days=0)
    s.add(o);run(s.flush());return o


def test_cancelled_order_cannot_create_schedule_change(db):
    s,u,*_=db;o=order(db,'cancelled')
    with pytest.raises(AppError,match='订单已取消'):
        run(schedule.create_change(s,o,user=u,new_delivery_date=today_business()+timedelta(days=30),
            reason='不应放行',plan_offsets={key:1 for key,_,_ in contract_nodes()}))
    assert not run(s.execute(select(OrderScheduleChange))).scalars().all()


def contract_nodes():
    from app.modules.order.milestones import MILESTONE_NODES
    return MILESTONE_NODES


def test_order_cancel_keeps_voucher_and_closes_pending_change(db):
    s,u,*_=db;o=order(db)
    change=run(schedule.create_change(s,o,user=u,new_delivery_date=today_business()+timedelta(days=30),reason='正常变更',
        plan_offsets={key:1 for key,_,_ in contract_nodes()}))
    note='中'*255
    p=PaymentRecord(order_id=o.id,received_date=today_business(),received_amount=1,currency='CNY',status='pending',
        voucher_note=note,created_by=u.id,created_at=datetime.now(UTC))
    s.add(p);run(s.flush())
    run(orders.cancel_order(o.id,Request({'type':'http','method':'POST','path':'/','headers':[],
        'client':('127.0.0.1',1)}),u,s))
    assert o.status=='cancelled' and p.status=='rejected' and p.voucher_note==note
    assert change.status=='cancelled' and change.cancelled_by==u.id
    with pytest.raises(AppError,match='订单已取消'):
        run(schedule.confirm_change(s,o,change,user=u,remark='不应放行'))
    audits=run(s.execute(select(AuditLog).where(AuditLog.business_type=='payment',AuditLog.business_id==p.id))).scalars().all()
    assert audits and audits[0].after_data['comment']=='订单取消，随单驳回'
