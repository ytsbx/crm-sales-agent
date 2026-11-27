"""同步整批失败需回滚事实、保留失败任务；仅内存数据库和HTTP故障替身。"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.core.errors import AppError
from app.modules.wecom import router, service
from app.modules.wecom.client import (
    WeComError, WeComNotConfigured, WeComResponseError, safe_error_text,
)
from app.modules.wecom.model import WeComSyncJob, WeComExternalContact, WeComFollowRelationship
from conftest import make_engine, SyncSessionAsAsync


@pytest.mark.anyio
@pytest.mark.parametrize('name,kind', [
    ('sync_departments', 'department'), ('sync_users', 'user'),
])
@pytest.mark.parametrize('failure', ['connect', 'timeout', 'http', 'business', 'configuration', 'response'])
async def test_sync_failure_rolls_back_facts_and_persists_one_failed_job(monkeypatch, name, kind, failure):
    engine = make_engine()
    for model in (WeComSyncJob, WeComExternalContact):
        model.__table__.create(engine)
    request = Request({'type': 'http', 'method': 'POST', 'path': '/', 'headers': [], 'client': ('127.0.0.1', 1)})
    http_request = httpx.Request('GET', 'https://mock.invalid/?access_token=secret-do-not-show')
    errors = {
        'connect': httpx.ConnectError('secret-do-not-show', request=http_request),
        'timeout': httpx.ReadTimeout('secret-do-not-show', request=http_request),
        'http': httpx.HTTPStatusError('secret-do-not-show', request=http_request, response=httpx.Response(503, request=http_request)),
        'business': WeComError(60020, '测试无权限', 'department/list'),
        'configuration': WeComNotConfigured('WECOM_CORP_ID'),
        'response': WeComResponseError('department/list'),
    }
    with Session(engine) as raw:
        raw.add(WeComExternalContact(external_userid='existing', name='原值'))
        raw.commit()
        async def failed_sync(session, *, user):
            await service.start_job(session, job_type=kind, operator_id=user.id)
            old = (await session.execute(select(WeComExternalContact))).scalar_one()
            old.name = '不应提交的修改'
            session.add(WeComExternalContact(external_userid='partial', name='不应提交的新值'))
            await session.flush()
            raise errors[failure]
        monkeypatch.setattr(service, name, failed_sync)
        with pytest.raises(AppError) as caught:
            await getattr(router, name)(request=request, user=SimpleNamespace(id=1), session=SyncSessionAsAsync(raw))
        assert caught.value.http_status == (422 if failure == 'configuration' else 502)
        # 模拟get_db在响应异常后再rollback：失败流水必须已独立提交。
        raw.rollback()
        rows = raw.scalars(select(WeComExternalContact)).all()
        assert [(r.external_userid, r.name) for r in rows] == [('existing', '原值')]
        jobs = raw.scalars(select(WeComSyncJob)).all()
        assert len(jobs) == 1
        job = jobs[0]
        assert job.job_type == kind and job.status == 'failed' and job.operator_id == 1
        assert job.success_count == 0 and job.fail_count == 1 and job.finished_at is not None
        assert job.error_message == caught.value.message
        assert 'secret-do-not-show' not in job.error_message
    engine.dispose()


# ---- B15-03 残余加固：safe_error_text 单元测试 + 单条落库不含凭据 ----------

@pytest.mark.parametrize('error,expected_fragment,forbidden', [
    (WeComError(60020, 'access_token=SECRET-LEAK', 'externalcontact/get'),
     '60020', 'SECRET-LEAK'),
    (WeComError(40014, 'https://qyapi.weixin.qq.com?access_token=SECRET-LEAK', 'message/send'),
     '40014', 'SECRET-LEAK'),
    (WeComResponseError('department/list', '返回 HTTP 503'),
     '503', 'access_token'),
    (WeComNotConfigured('WECOM_CORP_ID'),
     'WECOM_CORP_ID', 'secret'),
    (httpx.HTTPStatusError('SECRET-LEAK', request=httpx.Request('GET', 'https://x/?access_token=SECRET-LEAK'),
                           response=httpx.Response(503, request=httpx.Request('GET', 'https://x/'))),
     '503', 'SECRET-LEAK'),
    (httpx.ReadTimeout('SECRET-LEAK', request=httpx.Request('GET', 'https://x/?access_token=SECRET-LEAK')),
     '超时', 'SECRET-LEAK'),
    (httpx.ConnectError('SECRET-LEAK', request=httpx.Request('GET', 'https://x/?access_token=SECRET-LEAK')),
     '连接', 'SECRET-LEAK'),
    (RuntimeError('https://qyapi.weixin.qq.com/cgi-bin?access_token=SECRET-LEAK'),
     'RuntimeError', 'SECRET-LEAK'),
])
def test_safe_error_text_never_leaks_credentials(error, expected_fragment, forbidden):
    result = safe_error_text(error)
    assert expected_fragment in result
    assert forbidden not in result


@pytest.mark.anyio
async def test_single_item_sync_failure_stores_safe_error(monkeypatch):
    """单条 get_follow_users 失败时，job.error_message 不含外部 errmsg 原文。"""
    engine = make_engine()
    for model in (WeComSyncJob, WeComExternalContact, WeComFollowRelationship):
        model.__table__.create(engine)
    with Session(engine) as raw:
        raw.add(WeComExternalContact(id=1, external_userid='ext-1', name='隔离客户'))
        raw.add(WeComExternalContact(id=2, external_userid='ext-ok', name='正常客户'))
        raw.commit()

        class FailingAPI:
            async def get_follow_users(self, external_userid):
                if external_userid == 'ext-ok':
                    return []
                raise WeComError(60020, 'access_token=SECRET-LEAK invalid', 'externalcontact/get')

        monkeypatch.setattr(service.wecom_client, 'get_client', lambda: FailingAPI())
        job = await service.sync_follow_relations(
            SyncSessionAsAsync(raw), user=SimpleNamespace(id=1),
        )
        assert job.status == 'partial'
        assert job.fail_count == 1
        assert 'SECRET-LEAK' not in (job.error_message or '')
        assert '60020' in (job.error_message or '')
    engine.dispose()


@pytest.mark.anyio
async def test_http_503_via_real_client_stores_safe_error(monkeypatch):
    """真实客户端 + MockTransport HTTP 503（URL 带 token）→ 落库安全。"""
    from app.core.config import settings
    from app.modules.wecom.client import WeComClient, _token_cache

    monkeypatch.setattr(settings, 'wecom_corp_id', 'isolated-b15-03')
    monkeypatch.setattr(settings, 'wecom_external_contact_secret', 'isolated-secret')
    monkeypatch.setattr(settings, 'wecom_sync_max_pages', 1)
    _token_cache.clear()

    def handle(request):
        if 'gettoken' in str(request.url):
            return httpx.Response(200, json={'errcode': 0, 'access_token': 'tok'})
        return httpx.Response(503)

    engine = make_engine()
    for model in (WeComSyncJob, WeComExternalContact, WeComFollowRelationship):
        model.__table__.create(engine)
    with Session(engine) as raw:
        raw.add(WeComExternalContact(id=1, external_userid='ext-1', name='隔离客户'))
        raw.commit()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url='https://mock.invalid') as http:
            client = WeComClient(http=http)
            monkeypatch.setattr(service.wecom_client, 'get_client', lambda: client)
            job = await service.sync_follow_relations(
                SyncSessionAsAsync(raw), user=SimpleNamespace(id=1),
            )
        assert job.fail_count == 1
        assert 'tok' not in (job.error_message or '')
        assert 'access_token' not in (job.error_message or '')
        assert '503' in (job.error_message or '')
    engine.dispose()


# ---- B15-06：IntegrationLog 模型部分唯一索引与迁移一致 ----------------------

def test_integration_log_model_creates_partial_unique_index():
    """create_all 建出的索引必须阻止重复成功入站 ERP 事件，允许失败重试。"""
    from sqlalchemy.exc import IntegrityError
    from app.modules.integration.model import IntegrationLog

    engine = make_engine()
    IntegrationLog.__table__.create(engine)
    with Session(engine) as raw:
        raw.add(IntegrationLog(
            integration_type='erp', direction='inbound', status='success',
            event_key='dup-key', business_type='order',
        ))
        raw.commit()
        # 同键成功事件 → 唯一索引拒绝
        raw.add(IntegrationLog(
            integration_type='erp', direction='inbound', status='success',
            event_key='dup-key', business_type='order',
        ))
        with pytest.raises(IntegrityError):
            raw.commit()
        raw.rollback()
        # 同键失败事件 → 允许（部分索引只覆盖 success）
        raw.add(IntegrationLog(
            integration_type='erp', direction='inbound', status='failed',
            event_key='dup-key', business_type='order',
        ))
        raw.commit()
        # 出站同键成功 → 允许（部分索引只覆盖 inbound）
        raw.add(IntegrationLog(
            integration_type='erp', direction='outbound', status='success',
            event_key='dup-key', business_type='order',
        ))
        raw.commit()
    engine.dispose()


@pytest.mark.anyio
async def test_follow_sync_starts_background_job(monkeypatch):
    """跟进关系同步改为后台任务：接口立即返回 running 任务行，不再占着请求。"""
    from app.core.audit import AuditLog

    engine = make_engine()
    for model in (WeComSyncJob, AuditLog):
        model.__table__.create(engine)
    request = Request({'type': 'http', 'method': 'POST', 'path': '/', 'headers': [], 'client': ('127.0.0.1', 1)})
    with Session(engine) as raw:
        background = AsyncMock()
        monkeypatch.setattr(router, '_run_follow_relations_background', background)
        result = await router.sync_follow_relations(
            request=request, user=SimpleNamespace(id=1), session=SyncSessionAsAsync(raw),
        )
        assert result['data']['status'] == 'running'
        assert '已开始' in result['message']
        job = raw.scalars(select(WeComSyncJob)).one()
        assert job.job_type == 'follow_relation' and job.status == 'running'
        await asyncio.sleep(0)
        background.assert_awaited_once_with(job.id, 1)
    engine.dispose()


@pytest.mark.anyio
async def test_follow_background_failure_marks_job_failed(monkeypatch):
    """后台同步失败要把任务行标成 failed，并写入脱敏后的错误。"""
    from app.core.audit import AuditLog

    engine = make_engine()
    for model in (WeComSyncJob, AuditLog):
        model.__table__.create(engine)
    with Session(engine) as raw:
        job = WeComSyncJob(job_type='follow_relation', status='running', operator_id=1)
        raw.add(job)
        raw.commit()
        raw.refresh(job)

        class Ctx:
            async def __aenter__(self):
                return SyncSessionAsAsync(raw)

            async def __aexit__(self, *_):
                return False

        import app.core.database as database
        monkeypatch.setattr(database, 'SessionLocal', lambda: Ctx())
        async def failed_sync(session, *, user, job):
            raise WeComError(60020, 'access_token=SECRET-LEAK', 'externalcontact/get')
        monkeypatch.setattr(service, 'sync_follow_relations', failed_sync)

        await router._run_follow_relations_background(job.id, 1)
        raw.expire_all()
        row = raw.get(WeComSyncJob, job.id)
        assert row.status == 'failed' and row.fail_count == 1
        assert 'SECRET-LEAK' not in (row.error_message or '')
        assert '60020' in (row.error_message or '')
    engine.dispose()


# ---- 外部联系人同步：同样的后台任务形态（逐个成员拉列表，30 秒等不完） ----

@pytest.mark.anyio
async def test_external_sync_starts_background_job(monkeypatch):
    """外部联系人同步改为后台任务：接口立即返回 running 任务行，不再占着请求。"""
    from app.core.audit import AuditLog

    engine = make_engine()
    for model in (WeComSyncJob, AuditLog):
        model.__table__.create(engine)
    request = Request({'type': 'http', 'method': 'POST', 'path': '/', 'headers': [], 'client': ('127.0.0.1', 1)})
    with Session(engine) as raw:
        background = AsyncMock()
        monkeypatch.setattr(router, '_run_external_contacts_background', background)
        result = await router.sync_external_contacts(
            request=request, user=SimpleNamespace(id=1), session=SyncSessionAsAsync(raw),
        )
        assert result['data']['status'] == 'running'
        assert '已开始' in result['message']
        job = raw.scalars(select(WeComSyncJob)).one()
        assert job.job_type == 'external_contact' and job.status == 'running'
        await asyncio.sleep(0)
        background.assert_awaited_once_with(job.id, 1)
    engine.dispose()


@pytest.mark.anyio
async def test_external_background_failure_marks_job_failed(monkeypatch):
    """后台同步失败要把任务行标成 failed，并写入脱敏后的错误。"""
    from app.core.audit import AuditLog

    engine = make_engine()
    for model in (WeComSyncJob, AuditLog):
        model.__table__.create(engine)
    with Session(engine) as raw:
        job = WeComSyncJob(job_type='external_contact', status='running', operator_id=1)
        raw.add(job)
        raw.commit()
        raw.refresh(job)

        class Ctx:
            async def __aenter__(self):
                return SyncSessionAsAsync(raw)

            async def __aexit__(self, *_):
                return False

        import app.core.database as database
        monkeypatch.setattr(database, 'SessionLocal', lambda: Ctx())

        async def failed_sync(session, *, user, job):
            raise WeComError(60020, 'access_token=SECRET-LEAK', 'externalcontact/get')

        monkeypatch.setattr(service, 'sync_external_contacts', failed_sync)

        await router._run_external_contacts_background(job.id, 1)
        raw.expire_all()
        row = raw.get(WeComSyncJob, job.id)
        assert row.status == 'failed' and row.fail_count == 1
        assert 'SECRET-LEAK' not in (row.error_message or '')
        assert '60020' in (row.error_message or '')
    engine.dispose()
