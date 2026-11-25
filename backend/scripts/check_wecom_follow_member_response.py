"""本机隔离PG/正式API：企微跟进成员响应校验、失败记录及分页去重。"""
import os
import sys
from urllib.parse import urlsplit
sys.path.insert(0,"scripts")
from _test_support import require_isolated_db

db_name = require_isolated_db()
db_url = urlsplit(os.environ["DATABASE_URL"])
if not db_url.scheme.startswith("postgresql") or db_url.hostname not in ("127.0.0.1", "localhost") or not (
    db_name.startswith(("crm_iso", "crm_check", "crm_test")) or db_name.endswith("_test")
):
    raise SystemExit("只允许本机一次性隔离数据库")
os.environ.update(WECOM_PUSH_OFF="1",DINGTALK_PUSH_OFF="1",SCHEDULER_ENABLED="false",
                  DINGTALK_OA_STREAM_ENABLED="false",SCHEDULER_OA_SYNC_ENABLED="false")

import asyncio
from copy import deepcopy
from uuid import uuid4

import httpx
from sqlalchemy import text
from app.core.config import settings
from app.core.database import SessionLocal, engine
from app.main import app
from app.modules.wecom import client as adapter

async def main():
    original_client=adapter._client;original_cache=adapter._token_cache
    original_config=(settings.wecom_corp_id,settings.wecom_external_contact_secret)
    settings.wecom_corp_id='isolated-follow-member-response'
    settings.wecom_external_contact_secret='isolated-secret'
    adapter._token_cache={}
    mode={};calls=[];stamp=uuid4().hex[:12]
    extids=['iso-follow-'+stamp+'-a','iso-follow-'+stamp+'-b']
    def respond(request):
        calls.append((request.url.path,request.url.params.get('userid'),request.url.params.get('cursor')))
        path=request.url.path
        if path.endswith('/gettoken'):return httpx.Response(200,json={'access_token':'isolated-token'})
        if path.endswith('/get_follow_user_list'):return httpx.Response(200,json=mode['payload'])
        assert mode.get('normal') or 'page_payload' in mode,('unexpected downstream request',path)
        if path.endswith('/list'):
            if 'page_payload' in mode:
                if not mode.get('after_first') or request.url.params.get('cursor'):
                    return httpx.Response(200,json=mode['page_payload'])
                return httpx.Response(200,json={'external_userid':[extids[0]],'next_cursor':'second'})
            if request.url.params.get('userid')=='member-b':data={'external_userid':[extids[0]]}
            elif request.url.params.get('cursor')=='second':data={'external_userid':[extids[1]]}
            else:data={'external_userid':[extids[0]],'next_cursor':'second'}
        else:
            assert path.endswith('/get'),path
            extid=request.url.params['external_userid']
            assert extid in extids
            data={'external_contact':{'external_userid':extid,'name':'隔离成员响应验证'+extid[-1]}}
        return httpx.Response(200,json=data)
    async def facts():
        async with SessionLocal() as s:
            return {table:(await s.execute(text(f'SELECT to_jsonb(t) FROM {table} t ORDER BY id'))).scalars().all()
                    for table in ['wecom_external_contacts','wecom_follow_relationships','customers','contacts']}
    async def jobs():
        async with SessionLocal() as s:
            return (await s.execute(text("select to_jsonb(j) from wecom_sync_jobs j where job_type='external_contact' order by id"))).scalars().all()
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond),base_url='https://mock.invalid') as external:
            adapter._client=adapter.WeComClient(http=external)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,raise_app_exceptions=False),base_url='http://isolated') as c:
                r=await c.post('/api/v1/auth/login',json={'username':'admin','password':'admin123'});assert r.status_code==200,r.text
                c.headers['Authorization']='Bearer '+r.json()['data']['access_token']
                bad=[{}, {'follow_user':None}, {'follow_user':{}}, {'follow_user':'abc'}, {'follow_user':[1]},
                     {'follow_user':[None]}, {'follow_user':['']}, {'follow_user':[' ']}, {'follow_user':[True]}, {'follow_user':[{}]}]
                for payload in bad:
                    mode.clear();mode['payload']=deepcopy(payload);calls.clear();before=await facts();oldjobs=await jobs()
                    r=await c.post('/api/v1/integrations/wecom/sync-external-contacts')
                    after=await jobs();assert len(after)==len(oldjobs)+1
                    job=after[-1]
                    assert await facts()==before
                    assert all(path.endswith(('/gettoken','/get_follow_user_list')) for path,_,_ in calls)
                    print('malformed',repr(payload),'HTTP',r.status_code,'job',job['status'],job['success_count'],job['fail_count'],flush=True)
                    assert r.status_code==502 and '响应格式异常' in r.text,(r.status_code,r.text)
                    assert job['status']=='failed' and job['success_count']==0 and job['fail_count']==1,job
                    assert 'isolated-secret' not in r.text and 'isolated-token' not in r.text
                mode.clear();mode['payload']={'follow_user':[]};before=await facts();oldjobs=await jobs()
                r=await c.post('/api/v1/integrations/wecom/sync-external-contacts');assert r.status_code==200,r.text
                assert await facts()==before
                after=await jobs();assert len(after)==len(oldjobs)+1 and after[-1]['status']=='success' and after[-1]['success_count']==0
                print('PASS explicit empty member list remains successful zero with unchanged facts',flush=True)
                for empty_page in [{'external_userid':[]}, {'external_contact_list':[]}]:
                    mode.clear();mode.update(payload={'follow_user':['member-a']},page_payload=empty_page,after_first=False)
                    before=await facts();oldjobs=await jobs()
                    r=await c.post('/api/v1/integrations/wecom/sync-external-contacts')
                    assert r.status_code==200,r.text
                    after=await jobs()
                    assert len(after)==len(oldjobs)+1 and after[-1]['status']=='success' and after[-1]['success_count']==0
                    assert await facts()==before
                print('PASS both explicit empty customer page formats remain successful zero with unchanged facts',flush=True)
                for page_payload in [{}, {'external_userid':'not-a-list'}, {'external_contact_list':[{}]}]:
                    for after_first in [False,True]:
                        mode.clear();mode.update(payload={'follow_user':['member-a']},page_payload=page_payload,after_first=after_first)
                        before=await facts();oldjobs=await jobs();calls.clear()
                        r=await c.post('/api/v1/integrations/wecom/sync-external-contacts')
                        after=await jobs();assert len(after)==len(oldjobs)+1
                        print('malformed page',repr(page_payload),'after_first',after_first,'HTTP',r.status_code,'job',after[-1]['status'],flush=True)
                        assert r.status_code==502 and '响应格式异常' in r.text,(r.status_code,r.text)
                        assert after[-1]['status']=='failed' and after[-1]['success_count']==0 and after[-1]['fail_count']==1
                        assert await facts()==before
                        if after_first:assert ('/cgi-bin/externalcontact/list','member-a','second') in calls
                print('PASS malformed first/second pages retain one failure and no partial contacts or CRM mutations',flush=True)
                mode.clear();mode.update(payload={'follow_user':['member-a','member-b']},normal=True)
                before=await facts()
                for repeat in range(2):
                    calls.clear();oldjobs=await jobs()
                    r=await c.post('/api/v1/integrations/wecom/sync-external-contacts');assert r.status_code==200,r.text
                    after=await jobs();assert len(after)==len(oldjobs)+1 and after[-1]['status']=='success' and after[-1]['success_count']==2
                    current=await facts()
                    for table in ['customers','contacts','wecom_follow_relationships']:assert current[table]==before[table],table
                    matching=[row for row in current['wecom_external_contacts'] if row['external_userid'] in extids]
                    assert len(matching)==2 and {r['external_userid'] for r in matching}==set(extids)
                    assert len([p for p,_,_ in calls if p.endswith('/get')])==2,calls
                    assert ('/cgi-bin/externalcontact/list','member-a','second') in calls,calls
                print('PASS two members/two pages deduplicate same customer and repeat updates only; CRM facts unchanged',flush=True)
    finally:
        adapter._client=original_client;adapter._token_cache=original_cache
        settings.wecom_corp_id,settings.wecom_external_contact_secret=original_config
        await engine.dispose()
if __name__ == '__main__':
    asyncio.run(main())
