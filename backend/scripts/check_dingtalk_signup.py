"""首次扫码建号的 PostgreSQL 并发与事务验证，禁止业务库运行。"""
import asyncio
from uuid import uuid4
from unittest.mock import patch
from sqlalchemy import select, text, delete
from _test_support import require_isolated_db

require_isolated_db()


class FakeLogin:
    def __init__(self, userid): self.userid=userid
    async def exchange_user_token(self, code): return 'fake-token'
    async def get_login_userinfo(self, token): return {'unionId':'u-'+self.userid}
    async def userid_by_unionid(self, unionid): return self.userid
    async def get_login_contact_user(self, userid):
        return {'userid':userid,'unionid':'u-'+userid,'name':userid}


async def main():
    from app.core.database import SessionLocal
    from app.core.audit import AuditLog
    from app.modules.user.model import User, Role, user_roles
    from app.modules.dingtalk import service as dt
    prefix='__dt_signup_'+uuid4().hex[:12]
    try:
        with patch.object(dt,'get_client',lambda:FakeLogin(prefix)):
            async with SessionLocal() as first, SessionLocal() as second:
                one,reason=await dt.resolve_login_user(first,code='one')
                assert reason=='ok'
                pid=(await second.execute(text('SELECT pg_backend_pid()'))).scalar_one()
                waiting=asyncio.create_task(dt.resolve_login_user(second,code='two'))
                try:
                    for _ in range(100):
                        async with SessionLocal() as observer:
                            blocked=(await observer.execute(text("SELECT wait_event='advisory' FROM pg_stat_activity WHERE pid=:pid"),{'pid':pid})).scalar()
                        if blocked: break
                        assert not waiting.done(), '第二次扫码没有等待建号事务'
                        await asyncio.sleep(.02)
                    else: raise AssertionError('未观察到真实 advisory 锁等待')
                    await first.commit()
                    two,reason=await asyncio.wait_for(waiting,5)
                    assert reason=='ok' and one.id==two.id
                    await second.commit()
                finally:
                    if not waiting.done():
                        waiting.cancel()
                        try: await waiting
                        except asyncio.CancelledError: pass
            async with SessionLocal() as s:
                users=(await s.execute(select(User).where(User.dingtalk_userid==prefix))).scalars().all()
                assert len(users)==1
                roles=(await s.execute(select(Role.code,Role.data_scope).join(user_roles,user_roles.c.role_id==Role.id).where(user_roles.c.user_id==users[0].id))).all()
                assert roles==[('salesperson','self')]
            print('PASS 两次扫码真实交错等待，最终只有一个业务员账号且数据范围为本人')
        rollback_uid=prefix+'_rollback'
        with patch.object(dt,'get_client',lambda:FakeLogin(rollback_uid)):
            async with SessionLocal() as s:
                user,reason=await dt.resolve_login_user(s,code='rollback')
                assert reason=='ok'
                new_id=user.id
                await s.rollback()
            async with SessionLocal() as s:
                assert await s.get(User,new_id) is None
                assert (await s.execute(select(user_roles).where(user_roles.c.user_id==new_id))).first() is None
                assert (await s.execute(select(AuditLog.id).where(AuditLog.business_type=='user',AuditLog.business_id==new_id))).first() is None
            print('PASS 外层登录失败时账号、角色和建号审计一起回滚')
    finally:
        async with SessionLocal() as s:
            ids=list((await s.execute(select(User.id).where(User.dingtalk_userid.like(prefix+'%')))).scalars())
            if ids:
                await s.execute(delete(AuditLog).where(AuditLog.business_type=='user',AuditLog.business_id.in_(ids)))
                await s.execute(user_roles.delete().where(user_roles.c.user_id.in_(ids)))
                await s.execute(delete(User).where(User.id.in_(ids)))
            await s.commit()


if __name__=='__main__': asyncio.run(main())
