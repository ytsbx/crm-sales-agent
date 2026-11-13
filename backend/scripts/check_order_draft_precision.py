"""本机隔离PG/正式API：订单草稿精确数量/单价及仅修改备注不改原值。"""
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

import asyncio,json
from decimal import Decimal
from uuid import uuid4
from pathlib import Path

import httpx
from sqlalchemy import select,text
from app.main import app
from app.core.database import SessionLocal,engine
from app.modules.customer.model import Customer
from app.modules.order.model import OrderDraft,OrderDraftItem
from app.modules.user.model import User

async def main():
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,raise_app_exceptions=False),base_url='http://isolated') as c:
            r=await c.post('/api/v1/auth/login',json={'username':'admin','password':'admin123'});assert r.status_code==200,r.text
            c.headers['Authorization']='Bearer '+r.json()['data']['access_token']
            async with SessionLocal() as s:
                uid=(await s.execute(select(User.id).where(User.username=='admin'))).scalar_one()
                customer=Customer(name='隔离草稿数量'+uuid4().hex[:12],owner_id=uid);s.add(customer);await s.commit();cid=customer.id
            for qty,price in [('9999999999999.999','0.0001'),('1','999999999999.9999'),('1.250','123.4567'),('1',None),('1','0')]:
                async with SessionLocal() as s:
                    draft=OrderDraft(customer_id=cid,owner_id=uid,status='draft',currency='CNY',request_key=str(uuid4()),request_hash='0'*64,created_by=uid,source_context={'type':'inquiry','id':1,'no':'ISO-DRAFT-PRECISION','version':1})
                    s.add(draft);await s.flush()
                    item=OrderDraftItem(draft_id=draft.id,name='隔离精确数量',source_snapshot={'source_item_id':1,'original_quantity':qty,'specification':None,'remark':None},quantity=Decimal(qty),unit_price=Decimal(price) if price is not None else None)
                    s.add(item);await s.commit();did,iid=draft.id,item.id
                async def snapshot():
                    async with SessionLocal() as s:
                        return ((await s.execute(text('select to_jsonb(d) from order_drafts d where id=:id'),{'id':did})).scalar_one(),(await s.execute(text('select to_jsonb(i) from order_draft_items i where draft_id=:id order by id'),{'id':did})).scalars().all())
                r=await c.get(f'/api/v1/order-drafts/{did}');assert r.status_code==200,r.text
                data=r.json()['data'];row=data['items'][0]
                assert Decimal(row['quantity_exact'])==Decimal(qty)
                assert row['unit_price_exact'] is None if price is None else Decimal(row['unit_price_exact'])==Decimal(price)
                if len(sys.argv)>1 and qty=='9999999999999.999':
                    capture=Path(sys.argv[1]);capture.with_name('detail-green.json').write_text(json.dumps(data))
                body={'revision':data['revision'],'currency':'CNY','delivery_date':None,'payment_terms':'','remark':'只改备注','items':[{'source_item_id':row['source_item_id'],'quantity':row['quantity_exact'],'unit_price':row['unit_price_exact'],'specification':row['specification'],'remark':row['remark']}]}
                if len(sys.argv)>1 and qty=='9999999999999.999' and Path(sys.argv[1]).exists():
                    captured=json.loads(Path(sys.argv[1]).read_text());body['items'][0]['quantity']=captured['items'][0]['quantity'];body['items'][0]['unit_price']=captured['items'][0]['unit_price']
                before=await snapshot()
                bad={**body,'currency':'USD'}
                r=await c.patch(f'/api/v1/order-drafts/{did}',json=bad)
                assert r.status_code==400 and '人民币' in r.text and await snapshot()==before,r.text
                r=await c.patch(f'/api/v1/order-drafts/{did}',json=body);assert r.status_code==200,r.text
                async with SessionLocal() as s:
                    stored=await s.get(OrderDraftItem,iid)
                    assert stored.quantity==Decimal(qty),stored.quantity
                    assert stored.unit_price==(Decimal(price) if price is not None else None),stored.unit_price
                    audit=(await s.execute(text("select after_data from audit_logs where business_type='order_draft' and business_id=:id and action='update' order by id desc limit 1"),{'id':did})).scalar_one()
                    assert Decimal(audit['items'][0]['quantity_exact'])==Decimal(qty)
                final=await snapshot();assert final[0]['revision']==2 and final[0]['remark']=='只改备注'
                r=await c.patch(f'/api/v1/order-drafts/{did}',json=body)
                assert r.status_code==409 and await snapshot()==final,r.text
                # 既有小数位校验保留；无四位数量/五位单价静默舍入。
                for field,value in [('quantity','1.0001'),('unit_price','1.00001')]:
                    bad={**body,'revision':2,'items':[{**body['items'][0],field:value}]}
                    r=await c.patch(f'/api/v1/order-drafts/{did}',json=bad)
                    assert r.status_code==400 and await snapshot()==final,r.text
                print('PASS exact draft GET/note-only save/SQL/audit; null-vs-zero, USD and overprecision rejected, stale409 unchanged',qty,price,flush=True)
    finally:await engine.dispose()
if __name__=='__main__':asyncio.run(main())
