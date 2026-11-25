"""本机隔离PG/正式API：编号配置长度保存校验、真实取号兜底与事务回滚。"""
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
from app.modules.customer.model import Customer
from app.modules.bizdoc.model import BizDoc
from app.modules.contract.model import ContractDocument
from app.modules.inquiry.model import CustomInquiry
from app.modules.quote.model import Quote
from app.modules.order.model import SalesOrder
from app.modules.product.model import Product, Sku
from app.modules.settings.model import NumberingRule
from app.modules.settings.numbering import DEFAULT_RULES, generate_for
from app.modules.user.model import User

CODES = ['quote', 'order', 'contract', 'inquiry', 'sample_doc', 'order_doc', 'quote_doc']
TARGETS = {
    'quote': (Quote, Quote.quote_no), 'order': (SalesOrder, SalesOrder.order_no),
    'contract': (ContractDocument, ContractDocument.doc_no),
    'inquiry': (CustomInquiry, CustomInquiry.inquiry_no),
    **{code: (BizDoc, BizDoc.doc_no) for code in ['sample_doc', 'order_doc', 'quote_doc']},
}
FIELDS = ['code', 'name', 'prefix', 'date_format', 'seq_length', 'reset_period', 'enabled', 'remark']

async def main():
    originals = {}; created = []
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url='http://isolated') as c:
            r = await c.post('/api/v1/auth/login', json={'username':'admin','password':'admin123'})
            assert r.status_code == 200, r.text
            c.headers['Authorization'] = 'Bearer ' + r.json()['data']['access_token']
            async with SessionLocal() as s:
                for code in CODES:
                    row = (await s.execute(select(NumberingRule).where(NumberingRule.code == code))).scalar_one_or_none()
                    if row is None:
                        row = NumberingRule(code=code, enabled=True, **DEFAULT_RULES[code])
                        s.add(row); await s.flush(); created.append(row.id)
                    else:
                        originals[row.id] = {k:getattr(row,k) for k in FIELDS}
                await s.commit()
            async def snapshot(rid):
                async with SessionLocal() as s:
                    row = (await s.execute(text('select to_jsonb(r) from numbering_rules r where id=:id'), {'id':rid})).scalar_one()
                    counters = (await s.execute(text('select to_jsonb(n) from number_sequences n where rule_code=:code order by id'), {'code':row['code']})).scalars().all()
                    return row, counters
            r = await c.get('/api/v1/numbering-rules'); assert r.status_code == 200, r.text
            rules = {row['code']:row for row in r.json()['data']}
            for code in CODES:
                rid = rules[code]['id']; before = await snapshot(rid)
                r = await c.patch(f'/api/v1/numbering-rules/{rid}', json={'prefix':'P'*16,'date_format':'%Y%m%d','seq_length':12})
                assert r.status_code == 422 and '编号过长' in r.text, (code,r.status_code,r.text)
                assert await snapshot(rid) == before
                r = await c.patch(f'/api/v1/numbering-rules/{rid}', json={'prefix':'P'*12,'date_format':'%Y%m%d','seq_length':12})
                assert r.status_code == 200 and len(r.json()['data']['next_preview']) == 32, (code,r.text)
                before = await snapshot(rid)
                model, column = TARGETS[code]
                async with SessionLocal() as s:
                    number = await generate_for(s, code, model=model, column=column)
                    assert len(number) == 32, (code, number)
                    await s.rollback()
                assert await snapshot(rid) == before
                r = await c.patch(f'/api/v1/numbering-rules/{rid}', json={'prefix':'P'*13})
                assert r.status_code == 422 and await snapshot(rid) == before, (code,r.text)
            print('PASS seven target rule updates: reject36/partial33 with unchanged rows, accept32 and real PG generation rollback', flush=True)
            # 暂时挪开本机规则，真实POST验证内置类型创建，finally原样恢复。
            rid = rules['quote_doc']['id']
            async with SessionLocal() as s:
                row = await s.get(NumberingRule,rid); row.code = 'iso_' + uuid4().hex[:20]; await s.commit()
            payload = {'code':'quote_doc','name':'隔离编号长度','prefix':'P'*16,'date_format':'%Y%m%d','seq_length':12}
            r = await c.post('/api/v1/numbering-rules',json=payload)
            assert r.status_code == 422 and '编号过长' in r.text, r.text
            async with SessionLocal() as s:
                assert (await s.execute(select(NumberingRule).where(NumberingRule.code=='quote_doc'))).scalar_one_or_none() is None
            payload['prefix'] = 'P'*12
            r = await c.post('/api/v1/numbering-rules',json=payload)
            assert r.status_code == 200 and len(r.json()['data']['next_preview']) == 32, r.text
            created.append(r.json()['data']['id'])
            print('PASS create rejects overlong combination without row; valid32 creates', flush=True)
            # 模拟已有错误配置：正式建单应明确拒绝，不能写半单或推进计数器。
            async with SessionLocal() as s:
                row = await s.get(NumberingRule,rules['order']['id'])
                row.prefix='P'*16;row.date_format='%Y%m%d';row.seq_length=12;row.enabled=True
                uid = (await s.execute(select(User.id).where(User.username=='admin'))).scalar_one()
                customer=Customer(name='隔离编号验证'+uuid4().hex[:8], owner_id=uid)
                product=Product(name='隔离编号产品'+uuid4().hex[:8], status='active')
                s.add_all([customer,product]);await s.flush()
                sku=Sku(product_id=product.id,sku_code='ISO-NUM-'+uuid4().hex[:15],name='隔离规格',status='active')
                s.add(sku);await s.commit();cid,skuid=customer.id,sku.id
            async def facts():
                async with SessionLocal() as s:
                    count=(await s.execute(select(SalesOrder.id).where(SalesOrder.customer_id==cid))).scalars().all()
                    sequence=(await s.execute(text("select to_jsonb(n) from number_sequences n where rule_code='order' order by id"))).scalars().all()
                    return count,sequence
            before=await facts()
            order={'customer_id':cid,'currency':'CNY','items':[{'sku_id':skuid,'quantity':'1','unit_price':'1.00'}]}
            r=await c.post('/api/v1/orders',json=order)
            assert r.status_code == 422 and '编号过长' in r.text, (r.status_code,r.text)
            assert await facts() == before
            r=await c.patch(f"/api/v1/numbering-rules/{rules['order']['id']}",json={'prefix':'P'*12,'date_format':'%Y%m%d','seq_length':12})
            assert r.status_code == 200, r.text
            r=await c.post('/api/v1/orders',json=order);assert r.status_code == 200,r.text
            assert len(r.json()['data']['order_no']) == 32 and len((await facts())[0]) == 1
            print('PASS legacy bad configuration gets explicit422 with zero order/sequence changes; valid rule creates normally',flush=True)
    finally:
        async with SessionLocal() as s:
            for rid in created:
                row=await s.get(NumberingRule,rid)
                if row is not None:await s.delete(row)
            await s.flush()
            for rid,values in originals.items():
                row=await s.get(NumberingRule,rid)
                for k,v in values.items():setattr(row,k,v)
            await s.commit()
        await engine.dispose()

if __name__ == '__main__':
    asyncio.run(main())
