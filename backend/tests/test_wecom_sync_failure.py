"""同步整批失败需回滚事实、保留失败任务；仅内存数据库和HTTP故障替身。"""
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.core.errors import AppError
from app.modules.wecom import router, service
from app.modules.wecom.client import WeComError, WeComNotConfigured, WeComResponseError
from app.modules.wecom.model import WeComSyncJob, WeComExternalContact
from conftest import make_engine, SyncSessionAsAsync


@pytest.mark.anyio
@pytest.mark.parametrize('name,kind', [
    ('sync_departments', 'department'), ('sync_users', 'user'),
    ('sync_external_contacts', 'external_contact'), ('sync_follow_relations', 'follow_relation'),
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
