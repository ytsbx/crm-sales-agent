"""本机隔离PG/正式API：批量线索分配原因超长拒绝、合法边界完整保留。"""
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
from uuid import uuid4

import httpx
from sqlalchemy import select, text
from app.core.database import SessionLocal, engine
from app.main import app
from app.modules.user.model import User

from app.modules.lead.model import Lead

async def main():
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,raise_app_exceptions=False),base_url='http://isolated') as c:
            r=await c.post('/api/v1/auth/login',json={'username':'admin','password':'admin123'});assert r.status_code==200,r.text
            c.headers['Authorization']='Bearer '+r.json()['data']['access_token']
            async with SessionLocal() as s:
                target=(await s.execute(select(User.id).where(User.username=='zhangsan'))).scalar_one()
                leads=[Lead(name='隔离批分原因'+uuid4().hex[:12],status='pending') for _ in range(2)]
                s.add_all(leads);await s.commit();ids=[v.id for v in leads]
            async def stored():
                async with SessionLocal() as s:
                    rows=(await s.execute(text('SELECT to_jsonb(l) FROM leads l WHERE id=ANY(:ids) ORDER BY id'),{'ids':ids})).scalars().all()
                    assignments=(await s.execute(text('SELECT to_jsonb(a) FROM lead_assignments a WHERE lead_id=ANY(:ids) ORDER BY id'),{'ids':ids})).scalars().all()
                    return rows,assignments
            before=await stored()
            for reason in ['测'*256,'x'*256]:
                r=await c.post('/api/v1/leads/batch-assign',json={'lead_ids':ids,'owner_id':target,'reason':reason})
                print('overlong HTTP',r.status_code,r.text[:180],flush=True)
                assert await stored()==before
                assert r.status_code==400,(r.status_code,r.text)
            reason='测'*255
            r=await c.post('/api/v1/leads/batch-assign',json={'lead_ids':ids,'owner_id':target,'reason':reason});assert r.status_code==200,r.text
            assert r.json()['data']['assigned']==ids and r.json()['data']['skipped']==[],r.text
            after=await stored();assert all(v['owner_id']==target for v in after[0])
            assert len(after[1])==2 and all(v['reason']==reason for v in after[1]),after[1]
            print('PASS overlong reason rejected before writes; full255 Chinese preserved for both assigned leads/history',flush=True)
    finally:await engine.dispose()
asyncio.run(main())
