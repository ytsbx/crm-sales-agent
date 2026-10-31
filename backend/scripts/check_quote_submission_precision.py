"""本机隔离PG/正式提交审批：通知与客户动态金额保持到分。"""
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
from decimal import Decimal
from uuid import uuid4

import httpx
from sqlalchemy import select, text
from app.core.database import SessionLocal, engine
from app.main import app
from app.modules.customer.model import Customer
from app.modules.user.model import User

from datetime import UTC,datetime
from app.modules.quote.model import Quote,QuoteVersion,QuoteItem,QuoteCharge

from app.core.security import hash_password
from app.modules.user.model import Role,Permission,user_roles,role_permissions
from app.modules.pricing.model import PricePermission

async def main():
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,raise_app_exceptions=False),base_url='http://isolated') as c:
            stamp=uuid4().hex[:10]
            async with SessionLocal() as s:
                pids=dict((await s.execute(select(Permission.code,Permission.id))).all())
                role=Role(code='iso-submit-'+stamp,name='隔离提交权限',data_scope='all')
                u=User(username='iso-submit-'+stamp,name='隔离提交人',password_hash=hash_password('iso-submit-password'),status='active')
                s.add_all([role,u]);await s.flush()
                for code in ['quote:view','quote:manage']:await s.execute(role_permissions.insert().values(role_id=role.id,permission_id=pids[code]))
                await s.execute(user_roles.insert().values(user_id=u.id,role_id=role.id))
                s.add(PricePermission(role_id=role.id,minimum_margin=Decimal('0.15'),can_approve=False,status='active'))
                customer=Customer(name='隔离报价审批金额'+stamp,owner_id=u.id);s.add(customer);await s.commit();uid,cid=u.id,customer.id
            r=await c.post('/api/v1/auth/login',json={'username':u.username,'password':'iso-submit-password'});assert r.status_code==200,r.text
            c.headers['Authorization']='Bearer '+r.json()['data']['access_token']
            for quantity,price,expected in [('100','999999999999.9999','99999999999999.99'),('1','1234.56','1234.56'),('1','0.01','0.01')]:
                async with SessionLocal() as s:
                    q=Quote(quote_no='ISO-SUB-'+uuid4().hex[:20],customer_id=cid,owner_id=uid,status='draft')
                    s.add(q);await s.flush()
                    v=QuoteVersion(quote_id=q.id,version_no=1,approval_status='not_submitted',created_at=datetime.now(UTC),currency='CNY',subtotal_amount=Decimal(expected),total_amount=Decimal(expected))
                    s.add(v);await s.flush();q.current_version_id=v.id
                    s.add(QuoteItem(quote_version_id=v.id,sku_name_snapshot='隔离完整成本零毛利',quantity=Decimal(quantity),quoted_price=Decimal(price),cost_snapshot=Decimal(price),cost_present_snapshot=True,cost_complete_snapshot=True))
                    s.add(QuoteCharge(quote_version_id=v.id,charge_type='logistics',amount=Decimal('0'),logistics_confirmed_at=datetime.now(UTC)))
                    await s.commit();vid,qid,qno=v.id,q.id,q.quote_no
                r=await c.post(f'/api/v1/quote-versions/{vid}/submit-approval',json={'reason':'隔离精度验证'})
                assert r.status_code==200,r.text
                async with SessionLocal() as s:
                    version=await s.get(QuoteVersion,vid)
                    assert version.total_amount==Decimal(expected) and version.approval_status=='pending',(version.total_amount,version.approval_status,r.text)
                    messages=(await s.execute(text("SELECT content FROM notifications WHERE business_type='quote' AND business_id=:q AND title=:t ORDER BY id"),{'q':qid,'t':f'待审批报价 {qno}'})).scalars().all()
                    events=(await s.execute(text('SELECT content FROM followups WHERE quote_id=:q ORDER BY id'),{'q':qid})).scalars().all()
                    print('SQL quote',str(version.total_amount),'approval messages',messages,'progress',events,flush=True)
                    assert messages and all(m==f'V1 报价 ¥{Decimal(expected):,.2f}，隔离精度验证' for m in messages),messages
                    assert events==[f'【系统】{qno} V1 提交审批，金额 ¥{Decimal(expected):,.2f}'],events
                    instance_count=(await s.execute(text("SELECT count(*) FROM approval_instances WHERE business_type='quote_version' AND business_id=:v"),{'v':vid})).scalar_one()
                    assert instance_count==1,instance_count
                r=await c.post(f'/api/v1/quote-versions/{vid}/submit-approval',json={'reason':'隔离精度验证'})
                assert r.status_code==400 and '已提交或已通过' in r.json()['message'],r.text
                async with SessionLocal() as s:
                    assert (await s.execute(text("SELECT content FROM notifications WHERE business_type='quote' AND business_id=:q AND title=:t ORDER BY id"),{'q':qid,'t':f'待审批报价 {qno}'})).scalars().all()==messages
                    assert (await s.execute(text('SELECT content FROM followups WHERE quote_id=:q ORDER BY id'),{'q':qid})).scalars().all()==events
                    assert (await s.execute(text("SELECT count(*) FROM approval_instances WHERE business_type='quote_version' AND business_id=:v"),{'v':vid})).scalar_one()==1
                print('PASS quote submission exact approval notices/customer progress and repeat400 unchanged',expected,flush=True)
    finally:await engine.dispose()
asyncio.run(main())
