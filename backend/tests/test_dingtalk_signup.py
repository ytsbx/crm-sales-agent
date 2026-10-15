"""企业员工扫码首次建号；所有身份来自假钉钉、所有数据在内存库。"""
import asyncio
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from conftest import SyncSessionAsAsync, make_engine
from app.core.base import Base
from app.core.audit import AuditLog
from app.core.security import verify_password
from app.modules.user.model import User, Role, user_roles
from app.modules.dingtalk import service as dt


def run(coro): return asyncio.run(coro)


class FakeLogin:
    def __init__(self, *, userid='staff-1', unionid='union-1', profile=None):
        self.userid=userid; self.unionid=unionid; self.profile=profile
        self.profile_calls=0
    async def exchange_user_token(self, code): return 'fake-token'
    async def get_login_userinfo(self, token): return {'unionId':self.unionid,'nick':'不可信昵称'}
    async def userid_by_unionid(self, uid): return self.userid
    async def get_login_contact_user(self, uid):
        self.profile_calls+=1
        return self.profile if self.profile is not None else {'userid':uid,'unionid':self.unionid,'name':'新员工','mobile':'13800000001'}


@pytest.fixture
def db(monkeypatch):
    engine=make_engine()
    Base.metadata.create_all(engine,tables=[Role.__table__,user_roles,AuditLog.__table__])
    with Session(engine) as raw:
        s=SyncSessionAsAsync(raw)
        role=Role(code='salesperson',name='业务员',data_scope='self',status='active')
        s.add(role);run(s.flush())
        fake=FakeLogin()
        monkeypatch.setattr(dt,'get_client',lambda:fake)
        yield s,role,fake
    engine.dispose()


def login(db): return run(dt.resolve_login_user(db[0],code='opaque-code'))


def test_new_staff_gets_only_salesperson(db):
    s,role,fake=db
    user,reason=login(db)
    assert reason=='ok' and user.name=='新员工' and user.status=='active'
    assert user.dingtalk_userid=='staff-1' and user.department_id is None
    assert user.username.startswith('dt_') and len(user.username)<=64
    assert not verify_password('123456',user.password_hash)
    assert run(s.execute(select(user_roles.c.role_id).where(user_roles.c.user_id==user.id))).scalars().all()==[role.id]
    assert run(s.execute(select(AuditLog))).scalar_one().after_data['data_scope']=='self'
    assert fake.profile_calls==1


def test_repeat_login_preserves_admin_assigned_roles(db):
    s,role,fake=db
    user,_=login(db)
    admin=Role(code='admin',name='管理员',data_scope='all',status='active')
    s.add(admin);run(s.flush())
    run(s.execute(user_roles.delete().where(user_roles.c.user_id==user.id)))
    run(s.execute(user_roles.insert().values(user_id=user.id,role_id=admin.id)))
    run(s.flush())
    again,reason=login(db)
    assert reason=='ok' and again.id==user.id and fake.profile_calls==1
    assert run(s.execute(select(user_roles.c.role_id).where(user_roles.c.user_id==user.id))).scalars().all()==[admin.id]
    assert len(run(s.execute(select(User))).scalars().all())==1


@pytest.mark.parametrize('status',['active','disabled'])
@pytest.mark.parametrize('match',['name','mobile'])
def test_existing_unbound_account_requires_admin(db,status,match):
    s,_,_=db
    old=User(name='新员工' if match=='name' else '旧姓名',username='existing',password_hash='x',status=status,
             mobile='13800000001' if match=='mobile' else None)
    s.add(old);run(s.flush())
    user,reason=login(db)
    assert user is None and reason=='not_bound' and old.dingtalk_userid is None
    assert len(run(s.execute(select(User))).scalars().all())==1
    assert not run(s.execute(select(AuditLog))).scalars().all()


def test_bound_disabled_cannot_create_replacement(db):
    s,_,fake=db
    s.add(User(name='已停用',username='disabled',password_hash='x',status='disabled',dingtalk_userid='staff-1'))
    run(s.flush())
    user,reason=login(db)
    assert user is None and reason=='disabled' and fake.profile_calls==0
    assert len(run(s.execute(select(User))).scalars().all())==1


@pytest.mark.parametrize('change',['missing','disabled','all'])
def test_invalid_default_role_refuses_signup(db,change):
    s,role,_=db
    if change=='missing': run(s.execute(Role.__table__.delete()))
    elif change=='disabled': role.status='disabled'
    else: role.data_scope='all'
    run(s.flush())
    user,reason=login(db)
    assert user is None and reason=='role_unavailable'
    assert not run(s.execute(select(User))).scalars().all()


@pytest.mark.parametrize('profile',[
    {'userid':'somebody-else','name':'新人'},
    {'userid':'staff-1','unionid':'someone-else','name':'新人'},
    {'userid':'staff-1','name':''},
    {'userid':'staff-1','name':'人'*65},
])
def test_invalid_corporate_profile_denied(db,profile):
    s,_,fake=db;fake.profile=profile
    user,reason=login(db)
    assert user is None and reason=='invalid_profile'
    assert not run(s.execute(select(User))).scalars().all()


def test_outside_enterprise_denied(db):
    s,_,fake=db;fake.userid=None
    user,reason=login(db)
    assert user is None and reason=='not_in_contact' and fake.profile_calls==0
    assert not run(s.execute(select(User))).scalars().all()


def test_outer_failure_rolls_back_user_role_and_audit(db):
    s,_,_=db
    run(s.commit())
    # SQLite 默认的 SELECT 不开启底层事务，显式 BEGIN 模拟真实请求外层事务。
    s._session.connection().exec_driver_sql("BEGIN")
    user,reason=login(db)
    assert reason=='ok'
    run(s.rollback())
    assert not run(s.execute(select(User))).scalars().all()
    assert not run(s.execute(select(user_roles))).all()
    assert not run(s.execute(select(AuditLog))).scalars().all()


def test_contact_lookup_login_gate_blocks_network(monkeypatch):
    from app.core.config import settings
    from app.modules.dingtalk.client import DingTalkClient, DingTalkDisabled
    monkeypatch.setattr(settings,'dingtalk_login_off',True)
    client=DingTalkClient()
    with pytest.raises(DingTalkDisabled):
        run(client.get_login_contact_user('employee'))


@pytest.mark.parametrize('mode',['ok','bad_json','bad_shape','network'])
def test_contact_lookup_handles_upstream_response(monkeypatch,mode):
    import httpx
    from app.core.config import settings
    from app.modules.dingtalk import client as module
    monkeypatch.setattr(settings,'dingtalk_login_off',False)
    monkeypatch.setattr(settings,'dingtalk_app_key','fake-key')
    monkeypatch.setattr(settings,'dingtalk_app_secret','fake-secret')
    client=module.DingTalkClient()
    async def token(*,for_login=False):
        assert for_login
        return 'fake-token'
    client.access_token=token
    class HTTP:
        def __init__(self,**kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def post(self,url,**kwargs):
            assert kwargs['json']=={'userid':'staff-1'}
            if mode=='network': raise httpx.ConnectError('test')
            if mode=='bad_json': return httpx.Response(200,content=b'{')
            if mode=='bad_shape': return httpx.Response(200,json=[])
            return httpx.Response(200,json={'errcode':0,'result':{'userid':'staff-1','name':'员工'}})
    monkeypatch.setattr(module.httpx,'AsyncClient',HTTP)
    if mode=='ok': assert run(client.get_login_contact_user('staff-1'))['name']=='员工'
    else:
        with pytest.raises(module.DingTalkError): run(client.get_login_contact_user('staff-1'))
