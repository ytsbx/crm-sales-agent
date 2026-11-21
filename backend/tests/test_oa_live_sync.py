"""事件只刷新关联单据；失败重投；页面手动查询不创建审批。"""
import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.core.config import settings
from app.core.errors import AppError
from app.modules.dingtalk import router, service, oa_stream
from app.modules.dingtalk.model import OaInstance


@pytest.mark.anyio
async def test_stream_only_refreshes_linked_instance_and_checks_template(monkeypatch):
    session=Obj(execute=AsyncMock(),commit=AsyncMock())
    row=Obj(id=9,process_code='PROC-SAMPLE',error=None)
    session.execute.return_value=Obj(scalar_one_or_none=lambda:row)
    class Context:
        async def __aenter__(self): return session
        async def __aexit__(self,*_): pass
    monkeypatch.setattr(oa_stream,'SessionLocal',Context)
    sync=AsyncMock(return_value={'checked':1,'changed':1})
    monkeypatch.setattr(service,'sync_pending_instances',sync)
    stream=oa_stream.OaStream()
    await stream.process_event({'eventType':'unrelated'},{'processInstanceId':'ding'})
    session.execute.assert_not_awaited()
    await stream.process_event({'eventType':'bpms_task_change'},{'processInstanceId':'ding','processCode':'OTHER'})
    sync.assert_not_awaited()
    await stream.process_event({'eventType':'bpms_instance_change'},{'processInstanceId':'ding','processCode':'PROC-SAMPLE'})
    sync.assert_awaited_once_with(session,oa_id=9)
    session.commit.assert_awaited_once()
    assert stream.last_event_at
    session.execute.return_value=Obj(scalar_one_or_none=lambda:None)
    await stream.process_event({'eventType':'bpms_task_change'},{'processInstanceId':'not-in-crm'})
    assert sync.await_count==1


@pytest.mark.anyio
@pytest.mark.parametrize('failed',[False,True])
async def test_stream_acknowledges_only_after_refresh(monkeypatch,failed):
    stream=oa_stream.OaStream()
    stream.process_event=AsyncMock(side_effect=RuntimeError('private-data') if failed else None)
    socket=Obj(send=AsyncMock())
    frame={'type':'EVENT','headers':{'messageId':'event-1','eventType':'bpms_task_change'},'data':json.dumps({'processInstanceId':'ding'})}
    await stream.handle_frame(socket,frame)
    ack=json.loads(socket.send.await_args.args[0])
    assert ack['headers']['messageId']=='event-1'
    assert ack['code']==(500 if failed else 200)
    assert 'private-data' not in ack['message']


@pytest.mark.anyio
async def test_stream_stop_cancels_without_reconnect():
    stream=oa_stream.OaStream()
    stream.task=asyncio.create_task(asyncio.sleep(60))
    await stream.stop()
    assert stream.task is None and not stream.connected


@pytest.mark.anyio
async def test_manual_sync_scoped_to_current_record_and_throttled(monkeypatch):
    monkeypatch.setattr(settings,'dingtalk_push_off',False)
    row=Obj(id=7,instance_id='ding-7',synced_at=datetime.now(UTC)-timedelta(minutes=1),error=None)
    session=Obj(get=AsyncMock(return_value=row),commit=AsyncMock())
    monkeypatch.setattr(router,'ensure_oa_type_scope',AsyncMock())
    monkeypatch.setattr(service,'lock_oa_business_then_instance',AsyncMock(return_value=row))
    sync=AsyncMock(return_value={'checked':1,'changed':0})
    monkeypatch.setattr(service,'sync_pending_instances',sync)
    monkeypatch.setattr(service,'serialize',lambda r:{'id':r.id})
    await router.sync_oa_instance(7,Obj(),session)
    sync.assert_awaited_once_with(session,oa_id=7)
    row.synced_at=datetime.now(UTC)
    await router.sync_oa_instance(7,Obj(),session)
    assert sync.await_count==1
    row.error='upstream error'
    with pytest.raises(AppError):await router.sync_oa_instance(7,Obj(),session)


@pytest.mark.anyio
async def test_manual_sync_requires_permission_before_network(monkeypatch):
    session=Obj(get=AsyncMock(return_value=Obj(id=7,oa_type='sample')))
    actor=Obj(roles=[],has=lambda _:False)
    with pytest.raises(AppError):await router.sync_oa_instance(7,actor,session)


@pytest.mark.anyio
async def test_manual_sync_returns_conflict_when_business_lock_is_busy(monkeypatch):
    row = Obj(id=7, provider='self_oa')
    session = Obj(get=AsyncMock(return_value=row), commit=AsyncMock())
    monkeypatch.setattr(router, 'ensure_oa_type_scope', AsyncMock())
    monkeypatch.setattr(service, 'lock_oa_business_then_instance', AsyncMock(return_value=None))
    from app.modules.oa import service as own
    sync = AsyncMock()
    monkeypatch.setattr(own, 'sync_self_oa_row', sync)
    with pytest.raises(AppError, match='请稍后同步') as caught:
        await router.sync_oa_instance(7, Obj(), session)
    assert caught.value.http_status == 409
    sync.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.anyio
