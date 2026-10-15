"""企微客户建档：身份不明不合并、原负责人唯一匹配、预览和重试。"""
from types import SimpleNamespace as Obj

import pytest
from sqlalchemy import select, func, text
from sqlalchemy.orm import Session

from app.core.audit import AuditLog
from app.core.errors import AppError
from app.modules.customer.model import Customer, Contact
from app.modules.user.model import User
from app.modules.wecom.customer_import import _plan, preview_import, execute_import
from app.modules.wecom import customer_import
from app.modules.wecom.model import WeComExternalContact as External, WeComFollowRelationship as Follow, WeComSyncJob
from conftest import make_engine, SyncSessionAsAsync


def row(i=1, *, name='客户甲', full='', short=''):
    return Obj(id=i, external_userid=f'ext-{i}', name=name, corp_name=short,
               raw_data={'corp_full_name': full}, last_sync_at=None)


def user(i=1, uid='staff-1', status='active'):
    return Obj(id=i, wecom_userid=uid, status=status)


def relation(i=1, row_id=1, uid='staff-1'):
    return Obj(id=i, external_contact_id=row_id, wecom_userid=uid, status='active')


def company(i=1, name='甲有限公司', owner=1, kind='企业', deleted=False, status='active'):
    return Obj(id=i, name=name, customer_type=kind, owner_id=owner, deleted_at=deleted or None, status=status)


def snapshot(rows, users=None, customers=None, contacts=None, followers=None):
    return rows, users if users is not None else [user()], customers or [], contacts or [], followers if followers is not None else [relation(row_id=r.id, i=r.id) for r in rows]


def test_explicit_company_groups_contacts_but_personal_names_are_not_identity():
    rows=[row(1,full='甲有限公司'),row(2,full='甲有限公司'),row(3,name='张三'),row(4,name='张三')]
    summary, groups = _plan(snapshot(rows),'hold')
    assert summary['new_company_customers']==1
    assert summary['new_personal_customers']==2
    assert summary['import_contacts']==4
    assert len(groups)==3


@pytest.mark.parametrize('kwargs', [{'short':'甲公司'}, {'name':''}, {'name':'字'*65}, {'full':'字'*201}])
def test_uncertain_names_stay_pending(kwargs):
    result,_=_plan(snapshot([row(**kwargs)]),'hold')
    assert result['held_contacts']==1 and result['import_contacts']==0


@pytest.mark.parametrize('users,followers', [
    ([],[relation()]),
    ([user(status='disabled')],[relation()]),
    ([user(),user(2)],[relation()]),
    ([user()],[]),
    ([user(),user(2,'staff-2')],[relation(),relation(2,1,'staff-2')]),
])
def test_unbound_disabled_ambiguous_and_multiple_owners_never_enter_pool(users,followers):
    result,_=_plan(snapshot([row()],users=users,followers=followers),'hold')
    assert result['held_contacts']==1
    assert result['new_unassigned_customers']==0
    assert result['import_contacts']==0


def test_company_with_different_owners_needs_confirmation():
    result,_=_plan(snapshot([row(1,full='甲有限公司'),row(2,full='甲有限公司')],
        users=[user(),user(2,'staff-2')], followers=[relation(),relation(2,2,'staff-2')]),'hold')
    assert result['held_contacts']==2


@pytest.mark.parametrize('customers', [
    [company(),company(2)], [company(deleted=True)], [company(status='disabled')],
    [company(kind='个人')], [company(owner=2)],
])
def test_existing_customer_conflicts_are_not_merged(customers):
    result,_=_plan(snapshot([row(full='甲有限公司')],customers=customers),'hold')
    assert result['import_contacts']==0 and result['held_contacts']==1


def test_existing_personal_name_and_source_link_need_review():
    result,_=_plan(snapshot([row(name='张三')],customers=[company(name='张三',kind='个人')]),'hold')
    assert result['held_contacts']==1
    result,_=_plan(snapshot([row()],contacts=[Obj(id=1,external_userid='ext-1',customer_id=9,deleted_at=None)]),'hold')
    assert result['held_contacts']==1


@pytest.fixture
def db():
    engine=make_engine()
    # SQLite测试库明确使用与PG相同的部分唯一索引，而非把它退化成整列唯一。
    with engine.begin() as conn:
        conn.execute(text('DROP INDEX uq_contacts_primary_per_customer'))
        conn.execute(text('CREATE UNIQUE INDEX uq_contacts_primary_per_customer ON contacts(customer_id) WHERE is_primary AND deleted_at IS NULL'))
    for model in (External,Follow,WeComSyncJob,AuditLog):model.__table__.create(engine)
    with Session(engine) as raw:
        raw.add(User(id=1,name='员工甲',username='test-owner',password_hash='not-a-real-hash',status='active',wecom_userid='staff-1'))
        for i in (1,2):
            raw.add(External(id=i,external_userid=f'ext-{i}',name=f'联系人{i}',raw_data={'corp_full_name':'甲有限公司'},normalize_status='pending'))
            raw.add(Follow(id=i,external_contact_id=i,wecom_userid='staff-1',status='active'))
        raw.commit()
        yield raw, SyncSessionAsAsync(raw)
    engine.dispose()


