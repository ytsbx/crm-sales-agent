import asyncio
import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.requests import Request
from conftest import make_engine, SyncSessionAsAsync
from app.core.base import Base
from app.core.audit import AuditLog
from app.core.deps import CurrentUser
from app.core.security import hash_password, verify_password
from app.core.errors import AppError
from app.core import rate_limit
from app.modules.auth.model import LoginSession
from app.modules.auth.session import open_session, load_active_session
from app.modules.auth.schema import PersonalAccountUpdate
from app.modules.auth.router import update_personal_account
from app.modules.user.model import User
from app.modules.user.usernames import pinyin_username, allocate_username


def run(coro): return asyncio.run(coro)


@pytest.fixture
def db():
    engine=make_engine()
    Base.metadata.create_all(engine,tables=[AuditLog.__table__,LoginSession.__table__])
    rate_limit.reset('account-change:1|unknown')
    with Session(engine) as raw:
        s=SyncSessionAsAsync(raw)
        actor=User(id=1,username='zhangsan',name='张三',password_hash=hash_password('123456'),status='active',dingtalk_userid='dt-one',initial_password_pending=True)
        other=User(id=2,username='lisi',name='李四',password_hash=hash_password('otherpass'),status='active')
        s.add(actor);s.add(other);run(s.flush())
        own=run(open_session(s,1));second=run(open_session(s,1));other_session=run(open_session(s,2))
        run(s.flush())
        yield s,actor,other,CurrentUser(actor,set(),['salesperson'],'self',sid=own.sid),[own,second,other_session]
    engine.dispose()
    rate_limit.reset('account-change:1|unknown')


def save(db,**kwargs):
    s,_,_,user,_=db
    payload={'username':'zhangsan','current_password':'123456',**kwargs}
    return run(update_personal_account(PersonalAccountUpdate(**payload),Request({'type':'http','headers':[]}),user,s))


@pytest.mark.parametrize('name,expected',[('张三','zhangsan'),('李四','lisi'),('欧阳娜娜','ouyangnana'),('张 三','zhangsan'),('John Smith','johnsmith')])
def test_pinyin(name,expected): assert pinyin_username(name)==expected


def test_suffix_collision_is_case_insensitive(db):
    s,*_=db
    s.add(User(username='zhangsan2',name='别的用户',password_hash='x',status='active'))
    s.add(User(username='Zhangsan3',name='另一个用户',password_hash='x',status='active'))
    run(s.flush())
    assert run(allocate_username(s,'张三'))=='zhangsan4'


def test_change_own_credentials_without_admin_permission(db):
    s,actor,other,_,sessions=db
    save(db,username='zhangsan_new',new_password='new-password')
    assert actor.username=='zhangsan_new' and verify_password('new-password',actor.password_hash)
    assert not actor.initial_password_pending and actor.dingtalk_userid=='dt-one'
    assert other.username=='lisi' and verify_password('otherpass',other.password_hash)
    for item in sessions[:2]:
        s._session.refresh(item)
        assert item.status=='revoked'
        with pytest.raises(AppError): run(load_active_session(s,item.sid,1))
    assert run(load_active_session(s,sessions[2].sid,2)).status=='active'
    audit=run(s.execute(select(AuditLog))).scalar_one()
    assert audit.business_id==1 and audit.after_data=={'username':'zhangsan_new','password_changed':True}
    assert 'new-password' not in str(audit.after_data) and '123456' not in str(audit.before_data)


def test_username_only_keeps_initial_password_notice(db):
    _,actor,*_=db
    save(db,username='zhangsan2')
    assert actor.username=='zhangsan2' and actor.initial_password_pending
    assert verify_password('123456',actor.password_hash)


def test_wrong_current_password_changes_nothing(db):
    s,actor,*_=db
    with pytest.raises(AppError) as err: save(db,username='changed',current_password='wrong')
    assert err.value.http_status==400 and actor.username=='zhangsan'
    assert not run(s.execute(select(AuditLog))).scalars().all()


def test_existing_username_is_rejected(db):
    _,actor,*_=db
    with pytest.raises(AppError) as err: save(db,username='lisi')
    assert err.value.http_status==409 and actor.username=='zhangsan'


def test_disabled_account_cannot_change(db):
    s,actor,*_=db;actor.status='disabled';run(s.flush())
    with pytest.raises(AppError) as err: save(db)
    assert err.value.http_status==403


def test_no_change_does_not_revoke(db):
    s,_,_,_,sessions=db
    result=save(db)
    assert result['data']['require_relogin'] is False
    assert run(load_active_session(s,sessions[0].sid,1)).status=='active'


@pytest.mark.parametrize('extra',[{'user_id':2},{'roles':['admin']},{'data_scope':'all'},{'username':'张三'},{'new_password':'密'*25}])
def test_invalid_or_privileged_fields_rejected(extra):
    with pytest.raises(ValidationError):
        PersonalAccountUpdate(**{'username':'zhangsan','current_password':'123456',**extra})
