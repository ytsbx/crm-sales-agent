"""客户导入适配契约：HTTP 替身及隔离内存库，不读取真实企微客户。"""
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy.orm import Session

from app.core.config import settings
from app.modules.customer.model import Contact, Customer
from app.modules.settings.model import SystemSetting
from app.modules.user.model import User
from app.modules.wecom.client import ExternalContactBatch, WeComClient, WeComError
from app.modules.wecom import service
from app.modules.wecom.model import WeComExternalContact, WeComFollowRelationship, WeComSyncJob, WeComUser
from conftest import SyncSessionAsAsync, make_engine, make_user


@pytest.mark.anyio
async def test_customer_candidates_do_not_use_the_following_employees_mobile():
    engine = make_engine()
    for model in (WeComExternalContact, WeComFollowRelationship, WeComUser, SystemSetting):
        model.__table__.create(engine)
    mobile = "18800001111"
    with Session(engine) as raw:
        raw.add_all([
            Customer(id=1, name="甲方零件公司"),
            Contact(customer_id=1, name="甲联系人", mobile=mobile),
            User(id=7, name="员工", username="staff", password_hash="fixture", mobile=mobile),
            WeComUser(wecom_userid="STAFF", user_id=7),
            WeComExternalContact(id=8, external_userid="CONTACT", corp_name="乙方包装公司"),
            WeComFollowRelationship(external_contact_id=8, wecom_userid="STAFF", status="active"),
            SystemSetting(key="dedup_scoring", value={"threshold": 50, "weight_mobile": 60}),
        ])
        raw.flush()
        session = SyncSessionAsAsync(raw)
        viewer = make_user(7, permissions=("customer:view",))
        assert (await service.unbound_candidates(session, 8, user=viewer))["candidates"] == []
        raw.add(Customer(id=2, name="乙方包装公司"))
        raw.add(Customer(id=3, name="乙方包装公司", owner_id=99))
        raw.flush()
        matches = (await service.unbound_candidates(session, 8, user=viewer))["candidates"]
        assert [match["id"] for match in matches] == [2]
        assert all("手机号一致" not in str(reason) for reason in matches[0]["reasons"])
        viewer.data_scope = "all"
        matches = (await service.unbound_candidates(session, 8, user=viewer))["candidates"]
        assert {match["id"] for match in matches} == {2, 3}
    engine.dispose()


@pytest.mark.anyio
async def test_member_sync_only_auto_binds_an_unambiguous_unbound_account(monkeypatch):
    engine = make_engine()
    for model in (WeComUser, WeComSyncJob):
        model.__table__.create(engine)

    class API:
        async def list_departments(self):
            return [{"id": 1, "parentid": 0}]

        async def list_department_users(self, *_args, **_kwargs):
            return [
                {"userid": "ambiguous", "mobile": "18800001111"},
                {"userid": "known", "mobile": "18800001111"},
                {"userid": "new", "mobile": "18800002222"},
                {"userid": "other", "mobile": "18800003333"},
            ]

    monkeypatch.setattr(service.wecom_client, "get_client", API)
    with Session(engine) as raw:
        for uid, mobile, binding in (
            (21, "18800001111", None), (22, "18800001111", "known"),
            (23, "18800002222", None), (24, "18800003333", "other-existing"),
        ):
            raw.add(User(id=uid, username=f"staff{uid}", name=f"员工{uid}",
                         password_hash="fixture", mobile=mobile, wecom_userid=binding))
        raw.flush()
        job = await service.sync_users(SyncSessionAsAsync(raw), user=SimpleNamespace(id=1))
        rows = {row.wecom_userid: (row.user_id, row.sync_status) for row in raw.query(WeComUser)}
        assert rows == {"ambiguous": (None, "conflict"), "known": (22, "synced"),
                        "new": (23, "synced"), "other": (None, "conflict")}
        assert raw.get(User, 21).wecom_userid is None
        assert raw.get(User, 24).wecom_userid == "other-existing"
        assert len(job.detail["conflicts"]) == 2
    engine.dispose()


@pytest.mark.anyio
async def test_member_sync_marks_absence_only_after_success_and_keeps_binding(monkeypatch):
    engine = make_engine()
    for model in (WeComUser, WeComSyncJob):
        model.__table__.create(engine)
    members = []
    departments = [{"id": 2, "parentid": 1}, {"id": 1, "parentid": 0}]

    class API:
        async def list_departments(self):
            return departments

        async def list_department_users(self, department_id, **_kwargs):
            assert department_id == (1 if len(departments) == 2 else 2)
            if members is None:
                raise WeComError(-1, "timeout", "user/list")
            return members

    monkeypatch.setattr(service.wecom_client, "get_client", API)
    with Session(engine) as raw:
        user = User(id=21, username="staff", name="员工", password_hash="fixture", wecom_userid="staff")
        mapping = WeComUser(wecom_userid="staff", user_id=21, name="员工", sync_status="synced")
        raw.add_all([user, mapping])
        raw.flush()
        session = SyncSessionAsAsync(raw)
        members = None
        with pytest.raises(WeComError):
            await service.sync_users(session, user=SimpleNamespace(id=1))
        assert mapping.sync_status == "synced"
        members = []
        root = departments.pop()
        await service.sync_users(session, user=SimpleNamespace(id=1))
        assert mapping.sync_status == "synced"
        departments.append(root)
        job = await service.sync_users(session, user=SimpleNamespace(id=1))
        assert mapping.sync_status == "not_returned"
        assert job.detail["not_returned"] == ["员工"]
        assert mapping.user_id == 21 and user.wecom_userid == "staff"
        members = [{"userid": "staff", "name": "员工"}]
        await service.sync_users(session, user=SimpleNamespace(id=1))
        assert mapping.sync_status == "synced"
    engine.dispose()


@pytest.mark.anyio
async def test_successful_empty_followers_expire_only_that_contacts_old_relationships(monkeypatch):
    engine = make_engine()
    for model in (WeComSyncJob, WeComExternalContact, WeComFollowRelationship):
        model.__table__.create(engine)

    class API:
        async def get_follow_users(self, external_userid):
            if external_userid == "empty":
                return []
            raise WeComError(-1, "timeout", "externalcontact/get")

    monkeypatch.setattr(service.wecom_client, "get_client", API)
    with Session(engine) as raw:
        raw.add_all([
            WeComExternalContact(id=1, external_userid="empty"),
            WeComExternalContact(id=2, external_userid="failure"),
            WeComFollowRelationship(external_contact_id=1, wecom_userid="staff", status="active"),
            WeComFollowRelationship(external_contact_id=2, wecom_userid="staff", status="active"),
        ])
        raw.flush()
        job = await service.sync_follow_relations(SyncSessionAsAsync(raw), user=SimpleNamespace(id=1))
        assert {row.external_contact_id: row.status for row in raw.query(WeComFollowRelationship)} == {
            1: "deleted", 2: "active",
        }
        assert (job.status, job.success_count, job.fail_count) == ("partial", 0, 1)
        assert job.detail["marked_deleted"] == 1
    engine.dispose()


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