@pytest.mark.anyio
async def test_company_import_real_persistence_idempotent_and_audited(db):
    raw,session=db
    preview=await preview_import(session)
    assert preview['new_company_customers']==1 and preview['import_contacts']==2
    result=await execute_import(session,digest=preview['digest'],source='SYSTEM')
    raw.commit()
    c=raw.scalars(select(Customer)).one()
    assert c.owner_id==1 and c.pool_status=='private' and c.level is None
    assert c.last_contact_unknown and c.last_followup_at is None
    contacts=raw.scalars(select(Contact)).all()
    assert len(contacts)==2 and {x.external_userid for x in contacts}=={'ext-1','ext-2'}
    assert all(x.customer_id==c.id and x.owner_id==1 for x in contacts)
    assert sum(x.is_primary for x in contacts)==1
    assert raw.scalar(select(func.count()).select_from(AuditLog))==1
    for ext in raw.scalars(select(External)):
        assert ext.crm_customer_id==c.id and ext.crm_contact_id
    with pytest.raises(AppError) as exc:
        await execute_import(session,digest=preview['digest'])
    assert exc.value.http_status==409
    again=await preview_import(session)
    repeat=await execute_import(session,digest=again['digest'])
    raw.commit()
    assert repeat['import_contacts']==0
    assert raw.scalar(select(func.count()).select_from(Customer))==1
    assert raw.scalar(select(func.count()).select_from(Contact))==2
    assert result['job_id']


@pytest.mark.anyio
async def test_changed_owner_binding_invalidates_preview(db):
    raw,session=db
    preview=await preview_import(session)
    raw.get(User,1).wecom_userid='other'
    raw.flush()
    with pytest.raises(AppError) as exc:
        await execute_import(session,digest=preview['digest'])
    assert exc.value.http_status==409
    assert raw.scalar(select(func.count()).select_from(Customer))==0


@pytest.mark.anyio
async def test_existing_company_preserves_customer_details(db):
    raw,session=db
    raw.add(Customer(id=10,name='甲有限公司',customer_type='企业',owner_id=1,level='A',source='老客户介绍',remark='不可改'))
    raw.commit()
    preview=await preview_import(session)
    assert preview['existing_customer_contacts']==2
    await execute_import(session,digest=preview['digest']);raw.commit()
    existing=raw.get(Customer,10)
    assert (existing.owner_id,existing.level,existing.source,existing.remark)==(1,'A','老客户介绍','不可改')
    assert raw.scalar(select(func.count()).select_from(Customer))==1


@pytest.mark.anyio
async def test_failed_second_contact_rolls_back_entire_batch(db,monkeypatch):
    raw,session=db
    preview=await preview_import(session)
    original=customer_import.create_contact_for_customer
    calls=0
    async def fail_second(*args,**kwargs):
        nonlocal calls
        calls+=1
        if calls==2:raise RuntimeError('simulated write failure')
        return await original(*args,**kwargs)
    monkeypatch.setattr(customer_import,'create_contact_for_customer',fail_second)
    with pytest.raises(RuntimeError):await execute_import(session,digest=preview['digest'])
    raw.rollback()
    assert raw.scalar(select(func.count()).select_from(Customer))==0
    assert raw.scalar(select(func.count()).select_from(Contact))==0
    assert all(x.crm_contact_id is None for x in raw.scalars(select(External)))


@pytest.mark.anyio
@pytest.mark.parametrize('permissions', [
    {'wecom:manage'}, {'wecom:manage','customer:create'},
    {'customer:create','customer:assign'},
])
async def test_http_requires_all_three_permissions(db,permissions):
    import httpx
    from fastapi import FastAPI
    from app.core.database import get_db
    from app.core.deps import get_current_user
    from app.core.errors import register_exception_handlers
    from app.modules.wecom.router import router
    raw,session=db
    app=FastAPI();register_exception_handlers(app);app.include_router(router)
    async def viewer():return Obj(id=1,roles=[],has=lambda code:code in permissions)
    async def database():yield session
    app.dependency_overrides[get_current_user]=viewer
    app.dependency_overrides[get_db]=database
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        assert (await client.get('/integrations/wecom/customer-import/preview')).status_code==403
        assert (await client.post('/integrations/wecom/customer-import',json={'digest':'a'*64})).status_code==403
    assert raw.scalar(select(func.count()).select_from(Customer))==0


@pytest.mark.anyio
async def test_http_preview_confirm_and_payload_rejection(db):
    import httpx
    from fastapi import FastAPI
    from app.core.database import get_db
    from app.core.deps import get_current_user
    from app.core.errors import register_exception_handlers
    from app.modules.wecom.router import router
    raw,session=db
    app=FastAPI();register_exception_handlers(app);app.include_router(router)
    async def viewer():return Obj(id=1,roles=[],has=lambda code:code in {'wecom:manage','customer:create','customer:assign'})
    async def database():yield session
    app.dependency_overrides[get_current_user]=viewer
    app.dependency_overrides[get_db]=database
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        preview=await client.get('/integrations/wecom/customer-import/preview')
        assert preview.status_code==200
        digest=preview.json()['data']['digest']
        assert (await client.post('/integrations/wecom/customer-import',json={'digest':digest,'owner_id':999})).status_code==400
        result=await client.post('/integrations/wecom/customer-import',json={'digest':digest})
        assert result.status_code==200 and result.json()['data']['import_contacts']==2
    assert raw.scalar(select(func.count()).select_from(Customer))==1
