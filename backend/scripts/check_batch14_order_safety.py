"""第十四批：必须随机隔离 PostgreSQL，真行锁与 Numeric(16,3) 流程。"""
import asyncio
import uuid
from datetime import datetime, UTC
from decimal import Decimal

from sqlalchemy import select, text
from starlette.requests import Request

from _test_support import require_isolated_db
require_isolated_db()

from app.core.database import SessionLocal
from app.core.deps import CurrentUser
from app.core.errors import AppError
from app.core.timebase import today_business
from app.modules.customer.model import Customer
from app.modules.user.model import User
from app.modules.order import service, router
from app.modules.order.model import SalesOrder, SalesOrderItem, OrderShipmentBatch, OrderShipmentBatchItem
from app.modules.order.schema import OrderUpdate, ShipmentBatchCreate, ShipmentBatchShip


async def main():
    tag='CHK14-'+uuid.uuid4().hex[:8]
    async with SessionLocal() as s:
        users=(await s.execute(select(User).where(User.status=='active').order_by(User.id).limit(2))).scalars().all()
        assert len(users)==2
        old,new=users
        old_user=CurrentUser(old,{'order:manage'},['sales'],'self')
        new_user=CurrentUser(new,{'order:manage'},['sales'],'self')
        customer=(await s.execute(select(Customer).where(Customer.deleted_at.is_(None)).limit(1))).scalars().first()
        assert customer is not None
        order=SalesOrder(order_no=tag,customer_id=customer.id,owner_id=old.id,
                         sales_owner_id=old.id,status='pending',currency='CNY',total_amount=1)
        s.add(order);await s.commit();oid=order.id

    # 未提交的改派持有订单锁。写入口拿锁后必须重查可见性，不允许沿用旧归属。
    async with SessionLocal() as blocker:
        locked=(await blocker.execute(select(SalesOrder).where(SalesOrder.id==oid).with_for_update())).scalar_one()
        locked.owner_id=new_user.id;await blocker.flush()
        async def stale_writer():
            async with SessionLocal() as writer:
                await writer.execute(text("SET LOCAL application_name='batch14-order-writer'"))
                try:
                    result=await router.update_order(oid,OrderUpdate(remark='未经授权的旧负责人写入'),
                        Request({'type':'http','method':'PATCH','path':'/','headers':[],'client':('127.0.0.1',1)}),
                        old_user,writer)
                    return result
                except AppError as exc:
                    await writer.rollback();return exc
        task=asyncio.create_task(stale_writer())
        try:
            async with SessionLocal() as observer:
                for _ in range(100):
                    waiting=(await observer.execute(text("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE application_name='batch14-order-writer' AND wait_event_type='Lock')"))).scalar_one()
                    if waiting:break
                    assert not task.done(), '写请求未在订单锁处等待'
                    await asyncio.sleep(.03)
                else:raise AssertionError('未观察到 PostgreSQL 锁等待')
            await blocker.commit()
            result=await asyncio.wait_for(task,10)
            assert isinstance(result,AppError) and result.http_status==403, repr(result)
        finally:
            if not task.done():task.cancel();await asyncio.gather(task,return_exceptions=True)
    async with SessionLocal() as s:
        order=await s.get(SalesOrder,oid)
        assert order.owner_id==new_user.id and order.remark is None
        await router.update_order(oid,OrderUpdate(remark='新负责人正常保存'),
            Request({'type':'http','method':'PATCH','path':'/','headers':[],'client':('127.0.0.1',1)}),new_user,s)
        assert order.remark=='新负责人正常保存'
    print('PASS ownership lock wait -> old owner 403, new owner succeeds',flush=True)

    async with SessionLocal() as s:
        order=await s.get(SalesOrder,oid)
        order.status='shipped'
        order.total_amount=Decimal('10000000000000.00')
        item=SalesOrderItem(order_id=oid,sku_id=None,sku_snapshot='定制数量精度回归',
            quantity=Decimal('9999999999999.999'),unit_price=1,amount=Decimal('10000000000000.00'))
        s.add(item);await s.flush()
        batch=OrderShipmentBatch(order_id=oid,batch_no=1,status='shipped',
            actual_ship_date=today_business(),created_at=datetime.now(UTC),created_by=new_user.id)
        s.add(batch);await s.flush()
        s.add(OrderShipmentBatchItem(batch_id=batch.id,order_item_id=item.id,
            planned_qty=Decimal('9999999999999.998'),shipped_qty=Decimal('9999999999999.998')))
        await s.commit()
        view=await service.order_shipments(s,order)
        assert view['items'][0]['remaining_exact']=='0.001',view['items']
        assert view['items'][0]['unplanned_exact']=='0.001'
        assert not view['summary']['all_shipped']
        try:await service.change_status(s,order,new_status='completed',operator_id=new_user.id)
        except AppError:pass
        else:raise AssertionError('0.001 未发货却能标记完成')
        tail=await service.create_shipment_batch(s,order,
            payload=ShipmentBatchCreate(items=[{'order_item_id':item.id,'planned_qty':'0.001'}]),user_id=new_user.id)
        await service.ship_shipment_batch(s,order,tail,payload=ShipmentBatchShip(),operator_id=new_user.id)
        view=await service.order_shipments(s,order)
        assert view['summary']['all_shipped'] and Decimal(view['summary']['remaining_exact'])==0
        await service.change_status(s,order,new_status='completed',operator_id=new_user.id)
        assert order.status=='completed'
        await s.commit()
    print('PASS exact 0.001 remainder -> plan -> ship -> complete, no premature completion',flush=True)


asyncio.run(main())
