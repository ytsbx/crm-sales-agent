"""事件只刷新关联单据；失败重投；页面手动查询不创建审批。"""
import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock, Mock

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
    sync=AsyncMock()
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
