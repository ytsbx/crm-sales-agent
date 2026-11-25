"""真实隔离PG/ASGI：断线保存、未完成工具回滚及提交回执不确定性。"""
import os
from urllib.parse import urlparse
from uuid import uuid4
from _test_support import require_isolated_db
name=require_isolated_db()
u=urlparse(os.environ['DATABASE_URL'])
if u.hostname not in ('127.0.0.1','localhost') or not u.scheme.startswith('postgresql') or not (name.startswith(('crm_iso','crm_check','crm_test')) or name.endswith('_test')):
    raise SystemExit('只允许显式本机一次性PostgreSQL')
os.environ.update(WECOM_PUSH_OFF='1',DINGTALK_PUSH_OFF='1',SCHEDULER_ENABLED='false',SCHEDULER_OA_SYNC_ENABLED='false',DINGTALK_OA_STREAM_ENABLED='false')
import asyncio, json
from types import SimpleNamespace as NS
from unittest.mock import patch
from fastapi import FastAPI
from sqlalchemy import select, delete
from app.core.database import SessionLocal, engine
from app.core.deps import CurrentUser, get_current_user
from app.modules.agent import router, runtime

from app.modules.agent.model import AgentMessage, AgentSession, AgentAction
from app.modules.task.model import Task
from app.modules.user.model import User

class Model:
    def __init__(self):
        self.waiting=asyncio.Event()
        self.closed=False
        self.close_started=False
        self.chat=NS(completions=self)
    async def create(self,**kwargs):
        async def chunks():
            yield NS(choices=[NS(delta=NS(content='synthetic partial',tool_calls=None),finish_reason=None)])
            if mode=='tool':
                call=NS(index=0,id='synthetic-call',function=NS(name='search_customers',arguments='{}'))
                yield NS(choices=[NS(delta=NS(content=None,tool_calls=[call]),finish_reason='tool_calls')])
            elif mode!='ack':
                self.waiting.set()
                await asyncio.Event().wait()
        return chunks()
    async def close(self):
        self.close_started=True
        await asyncio.sleep(0)
        self.closed=True

async def main(case):
    global mode
    mode=case
    uid=sid=None
    try:
        async with SessionLocal() as s:
            user=User(username='stream-review-'+uuid4().hex,name='isolated',password_hash='!',status='active')
            s.add(user);await s.flush();uid=user.id
            chat=AgentSession(user_id=uid,title='stream review');s.add(chat);await s.flush();sid=chat.id
            actor=CurrentUser(user,{'agent:use','customer:view'},['salesperson'],'self')
            await s.commit()
        app=FastAPI();app.include_router(router.router)
        app.dependency_overrides[get_current_user]=lambda:actor
        model=Model();frames=[];received=False
        body=json.dumps({'content':'synthetic test'}).encode()
        async def receive():
            nonlocal received
            if not received:
                received=True
                return {'type':'http.request','body':body,'more_body':False}
            await model.waiting.wait()
            return {'type':'http.disconnect'}
        async def send(event): frames.append(event)
        if case=='ack':
            async with SessionLocal() as s:
                class LostAck:
                    def __init__(self):self.commits=0
                    def __getattr__(self,name):return getattr(s,name)
                    async def commit(self):
                        await s.commit();self.commits+=1
                        if self.commits==2:raise asyncio.CancelledError()
                chat=await s.get(AgentSession,sid)
                with patch.object(runtime,'_client',lambda:model),patch.object(runtime,'model_ready',lambda:True):
                    try:
                        async for _ in runtime.run_turn_events(LostAck(),agent_session=chat,user=actor,text='synthetic test'):pass
                    except asyncio.CancelledError:pass
                    else:raise AssertionError('cancellation was swallowed')
            async with SessionLocal() as s:
                rows=(await s.scalars(select(AgentMessage).where(AgentMessage.session_id==sid,AgentMessage.role=='assistant'))).all()
                assert len(rows)==1 and rows[0].content=='synthetic partial'
            print('commit succeeded before cancellation: one full reply, no duplicate',flush=True)
            return
        path=f'/agent/sessions/{sid}/messages/stream' 
        scope={'type':'http','asgi':{'version':'3.0','spec_version':'2.3'},'http_version':'1.1','method':'POST',
               'scheme':'http','path':path,'raw_path':path.encode(),'query_string':b'',
               'headers':[(b'content-type',b'application/json'),(b'content-length',str(len(body)).encode())],
               'client':('127.0.0.1',1),'server':('test',80),'root_path':''}
        from app.modules.agent.tools import TOOLS
        async def interrupted_handler(ctx,**kwargs):
            ctx.session.add(AgentAction(session_id=sid,tool_name='create_task',action_type='create_task',title='unfinished synthetic action',risk_level='L2',status='awaiting_confirmation'))
            ctx.session.add(Task(title='unfinished synthetic task',owner_id=uid,status='pending'))
            await ctx.session.flush()
            model.waiting.set()
            await asyncio.Event().wait()
        with patch.object(TOOLS['search_customers'],'handler',interrupted_handler), patch.object(runtime,'_client',lambda:model),patch.object(runtime,'model_ready',lambda:True):
            await asyncio.wait_for(app(scope,receive,send),5)
        # Let shielded session disposal tasks finish before inspecting the pool.
        for _ in range(10): await asyncio.sleep(0)
        async with SessionLocal() as s:
            messages=(await s.scalars(select(AgentMessage).where(AgentMessage.session_id==sid))).all()
            roles=[m.role for m in messages]
        result={'case':case,'status':[e.get('status') for e in frames if e['type']=='http.response.start'],
                'delta_sent':any(b'event: delta' in e.get('body',b'') for e in frames),
                'roles':roles,'checked_out':engine.pool.checkedout()}
        print(result,flush=True)
        assert result['status']==[200] and result['delta_sent']
        assert roles==['user','assistant'] and result['checked_out']==0, 'actual disconnect must preserve emitted partial reply'
        async with SessionLocal() as s:
            assert not (await s.execute(select(AgentAction.id).where(AgentAction.session_id==sid))).all()
            assert not (await s.execute(select(Task.id).where(Task.owner_id==uid))).all()
            assistant=await s.scalar(select(AgentMessage).where(AgentMessage.session_id==sid,AgentMessage.role=='assistant'))
            assert assistant.content=='synthetic partial\n\n（连接中断，以上为已生成的部分）'
        print(case+': partial persisted, unfinished tool action absent',flush=True)

    finally:
        if uid is not None:
            async with SessionLocal() as s:
                await s.execute(delete(Task).where(Task.owner_id==uid))
                if sid is not None:
                    await s.execute(delete(AgentAction).where(AgentAction.session_id==sid))
                    await s.execute(delete(AgentMessage).where(AgentMessage.session_id==sid))
                    await s.execute(delete(AgentSession).where(AgentSession.id==sid))
                await s.execute(delete(User).where(User.id==uid));await s.commit()
        await engine.dispose()
async def matrix():
    for case in ('text','tool','ack'):await main(case)
asyncio.run(matrix())
