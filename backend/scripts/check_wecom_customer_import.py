"""一次性PG库：验证企微批量建档和手工关联并发互斥，不连真实企微。"""
import asyncio
import uuid
from types import SimpleNamespace
from sqlalchemy import select, func, delete
from _test_support import require_isolated_db
require_isolated_db()
from app.core.database import SessionLocal, engine
from app.core.errors import AppError
from app.core.audit import AuditLog
from app.modules.customer.model import Customer, Contact
from app.modules.user.model import User
from app.modules.wecom.model import WeComExternalContact as E, WeComFollowRelationship as F, WeComSyncJob as J
from app.modules.wecom.customer_import import preview_import, execute_import
from app.modules.wecom.service import bind_customer


async def main():
    prefix='wecom_import_'+uuid.uuid4().hex[:10]
    ext_ids=[]; user_id=None;customer_ids=[];job_ids=[]
    try:
        async with SessionLocal() as s:
            u=User(name=prefix,username=prefix,password_hash='!',wecom_userid=prefix,status='active')
            s.add(u);await s.flush();user_id=u.id
            target=Customer(name=prefix+'有限公司',customer_type='企业',owner_id=u.id,level='A')
            s.add(target);await s.flush();customer_ids.append(target.id)
            for i in range(2):
                e=E(external_userid=prefix+str(i),name='联系人'+str(i),raw_data={'corp_full_name':target.name})
                s.add(e);await s.flush();ext_ids.append(e.id)
                s.add(F(external_contact_id=e.id,wecom_userid=prefix,status='active'))
            await s.commit()
        async with SessionLocal() as s:
            p=await preview_import(s)
            assert p['import_contacts']==2
        async def execute():
            async with SessionLocal() as s:
                try:
                    result=await execute_import(s,digest=p['digest'],operator_id=user_id)
                    await s.commit();job_ids.append(result['job_id']);return 200
                except AppError as exc:
                    await s.rollback();return exc.http_status
        assert sorted(await asyncio.gather(execute(),execute()))==[200,409]
        async with SessionLocal() as s:
            assert await s.scalar(select(func.count()).select_from(Contact).where(Contact.customer_id==customer_ids[0]))==2
            target=await s.get(Customer,customer_ids[0]);assert target.level=='A' and target.owner_id==user_id
            # 手工关联必须等待批量相同的企微行锁；拿到锁后重判，不能建第二条联系人。
            async with SessionLocal() as blocker:
                await blocker.execute(select(E).where(E.id==ext_ids[0]).with_for_update())
                async def manual():
                    async with SessionLocal() as other:
                        try:
                            await bind_customer(other,user=SimpleNamespace(id=user_id),contact_id=ext_ids[0],customer_id=customer_ids[0])
                            await other.commit();return 200
                        except AppError as exc:
                            await other.rollback();return exc.http_status
                task=asyncio.create_task(manual())
                await asyncio.sleep(.15)
                assert not task.done(),'manual bind bypassed source row lock'
                await blocker.commit()
                assert await task==422
            assert await s.scalar(select(func.count()).select_from(Contact).where(Contact.customer_id==customer_ids[0]))==2
        print('PASS: PG并发导入一成一冲突；手工关联等待源行锁并重判；已有客户归属和等级保留')
    finally:
        async with SessionLocal() as s:
            if ext_ids:
                await s.execute(delete(F).where(F.external_contact_id.in_(ext_ids)))
                await s.execute(delete(E).where(E.id.in_(ext_ids)))
            if customer_ids:
                await s.execute(delete(Contact).where(Contact.customer_id.in_(customer_ids)))
                await s.execute(delete(Customer).where(Customer.id.in_(customer_ids)))
            if job_ids:
                await s.execute(delete(AuditLog).where(AuditLog.action=='wecom_import_customers',AuditLog.business_id.in_(job_ids)))
                await s.execute(delete(J).where(J.id.in_(job_ids)))
            if user_id:await s.execute(delete(User).where(User.id==user_id))
            await s.commit()
        await engine.dispose()


if __name__=='__main__':asyncio.run(main())