async def test_delayed_query_cannot_overwrite_withdrawal_completed_while_waiting(monkeypatch):
    row = Obj(id=7, instance_id="remote-7", status="pending", updated_at=datetime.now(UTC))
    session = Obj(commit=AsyncMock())
    lock = AsyncMock(return_value=row)
    monkeypatch.setattr(service, "lock_oa_business_then_instance", lock)

    async def fetch(instance_id):
        assert instance_id == "remote-7"
        session.commit.assert_awaited_once()
        lock.assert_not_awaited()
        row.status = "withdrawn"
        return {"status": "pending"}

    assert await service.query_oa_without_locks(session, row, fetch) == (None, None, None)
    assert row.status == "withdrawn"


@pytest.mark.anyio
async def test_stuck_self_oa_submission_is_never_resent_and_only_matching_receipt_can_be_adopted(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from starlette.requests import Request

    from app.modules.inquiry.model import CustomInquiry
    from app.modules.oa import client as own_client
    from tests.conftest import SyncSessionAsAsync, _register_sqlite_bigint_as_integer

    _register_sqlite_bigint_as_integer()
    engine = create_engine("sqlite://")
    OaInstance.__table__.create(engine)
    CustomInquiry.__table__.create(engine)
    monkeypatch.setattr(own_client, "configured", lambda: True)
    monkeypatch.setattr(router, "ensure_oa_type_scope", AsyncMock())
    monkeypatch.setattr(router, "write_audit", AsyncMock())
    receipt_id = str(uuid4())
    read = AsyncMock(return_value={"id": receipt_id, "external_ref": "different", "status": "pending"})
    create = AsyncMock()
    monkeypatch.setattr(own_client, "get_approval", read)
    monkeypatch.setattr(own_client, "create_approval", create)
    with Session(engine) as raw:
        raw.add(CustomInquiry(id=1, title="定制询价"))
        raw.add(OaInstance(id=1, inquiry_id=1, oa_type="inquiry", provider="self_oa",
            idempotency_key="crm-inquiry:1:1", status="submitting",
            created_at=datetime.now(UTC) - timedelta(minutes=11)))
        raw.commit()
        session = SyncSessionAsAsync(raw)
        assert await service.sync_pending_instances(session) == {"checked": 1, "changed": 1}
        await session.commit()
        assert raw.get(OaInstance, 1).status == "needs_review"
        read.assert_not_awaited()
        req = Request({"type": "http", "headers": []})
        payload = router.ResolveOa(action="adopt", instance_id=receipt_id)
        with pytest.raises(AppError, match="不匹配"):
            await router.resolve_oa_instance(1, payload, req, Obj(id=1), session)
        read.return_value["external_ref"] = "crm-inquiry:1:1"
        await router.resolve_oa_instance(1, payload, req, Obj(id=1), session)
        await router.resolve_oa_instance(1, payload, req, Obj(id=1), session)
        assert raw.get(OaInstance, 1).status == "pending"
        assert raw.get(CustomInquiry, 1).extra["oa_approval"]["instance_id"] == receipt_id
        assert read.await_count == 2
        create.assert_not_awaited()
    engine.dispose()


@pytest.mark.anyio
async def test_targeted_query_refreshes_terminal_row_without_touching_other_rows(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from tests.conftest import SyncSessionAsAsync,_register_sqlite_bigint_as_integer
    _register_sqlite_bigint_as_integer()
    monkeypatch.setattr(settings,'dingtalk_push_off',False)
    engine=create_engine('sqlite://')
    OaInstance.__table__.create(engine)
    with Session(engine) as raw:
        for i in (1,2):
            raw.add(OaInstance(id=i,inquiry_id=i,inquiry_version=1,oa_type='inquiry',instance_id=f'ding-{i}',
                idempotency_key=f'key-{i}',status='approved',created_at=datetime.now(UTC)))
        raw.commit()
        read=AsyncMock(return_value={'status':'COMPLETED','result':'agree','operationRecords':[{'remark':'新增回复'}]})
        monkeypatch.setattr(service,'get_client',lambda:Obj(get_process_instance=read))
        await service.sync_pending_instances(SyncSessionAsAsync(raw),oa_id=2)
        read.assert_awaited_once_with('ding-2')
        assert raw.get(OaInstance,1).synced_at is None
        assert raw.get(OaInstance,2).result['payload']['operationRecords'][0]['remark']=='新增回复'
    engine.dispose()


@pytest.mark.anyio
async def test_display_template_cached_but_submission_schema_not_cached(monkeypatch):
    from app.modules.dingtalk.client import DingTalkClient
    client=DingTalkClient()
    monkeypatch.setattr(client,'_require',Mock())
    read=AsyncMock(return_value={'schemaContent':{'items':[]}})
    monkeypatch.setattr(client,'get_process_schema',read)
    a=await client.get_display_process_schema('PROC')
    a['schemaContent']['items'].append('changed')
    b=await client.get_display_process_schema('PROC')
    assert b['schemaContent']['items']==[]
    assert read.await_count==1
    await client.get_process_schema('PROC')
    assert read.await_count==2


@pytest.mark.anyio
@pytest.mark.parametrize('age,error,calls', [(None,None,1),(1,None,0),(4,None,1),(1,'temporary',1)])
async def test_self_oa_polling_coalesces_recent_queries_after_permission_check(monkeypatch, age, error, calls):
    from app.modules.oa import service as own
    row = Obj(id=7, provider='self_oa', synced_at=None if age is None else datetime.now(UTC)-timedelta(seconds=age), error=error)
    session = Obj(get=AsyncMock(return_value=row), commit=AsyncMock())
    scope = AsyncMock()
    monkeypatch.setattr(router, 'ensure_oa_type_scope', scope)
    lock = AsyncMock(return_value=row)
    monkeypatch.setattr(service, 'lock_oa_business_then_instance', lock)
    sync = AsyncMock()
    monkeypatch.setattr(own, 'sync_self_oa_row', sync)
    monkeypatch.setattr(own, 'serialize', lambda row: {'id':row.id})
    await router.sync_oa_instance(7,Obj(),session)
    assert sync.await_count == calls
    assert scope.await_count == 2 + calls
    lock.assert_awaited_once_with(session,row)
    session.get.assert_awaited_once_with(OaInstance,7)


@pytest.mark.anyio
async def test_disabled_legacy_rows_cannot_starve_live_oa_polling(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from tests.conftest import SyncSessionAsAsync, _register_sqlite_bigint_as_integer
    from app.modules.oa import client as own_client, service as own
    from app.modules.inquiry.model import CustomInquiry
    _register_sqlite_bigint_as_integer()
    engine=create_engine('sqlite://')
    OaInstance.__table__.create(engine)
    CustomInquiry.__table__.create(engine)
    monkeypatch.setattr(settings,'dingtalk_push_off',True)
    monkeypatch.setattr(own_client,'configured',lambda:True)
    sync=AsyncMock()
    monkeypatch.setattr(own,'sync_self_oa_row',sync)
    with Session(engine) as raw:
        for identity,provider in [(1,'dingtalk'),(2,'self_oa')]:
            raw.add(OaInstance(id=identity,inquiry_id=identity,inquiry_version=1,provider=provider,
                oa_type='inquiry',instance_id=str(identity),status='pending',idempotency_key=str(identity),created_at=datetime.now(UTC)))
        raw.commit()
        result=await service.sync_pending_instances(SyncSessionAsAsync(raw),limit=1)
        assert result['checked']==1
        assert sync.await_args.args[1].id==2
    engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize('failure', ['explicit', 'unknown', 'configuration'])
async def test_self_oa_query_failure_returns_safe_business_error_without_committing(monkeypatch, failure):
    from app.modules.oa import client as own_client, service as own
    exceptions = {
        'explicit': own_client.OaError('private upstream response'),
        'unknown': own_client.OaUnknownOutcome('private upstream URL'),
        'configuration': own_client.OaNotConfigured('private configuration'),
    }
    row = Obj(id=7, provider='self_oa', synced_at=None, error=None, status='pending')
    session = Obj(get=AsyncMock(return_value=row), commit=AsyncMock())
    monkeypatch.setattr(router, 'ensure_oa_type_scope', AsyncMock())
    monkeypatch.setattr(service, 'lock_oa_business_then_instance', AsyncMock(return_value=row))
    monkeypatch.setattr(own, 'sync_self_oa_row', AsyncMock(side_effect=exceptions[failure]))
    with pytest.raises(AppError, match='暂时无法确认最新审批结果') as caught:
        await router.sync_oa_instance(7, Obj(), session)
    assert caught.value.http_status == 502
    assert 'private' not in caught.value.message
    assert row.status == 'pending'
    session.commit.assert_not_awaited()


@pytest.mark.parametrize('provider,auto_sync,expected', [
    ('self_oa', True, None),
    ('self_oa', False, '同序 OA'),
    ('dingtalk', True, '钉钉连接当前已关闭'),
])
def test_sync_notice_uses_the_record_provider(monkeypatch, provider, auto_sync, expected):
    monkeypatch.setattr(settings, 'dingtalk_push_off', True)
    monkeypatch.setattr(settings, 'scheduler_enabled', False)
    monkeypatch.setattr(settings, 'scheduler_oa_sync_enabled', auto_sync)
    notice = service.serialize(OaInstance(provider=provider, status='pending'))['sync_notice']
    if expected is None:
        assert notice is None
    else:
        assert expected in notice
