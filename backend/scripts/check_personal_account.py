"""个人账号真实 HTTP 与 PostgreSQL 回归，仅隔离库。"""
import asyncio
import os
from uuid import uuid4
from unittest.mock import patch
import httpx
from sqlalchemy import select, delete
from _test_support import require_isolated_db
require_isolated_db()


async def main():
    from app.core.database import SessionLocal
    from app.core.audit import AuditLog
    from app.modules.auth.model import LoginSession
    from app.modules.user.model import User, user_roles
    from app.modules.dingtalk import service as dt
    from app.modules.user.usernames import pinyin_username
    prefix='__account_'+uuid4().hex[:10]
    name='张三'+uuid4().hex[:8]
    ids=[]
    class Fake:
        def __init__(self,uid): self.uid=uid
        async def exchange_user_token(self,code): return 'fake'
        async def get_login_userinfo(self,token): return {'unionId':'union-'+self.uid}
        async def userid_by_unionid(self,unionid): return self.uid
        async def get_login_contact_user(self,uid): return {'userid':uid,'name':name}
    try:
        for i in range(2):
            with patch.object(dt,'get_client',lambda i=i:Fake(prefix+str(i))):
                async with SessionLocal() as s:
                    user,reason=await dt.resolve_login_user(s,code='fake')
                    assert reason=='ok'
                    ids.append(user.id)
                    assert user.username==pinyin_username(name)+('2' if i else '')
                    await s.commit()
        async with httpx.AsyncClient(base_url=os.environ['API_BASE']+'/',timeout=20) as api:
            async def login(username,password):
                res=await api.post('auth/login',json={'username':username,'password':password})
                assert res.status_code==200, res.text
                return {'Authorization':'Bearer '+res.json()['data']['access_token']}
            first_name=pinyin_username(name)
            h1=await login(first_name,'123456')
            h2=await login(first_name+'2','123456')
            res=await api.get('auth/me',headers=h1)
            assert res.json()['data']['initial_password_pending'] is True
            assert res.json()['data']['roles']==['salesperson']
            res=await api.patch('auth/account',headers=h1,json={'username':first_name,'current_password':'123456','roles':['admin']})
            assert res.status_code==400
            changed='myaccount_'+uuid4().hex[:10]
            res=await api.patch('auth/account',headers=h1,json={'username':changed,'current_password':'123456','new_password':'changed-password9'})
            assert res.status_code==200, res.text
            assert (await api.get('auth/me',headers=h1)).status_code==401
            assert (await api.post('auth/login',json={'username':changed,'password':'123456'})).status_code==400
            h1=await login(changed,'changed-password9')
            assert (await api.get('auth/me',headers=h1)).json()['data']['initial_password_pending'] is False
            assert (await api.get('auth/me',headers=h2)).json()['data']['initial_password_pending'] is True
            print('PASS 全拼重名编号/123456登录/本人修改/旧会话失效/旧密码拒绝/他人不变')
            wanted='shared_'+uuid4().hex[:10]
            answers=await asyncio.gather(
                api.patch('auth/account',headers=h1,json={'username':wanted,'current_password':'changed-password9'}),
                api.patch('auth/account',headers=h2,json={'username':wanted,'current_password':'123456'}))
            assert sorted(answer.status_code for answer in answers)==[200,409], [(a.status_code,a.text) for a in answers]
            async with SessionLocal() as s:
                assert len((await s.execute(select(User.id).where(User.username==wanted))).scalars().all())==1
                audits=(await s.execute(select(AuditLog).where(AuditLog.operator_id.in_(ids)))).scalars().all()
                assert 'changed-password9' not in str([(a.before_data,a.after_data) for a in audits])
            print('PASS 两个用户并发抢同一登录名：一个成功、一个409，无500')
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(LoginSession).where(LoginSession.user_id.in_(ids)))
            await s.execute(delete(AuditLog).where(AuditLog.operator_id.in_(ids)))
            await s.execute(user_roles.delete().where(user_roles.c.user_id.in_(ids)))
            await s.execute(delete(User).where(User.id.in_(ids)))
            await s.commit()


if __name__=='__main__': asyncio.run(main())
