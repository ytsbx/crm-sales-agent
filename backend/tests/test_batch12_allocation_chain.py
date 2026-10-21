"""Real functions + real SQL: allocation authority, consumers, manual intent and accounting."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
import pytest
from sqlalchemy.orm import Session
from starlette.requests import Request
from conftest import make_engine, make_user, SyncSessionAsAsync
from app.core.audit import AuditLog
from app.core.errors import AppError
from app.core.timebase import today_business
from app.modules.customer.model import Customer
from app.modules.order.model import SalesOrder, OrderMilestone
from app.modules.order import milestones
from app.modules.payment.model import ReceivablePlan, PaymentRecord, PaymentAllocation
from app.modules.payment import service as svc, router
from app.modules.payment.schema import PaymentAllocationUpdate, ReceivableUpdate
from app.modules.quote.model import Quote, QuoteVersion
from app.modules.approval.model import ApprovalInstance
from app.modules.approval import rules_engine
from app.modules.agent import insights, tools
from app.modules.analytics import service as analytics
from app.modules.settings import service as settings_service
from app.modules.settings.model import SystemSetting
from app.modules.task.model import Task
from app.modules.followup.model import FollowUp
from app.modules.file.model import FileRecord

NOW=datetime.now(UTC)
REQ=Request({'type':'http','headers':[],'client':('127.0.0.1',1)})
USER=make_user(1,data_scope='all',permissions=('payment:view','payment:manage'))
class AsyncSession(SyncSessionAsAsync):
    async def delete(self,obj): self._session.delete(obj)

@pytest.fixture
def case():
    engine=make_engine()
    for model in (AuditLog,ApprovalInstance,SystemSetting,Task,FollowUp,FileRecord):
        model.__table__.create(engine,checkfirst=True)
    with engine.connect() as conn:
        conn.connection.create_function('timezone',2,lambda tz,val: val)
        conn.connection.create_function('to_char',2,lambda val,fmt: val[:7] if val else None)
    with Session(engine,expire_on_commit=False) as raw:
        raw.add(Customer(id=1,name='隔离客户',owner_id=1))
        raw.add(SalesOrder(id=1,customer_id=1,owner_id=1,order_no='B12',total_amount=D(200)))
        raw.add_all([ReceivablePlan(id=i,order_id=1,plan_name=f'第{i}期',due_date=today_business()-timedelta(days=2),amount=D(100),status='pending',created_at=NOW) for i in (1,2)])
        q=Quote(id=1,quote_no='B12Q',customer_id=1,owner_id=1)
        v=QuoteVersion(id=1,quote_id=1,version_no=1,created_at=NOW,total_amount=D(100),currency='CNY')
        raw.add_all([q,v]);raw.commit()
        yield raw,AsyncSession(raw),q,v
    engine.dispose()

def receipt(raw,amount='200',mode='legacy',status='confirmed',plan_id=1):
    row=PaymentRecord(order_id=1,receivable_plan_id=plan_id,received_date=today_business(),received_amount=D(amount),status=status,allocation_mode=mode,created_at=NOW)
    raw.add(row);raw.flush();return row
async def allocate(raw,s,row):
    await svc.allocate_payment(s,row,operator_id=1)
    for pid in (1,2): await svc.recalc_plan(s,raw.get(ReceivablePlan,pid))
    raw.commit()
async def change(s,payment,allocations):
    return await router.update_payment_allocations(payment.id,PaymentAllocationUpdate(allocations=allocations,reason='财务核实后调整'),REQ,USER,s)

@pytest.mark.anyio
async def test_complete_allocation_all_consumers(case):
    raw,s,q,v=case;p=receipt(raw);await allocate(raw,s,p)
    assert await svc.confirmed_amounts(s,[1,2])=={1:D(100),2:D(100)}
    context=await rules_engine.build_context(s,quote=q,version=v,items=[],fx=None)
    assert not context['customer_has_overdue']
    assert (await tools.get_receivables_summary(tools.ToolContext(s,USER,1),1))['overdue_count']==0
    assert (await insights.receivable_risk(s,USER,order_id=1))['summary']['overdue_count']==0
    assert (await svc.order_finance_summary(s,1))['overdue_count']==0
    assert (await analytics.receivable_stats(s,USER))['overdue_count']==0
    assert (await analytics.payment_stats(s,USER))['overdue_amount']==0
    dash=await analytics.dashboard_summary(s,USER)
    assert dash['pending_receivable_amount']==0 and dash['overdue_receivable_count']==0
    assert await settings_service.confirmed_not_paid_count(s)==0
    overdue=await router.list_receivables('overdue',1,1,20,USER,s)
    assert overdue['data']['total']==0

@pytest.mark.anyio
async def test_dashboard_order_total_keeps_separate_amounts(case):
    raw, s, _, _ = case
    order = raw.get(SalesOrder, 1)
    order.goods_amount = D(90)
    order.logistics_amount = D(10)
    order.other_charge_amount = D(0)
    order.discount_amount = D(0)
    order.total_amount = D(100)
    raw.commit()
    summary = await analytics.dashboard_summary(s, USER)
    assert summary['month_won_amount'] == 100
    assert summary['month_order_goods_amount'] == 90
    assert summary['month_order_logistics_amount'] == 10
    assert summary['month_order_other_charge_amount'] == 0
    assert summary['month_order_discount_amount'] == 0
    assert summary['month_order_unverified_count'] == 0
    order.goods_amount = D(91)
    raw.commit()
    summary = await analytics.dashboard_summary(s, USER)
    assert summary['month_won_amount'] == 100
    assert summary['month_order_unverified_count'] == 1
    order.goods_amount = D(90)
    order.logistics_amount = None
    raw.commit()
    summary = await analytics.dashboard_summary(s, USER)
    assert summary['month_won_amount'] == 100
    assert summary['month_order_goods_amount'] == 0
    assert summary['month_order_unverified_count'] == 1
    order.status = 'cancelled'
    raw.commit()
    summary = await analytics.dashboard_summary(s, USER)
    assert summary['month_won_amount'] == 0
    assert summary['month_order_unverified_count'] == 0

@pytest.mark.anyio
@pytest.mark.parametrize('amounts',[[],[{'plan_id':1,'amount':'50'}]])
async def test_manual_intent_survives_every_sync_and_rebalance(case,amounts):
    raw,s,q,v=case;p=receipt(raw);await allocate(raw,s,p)
    result=await change(s,p,amounts)
    expected=sum((D(x['amount']) for x in amounts),D(0))
    assert p.allocation_mode=='manual'
    for _ in range(2):
        await milestones.sync_payment_milestones(s,1)
        await svc.refresh_overdue_plans(s)
        await svc.rebalance_order_allocations(s,1,operator_id=1)
        await change(s,p,amounts)
    actual=await svc.serialize_allocations(s,p.id)
    assert sum((D(str(x['amount'])) for x in actual),D(0))==expected
    assert result['data']['allocations']==actual
    totals=await svc.order_finance_summary(s,1)
    assert totals['received_amount']==200 and totals['allocated_amount']==float(expected)
    assert totals['unallocated_amount']==float(D(200)-expected) and totals['unreceived_amount']==0
    assert totals['unsettled_plan_amount']==float(D(200)-expected)
    audit=raw.query(AuditLog).filter_by(action='update_allocations').order_by(AuditLog.id.desc()).first()
    assert audit.after_data['allocations']==actual
    await router.update_receivable(1,ReceivableUpdate(amount=D(150)),REQ,USER,s)
    assert await svc.serialize_allocations(s,p.id)==actual
    payment=raw.query(OrderMilestone).filter_by(node='payment').first()
    assert payment is None or payment.actual_date is None

@pytest.mark.anyio
async def test_moved_receipt_overdue_refresh_and_details(case):
    raw,s,q,v=case;p=receipt(raw);await allocate(raw,s,p)
    await change(s,p,[{'plan_id':2,'amount':'200'}])
    assert await svc.confirmed_amount(s,1)==0 and await svc.confirmed_amount(s,2)==200
    risk=await insights.receivable_risk(s,USER,order_id=1)
    assert [x['plan_id'] for x in risk['overdue']]==[1]
    raw.get(ReceivablePlan,1).status='pending';raw.flush()
    assert await svc.refresh_overdue_plans(s)==1
    assert raw.get(ReceivablePlan,1).status=='overdue'
    details=(await router.plan_payments(2,USER,s))['data']
    assert len(details)==1 and details[0]['received_amount']==200 and details[0]['plan_allocated_amount']==200
    assert (await rules_engine.build_context(s,quote=q,version=v,items=[],fx=None))['customer_has_overdue']

@pytest.mark.anyio
async def test_delete_allocated_target_refused_and_empty_plan_allowed(case):
    raw,s,q,v=case;p=receipt(raw);await allocate(raw,s,p)
    with pytest.raises(AppError) as err: await router.delete_receivable(2,REQ,USER,s)
    assert err.value.http_status==422
    await change(s,p,[{'plan_id':1,'amount':'200'}])
    assert (await router.delete_receivable(2,REQ,USER,s))['code']==0
    assert raw.get(ReceivablePlan,2) is None

@pytest.mark.anyio
@pytest.mark.parametrize('mode,expected',[('legacy',200),('auto',0),('manual',0)])
async def test_missing_rows_only_legacy_falls_back(case,mode,expected):
    raw,s,q,v=case;p=receipt(raw,mode=mode);raw.commit()
    assert await svc.confirmed_amount(s,1)==expected
    before=raw.query(PaymentAllocation).count()
    await milestones.sync_payment_milestones(s,1)
    assert raw.query(PaymentAllocation).count()==before
    detail=await svc.serialize_payment(s,p)
    assert detail['allocated_amount']==expected and detail['unallocated_amount']==200-expected

@pytest.mark.anyio
@pytest.mark.parametrize('status',['pending','rejected'])
async def test_unconfirmed_never_settles_plan(case,status):
    raw,s,q,v=case;p=receipt(raw,status=status)
    raw.add(PaymentAllocation(payment_id=p.id,plan_id=1,amount=D(200),source='manual',created_at=NOW,updated_at=NOW));raw.flush()
    assert await svc.confirmed_amount(s,1)==0
    assert await svc.allocate_payment(s,p,operator_id=1)==0
    assert not await svc.allocation_totals(s,[1])

@pytest.mark.anyio
async def test_cent_tail_and_repeat_allocation(case):
    raw,s,q,v=case
    raw.get(ReceivablePlan,1).amount=D('.01');raw.get(ReceivablePlan,2).amount=D('.02')
    p=receipt(raw,amount='.03');await allocate(raw,s,p)
    for _ in range(3): await svc.allocate_payment(s,p,operator_id=1)
    assert await svc.confirmed_amounts(s,[1,2])=={1:D('.01'),2:D('.02')}
    assert raw.query(PaymentAllocation).count()==2
    assert (await svc.order_finance_summary(s,1))['unallocated_amount']==0

@pytest.mark.anyio
async def test_scope_and_bad_targets_leave_financial_data_unchanged(case):
    raw,s,q,v=case;p=receipt(raw);await allocate(raw,s,p)
    restricted=make_user(2,data_scope='self',permissions=('payment:manage',))
    with pytest.raises(AppError): await router.update_payment_allocations(p.id,PaymentAllocationUpdate(allocations=[],reason='越权'),REQ,restricted,s)
    for target in (999,):
        with pytest.raises(AppError): await change(s,p,[{'plan_id':target,'amount':'100'}])
    with pytest.raises(AppError): await change(s,p,[{'plan_id':1,'amount':'201'}])
    assert p.allocation_mode=='auto' and await svc.confirmed_amount(s,1)==100

@pytest.mark.anyio
async def test_cross_day_and_cancelled_do_not_settle_or_collect(case,monkeypatch):
    raw,s,q,v=case;p=receipt(raw,amount='50');await allocate(raw,s,p)
    today=today_business();plan=raw.get(ReceivablePlan,1);plan.due_date=today;plan.status='partial';raw.flush()
    assert not svc.is_plan_overdue(status=plan.status,balance=D(50),due_date=today,today=today)
    monkeypatch.setattr(svc,'today_business',lambda:today+timedelta(days=1))
    assert await svc.refresh_overdue_plans(s)>=1 and plan.status=='overdue'
    plan.status='cancelled';raw.flush()
    assert not svc.is_plan_overdue(status='cancelled',balance=D(50),due_date=today,today=today+timedelta(days=1))

@pytest.mark.anyio
async def test_maximum_amount_round_trip_is_exact(case):
    raw,s,q,v=case
    maximum=D('99999999999999.99')
    p=receipt(raw,amount=str(maximum),mode='manual')
    # SQLite stores Numeric as binary float; test edits/JSON/SQL Decimal at a representable sum instead.
    # Real Numeric(16,2) persistence and full large-value roundtrip are guarded by the PostgreSQL suite.
    assert PaymentAllocationUpdate(allocations=[{'plan_id':1,'amount':str(maximum)}],reason='金额边界').allocations[0].amount==maximum
    amount=D('100.01');p.received_amount=amount;raw.flush()
    await change(s,p,[{'plan_id':1,'amount':'100.01'}])
    item=await svc.serialize_payment(s,p)
    assert item['received_amount_exact']=='100.01'
    assert item['allocations'][0]['amount_exact']=='100.01'
    assert item['allocated_amount_exact']=='100.01' and item['unallocated_amount_exact']=='0.00'
