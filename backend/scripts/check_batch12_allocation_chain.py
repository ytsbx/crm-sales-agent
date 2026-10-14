"""第十二批：隔离 PostgreSQL 完整回款消费链与真实并发守门。"""
import sys
sys.path[:0]=['.','scripts']
from _test_support import require_isolated_db, ISOLATED_DB_PREFIXES, ISOLATED_DB_SUFFIXES
name=require_isolated_db()
if not (name.startswith(ISOLATED_DB_PREFIXES) or name.endswith(ISOLATED_DB_SUFFIXES)):
    raise SystemExit('只允许随机隔离测试库')
import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from unittest.mock import AsyncMock
from uuid import uuid4
from sqlalchemy import select,text,delete
from starlette.requests import Request
from app.core.database import SessionLocal,engine
from app.core.audit import AuditLog
from app.core.deps import CurrentUser
from app.core.errors import AppError
from app.core.timebase import today_business
from app.modules.user.model import User
from app.modules.customer.model import Customer
from app.modules.order.model import SalesOrder,OrderMilestone
from app.modules.payment.model import ReceivablePlan,PaymentRecord
from app.modules.payment import router,service as svc
from app.modules.payment.schema import PaymentAction,PaymentAllocationUpdate,ReceivableUpdate
from app.modules.order import milestones
from app.modules.analytics import service as analytics
from app.modules.agent import insights,tools
from app.modules.followup import service as followups
from app.modules.notification import service as notifications
REQ=Request({'type':'http','headers':[],'client':('127.0.0.1',1)})
async def wait_blocked(pid,worker):
    for _ in range(100):
        async with SessionLocal() as s:
            locked=await s.scalar(text("SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=:pid"),{'pid':pid})
        if locked: return
        assert not worker.done(),'worker did not wait for the order lock'
        await asyncio.sleep(.02)
    raise AssertionError('no observed PostgreSQL lock wait')
