"""客户导入适配契约：HTTP 替身及隔离内存库，不读取真实企微客户。"""
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy.orm import Session

from app.core.config import settings
from app.modules.wecom.client import ExternalContactBatch, WeComClient, WeComError
from app.modules.wecom import service
from app.modules.wecom.model import WeComExternalContact, WeComFollowRelationship, WeComSyncJob
from conftest import SyncSessionAsAsync, make_engine


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, 'wecom_corp_id', 'test-corp')
    monkeypatch.setattr(settings, 'wecom_external_contact_secret', 'test-secret')
    monkeypatch.setattr(settings, 'wecom_sync_max_pages', 3)


@pytest.mark.anyio
async def test_customer_followers_use_detail_and_collect_pages(configured):
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.path.endswith('/gettoken'):
            return httpx.Response(200, json={'access_token': 'test-token', 'expires_in': 7200})
        assert request.url.path == '/cgi-bin/externalcontact/get'
        assert request.url.params['external_userid'] == 'customer-1'
        if request.url.params.get('cursor'):
            assert request.url.params['cursor'] == 'second'
            return httpx.Response(200, json={'follow_user': [{'userid': 'b'}, {'userid': 'a'}]})
        return httpx.Response(200, json={'follow_user': [{'userid': 'a'}], 'next_cursor': 'second'})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url='https://mock.invalid') as http:
        rows = await WeComClient(http=http).get_follow_users('customer-1')
    assert {row['userid'] for row in rows} == {'a', 'b'}
    assert len(rows) == 2
    assert all('get_follow_user_list' not in str(r.url) for r in requests)


@pytest.mark.anyio
@pytest.mark.parametrize('payload', [
    {}, {'follow_user': ['employee-id']}, {'follow_user': [{}]},
    {'follow_user': [], 'next_cursor': 'repeat'},
    {'errcode': 60020, 'errmsg': 'blocked'},
])
async def test_invalid_or_incomplete_followers_never_return_empty_success(configured, payload):
    def handle(request):
        if request.url.path.endswith('/gettoken'):
            return httpx.Response(200, json={'access_token': 'test-token', 'expires_in': 7200})
        return httpx.Response(200, json=payload)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url='https://mock.invalid') as http:
        with pytest.raises(WeComError):
            await WeComClient(http=http).get_follow_users('customer-1')


@pytest.mark.anyio
async def test_partial_customer_list_reports_failures_and_retries_failed_detail(configured):
    attempts = 0
    def handle(request):
        nonlocal attempts
        path = request.url.path
        if path.endswith('/gettoken'):
            data = {'access_token': 'test-token', 'expires_in': 7200}
        elif path.endswith('/get_follow_user_list'):
            data = {'follow_user': ['a', 'b']}
        elif path.endswith('/list'):
            data = {'external_userid': ['customer-1']}
        else:
            attempts += 1
            data = ({'errcode': 84061, 'errmsg': 'no permission'} if attempts == 1 else
                    {'external_contact': {'external_userid': 'customer-1', 'name': 'test'}})
        return httpx.Response(200, json=data)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url='https://mock.invalid') as http:
        rows = await WeComClient(http=http).list_all_external_contacts()
    assert attempts == 2
    assert len(rows) == 1
    assert len(rows.errors) == 1


@pytest.mark.anyio
async def test_partial_results_recorded_and_failed_followers_preserved(monkeypatch):
    engine = make_engine()
    for model in (WeComSyncJob, WeComExternalContact, WeComFollowRelationship):
        model.__table__.create(engine)
    class API:
        async def list_all_external_contacts(self):
            return ExternalContactBatch([{'external_userid': 'customer-1', 'name': 'test'}], ['b: 84061'])
        async def get_follow_users(self, external_userid):
            raise WeComError(-1, 'second page failed', 'externalcontact/get')
    monkeypatch.setattr(service.wecom_client, 'get_client', lambda: API())
    with Session(engine) as raw:
        session = SyncSessionAsAsync(raw)
        job = await service.sync_external_contacts(session, user=SimpleNamespace(id=1))
        assert (job.status, job.success_count, job.fail_count) == ('partial', 1, 1)
        assert job.detail['failed_members'] == 1
        raw.flush()
        contact = raw.query(WeComExternalContact).one()
        relation = WeComFollowRelationship(external_contact_id=contact.id, wecom_userid='a', status='active')
        raw.add(relation)
        raw.flush()
        second = await service.sync_follow_relations(session, user=SimpleNamespace(id=1))
        assert second.fail_count == 1
        assert relation.status == 'active'
    engine.dispose()


@pytest.mark.anyio
async def test_follower_names_resolve_without_creating_or_binding_accounts():
    from app.modules.wecom.model import WeComUser
    from app.modules.user.model import User
    engine=make_engine()
    for model in (WeComExternalContact,WeComFollowRelationship,WeComUser):
        model.__table__.create(engine)
    with Session(engine) as raw:
        raw.add(WeComExternalContact(id=1,external_userid='external-internal-id',name='客户',normalize_status='pending'))
        raw.add(User(id=1,name='CRM姓名',username='existing-name',password_hash='!',wecom_userid='bound'))
        raw.add(WeComUser(wecom_userid='unbound',name='李浩轩'))
        raw.add(WeComUser(wecom_userid='bound',name='企微原姓名'))
        for i,uid in enumerate(('unbound','bound','unknown'),1):
            raw.add(WeComFollowRelationship(external_contact_id=1,wecom_userid=uid,status='active'))
        raw.flush()
        rows,total=await service.unbound_contacts(SyncSessionAsAsync(raw),keyword=None,page=1,page_size=20)
        assert total==1
        followers={item['wecom_userid']:item['name'] for item in rows[0]['followers']}
        assert followers=={'unbound':'李浩轩','bound':'企微原姓名','unknown':None}
        assert raw.query(User).count()==1
        assert raw.query(WeComUser).filter_by(wecom_userid='unbound').one().user_id is None
    engine.dispose()