async def main():
    old_followup,old_dispatch,old_notify=followups.record_and_notify,notifications.dispatch_pending,notifications.notify
    followups.record_and_notify=AsyncMock();notifications.dispatch_pending=AsyncMock();notifications.notify=AsyncMock()
    order_id=customer_id=None
    try:
        async with SessionLocal() as s:
            actor=CurrentUser(await s.get(User,1),{'payment:manage','payment:view'},['admin'],'all')
            c=Customer(name='B12-'+uuid4().hex[:10],owner_id=1);s.add(c);await s.flush();customer_id=c.id
            o=SalesOrder(customer_id=c.id,owner_id=1,order_no='B12-'+uuid4().hex[:12],total_amount=D(200));s.add(o);await s.flush();order_id=o.id
            plans=[ReceivablePlan(order_id=o.id,plan_name=f'第{i}期',amount=D(100),due_date=today_business()-timedelta(days=1),created_at=datetime.now(UTC)) for i in (1,2)]
            s.add_all(plans);await s.flush();pids=[p.id for p in plans]
            records=[PaymentRecord(order_id=o.id,receivable_plan_id=plans[0].id,received_amount=D(100),received_date=today_business(),status='pending',created_at=datetime.now(UTC)) for _ in range(2)]
            s.add_all(records);await s.commit();rids=[r.id for r in records]
        async def confirm(rid):
            async with SessionLocal() as s:
                return await router.confirm_payment(rid,PaymentAction(),REQ,actor,s)
        await asyncio.wait_for(asyncio.gather(*(confirm(rid) for rid in rids)),15)
        async with SessionLocal() as s:
            assert await svc.confirmed_amounts(s,pids)=={pids[0]:D(100),pids[1]:D(100)}
            assert (await analytics.dashboard_summary(s,actor))['overdue_receivable_count']==0
            assert (await tools.get_receivables_summary(tools.ToolContext(s,actor,1),order_id))['overdue_count']==0
            assert (await insights.receivable_risk(s,actor,order_id=order_id))['summary']['open_amount']==0
            assert (await analytics.payment_stats(s,actor))['overdue_amount']==0
            assert len((await router.plan_payments(pids[1],actor,s))['data'])==1
        print('PASS 并发确认、工作台/AI/风险/账龄/按期明细一致',flush=True)
        async def adjust(rid,rows):
            async with SessionLocal() as s:
                return await router.update_payment_allocations(rid,PaymentAllocationUpdate(allocations=rows,reason='真实并发验证'),REQ,actor,s)
        await asyncio.wait_for(asyncio.gather(adjust(rids[0],[]),adjust(rids[1],[{'plan_id':pids[1],'amount':'50'}])),15)
        async with SessionLocal() as s:
            await milestones.sync_payment_milestones(s,order_id)
            await svc.refresh_overdue_plans(s)
            await svc.rebalance_order_allocations(s,order_id,operator_id=1)
            await s.commit()
        async with SessionLocal() as s:
            summary=await svc.order_finance_summary(s,order_id)
            assert summary['received_amount']==200 and summary['allocated_amount']==50 and summary['unallocated_amount']==150
            assert await svc.confirmed_amount(s,pids[0])==0
            assert (await s.get(PaymentRecord,rids[0])).allocation_mode=='manual'
            audit=(await s.execute(select(AuditLog).where(AuditLog.business_type=='payment',AuditLog.business_id==rids[0],AuditLog.action=='update_allocations').order_by(AuditLog.id.desc()))).scalars().first()
            assert audit.after_data['allocations']==[]
            assert await svc.serialize_allocations(s,rids[0])==[]
        print('PASS 并发人工清空/部分分配，跨事务重复同步不回填，审计一致',flush=True)
        # refresh blocks on order, then sees latest manual intention (not its stale scan snapshot).
        ready=asyncio.Event();pid_box=[]
        async def refresh():
            async with SessionLocal() as s:
                pid_box.append(await s.scalar(text('select pg_backend_pid()')));ready.set()
                await svc.refresh_overdue_plans(s);await s.commit()
        async with SessionLocal() as s:
            await svc.lock_order(s,order_id)
            worker=asyncio.create_task(refresh());await ready.wait();await wait_blocked(pid_box[0],worker)
            await router.update_payment_allocations(rids[1],PaymentAllocationUpdate(allocations=[],reason='持锁清空'),REQ,actor,s)
            await asyncio.wait_for(worker,15)
        async with SessionLocal() as s:
            assert await svc.confirmed_amount(s,pids[1])==0
            assert (await svc.order_finance_summary(s,order_id))['unallocated_amount']==200
            await router.update_payment_allocations(rids[1],PaymentAllocationUpdate(allocations=[{'plan_id':pids[1],'amount':'100'}],reason='主动重新分配'),REQ,actor,s)
            try:
                await router.delete_receivable(pids[1],REQ,actor,s)
                raise AssertionError('delete allocated target succeeded')
            except AppError as e:
                assert e.http_status==422;await s.rollback()
            await router.update_receivable(pids[0],ReceivableUpdate(amount=D(150)),REQ,actor,s)
            assert await svc.confirmed_amount(s,pids[1])==100
            assert await svc.confirmed_amount(s,pids[0])==0
        print('PASS 每日刷新与人工调整真实锁交错，删除守卫、应收修改尊重人工决定',flush=True)
        async with SessionLocal() as s:
            record = await svc.get_visible_payment(s,actor,rids[0],for_update=True)
            record.received_amount=D('99999999999999.99')
            await s.commit()
        async with SessionLocal() as s:
            result=await router.update_payment_allocations(rids[0],PaymentAllocationUpdate(
                allocations=[{'plan_id':pids[0],'amount':'99999999999999.99'}],reason='合法金额上限精确往返'),REQ,actor,s)
            row=await s.get(PaymentRecord,rids[0]);view=await svc.serialize_payment(s,row)
            assert view['received_amount_exact']=='99999999999999.99'
            assert view['allocated_amount_exact']=='99999999999999.99'
            assert result['data']['allocations'][0]['amount_exact']=='99999999999999.99'
        print('PASS PostgreSQL Numeric(16,2) 合法上限精确往返不丢分',flush=True)
        print('PASS 第十二批完整回款链路',flush=True)
    finally:
        followups.record_and_notify=old_followup;notifications.dispatch_pending=old_dispatch;notifications.notify=old_notify
        if order_id:
            async with SessionLocal() as s:
                await s.execute(delete(AuditLog).where(AuditLog.business_id.in_(rids),AuditLog.business_type=='payment'))
                await s.execute(delete(AuditLog).where(AuditLog.business_id.in_(pids),AuditLog.business_type=='receivable_plan'))
                await s.execute(delete(OrderMilestone).where(OrderMilestone.order_id==order_id))
                await s.execute(delete(PaymentRecord).where(PaymentRecord.order_id==order_id))
                await s.execute(delete(ReceivablePlan).where(ReceivablePlan.order_id==order_id))
                await s.execute(delete(SalesOrder).where(SalesOrder.id==order_id))
                await s.execute(delete(Customer).where(Customer.id==customer_id));await s.commit()
        await engine.dispose()
if __name__=='__main__':asyncio.run(main())
