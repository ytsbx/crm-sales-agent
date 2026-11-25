"""本机隔离PG/正式API：来源创建订单草稿/打样的精确数量、单价及防重。"""
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
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import select, text
from app.core.database import SessionLocal, engine
from app.main import app
from app.modules.customer.model import Customer
from app.modules.inquiry.model import CustomInquiry
from app.modules.order.model import OrderDraftItem
from app.modules.quote.model import Quote, QuoteItem, QuoteVersion
from app.modules.sample.model import SampleItem
from app.modules.user.model import User


async def main():
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url='http://isolated') as client:
            r = await client.post('/api/v1/auth/login', json={'username': 'admin', 'password': 'admin123'})
            assert r.status_code == 200, r.text
            client.headers['Authorization'] = 'Bearer ' + r.json()['data']['access_token']
            async with SessionLocal() as session:
                uid = (await session.execute(select(User.id).where(User.username == 'admin'))).scalar_one()
                customer = Customer(name='隔离来源精度' + uuid4().hex[:12], owner_id=uid)
                session.add(customer)
                await session.flush()
                quote = Quote(quote_no='ISO-PREC-' + uuid4().hex[:20], customer_id=customer.id, owner_id=uid, status='draft')
                session.add(quote)
                await session.flush()
                version = QuoteVersion(created_at=datetime.now(UTC), quote_id=quote.id, version_no=1, currency='CNY', approval_status='not_submitted')
                session.add(version)
                await session.flush()
                quote.current_version_id = version.id
                item = QuoteItem(quote_version_id=version.id, sku_name_snapshot='隔离来源精确明细', quantity=Decimal('9999999999999.999'), quoted_price=Decimal('0.0001'), cost_snapshot=Decimal('0.0001'), cost_present_snapshot=True, cost_complete_snapshot=True)
                session.add(item)
                await session.commit()
                vid, iid = version.id, item.id
            for mode in ('order', 'sample'):
                path = '/api/v1/order-drafts' if mode == 'order' else '/api/v1/samples/from-source'
                preview = '/api/v1/order-drafts/source' if mode == 'order' else '/api/v1/samples/source'
                r = await client.get(preview, params={'quote_version_id': vid})
                assert r.status_code == 200, r.text
                assert r.json()['data']['items'][0]['original_quantity'] == '9999999999999.999'
                if mode == 'order':
                    assert Decimal(r.json()['data']['items'][0]['unit_price']) == Decimal('0.0001')
                cases = [('9999999999999.999', '0.0001'), ('0.001', '999999999999.9999'), ('1.250', '123.4567'), ('1', None), ('1', '0')] if mode == 'order' else [('1', None), ('9999999999999.999', None), ('1.250', None), ('0.001', None)]
                captured = json.loads(Path(sys.argv[1]).read_text())[mode] if len(sys.argv) > 1 else None
                bodies = captured if captured is not None else [{'request_key': str(uuid4()), 'items': [{'quantity': qty, **({'unit_price': price} if mode == 'order' else {})}]} for qty, price in cases]
                key_map = {}
                for original_body in bodies:
                    line = {**original_body['items'][0], 'source_item_id': iid}
                    body = {'quote_version_id': vid, 'request_key': key_map.setdefault(original_body['request_key'], str(uuid4())), 'items': [line]}
                    if mode == 'sample':
                        body['use_dingtalk'] = False  # 外部审批不在此隔离验证范围。
                    r = await client.post(path, json=body)
                    assert r.status_code == 200, r.text
                    result = r.json()['data']
                    rid = result['id']
                    async with SessionLocal() as session:
                        if mode == 'order':
                            stored = (await session.execute(select(OrderDraftItem).where(OrderDraftItem.draft_id == rid))).scalar_one()
                            assert stored.unit_price == (Decimal(line['unit_price']) if line['unit_price'] is not None else None)
                            assert Decimal(result['items'][0]['quantity_exact']) == Decimal(line['quantity'])
                            audit = (await session.execute(text("select after_data from audit_logs where business_type='order_draft' and business_id=:id and action='create'"), {'id': rid})).scalar_one()
                            assert Decimal(audit['items'][0]['quantity_exact']) == Decimal(line['quantity'])
                            assert Decimal(stored.source_snapshot['original_quantity']) == Decimal('9999999999999.999')
                        else:
                            stored = (await session.execute(select(SampleItem).where(SampleItem.sample_request_id == rid))).scalar_one()
                            assert stored.original_quantity == Decimal('9999999999999.999')
                        assert stored.quantity == Decimal(line['quantity']), stored.quantity
                        source = await session.get(QuoteItem, iid)
                        assert source.quantity == Decimal('9999999999999.999') and source.quoted_price == Decimal('0.0001')
                    again = await client.post(path, json=body)
                    assert again.status_code == 200 and again.json()['data']['id'] == rid, again.text
                    async def snapshot():
                        table, items_table, fk = ('order_drafts', 'order_draft_items', 'draft_id') if mode == 'order' else ('sample_requests', 'sample_items', 'sample_request_id')
                        async with SessionLocal() as session:
                            return ((await session.execute(text(f'select to_jsonb(d) from {table} d where id=:id'), {'id': rid})).scalar_one(), (await session.execute(text(f'select to_jsonb(i) from {items_table} i where {fk}=:id order by id'), {'id': rid})).scalars().all())
                    before = await snapshot()
                    conflict = {**body, 'items': [{**line, 'quantity': '2'}]}
                    r = await client.post(path, json=conflict)
                    assert r.status_code == 409 and await snapshot() == before, r.text
                    fields = [('quantity', '1.0001'), ('quantity', '0'), ('quantity', 'abc')]
                    if mode == 'order':
                        fields.append(('unit_price', '1.00001'))
                    for field, invalid in fields:
                        bad = {**body, 'request_key': str(uuid4()), 'items': [{**line, field: invalid}]}
                        r = await client.post(path, json=bad)
                        assert r.status_code == 400 and await snapshot() == before, r.text
                    print('PASS source', mode, 'exact SQL/source retained/replay/conflict/invalid precision', line['quantity'], line.get('unit_price'), flush=True)
            # 询价来源允许尚未确定数量；不把它擅自改成0，订单需手填、打样仍用1。
            for original_qty in (None, Decimal('9999999999999.999')):
                async with SessionLocal() as session:
                    inquiry = CustomInquiry(title='隔离询价精度', inquiry_no='ISO-INQ-' + uuid4().hex[:20], customer_id=customer.id, quantity=original_qty, created_by=uid)
                    session.add(inquiry)
                    await session.commit()
                    inquiry_id = inquiry.id
                for mode in ('order', 'sample'):
                    preview = '/api/v1/order-drafts/source' if mode == 'order' else '/api/v1/samples/source'
                    path = '/api/v1/order-drafts' if mode == 'order' else '/api/v1/samples/from-source'
                    r = await client.get(preview, params={'inquiry_id': inquiry_id})
                    assert r.status_code == 200, r.text
                    source_line = r.json()['data']['items'][0]
                    assert source_line['original_quantity'] == (str(original_qty) if original_qty is not None else None)
                    if mode == 'order':
                        assert source_line['unit_price'] is None
                    qty = str(original_qty) if original_qty is not None else ('0.001' if mode == 'order' else '1')
                    body = {'inquiry_id': inquiry_id, 'request_key': str(uuid4()), 'items': [{'source_item_id': inquiry_id, 'quantity': qty, **({'unit_price': None} if mode == 'order' else {})}]}
                    if mode == 'sample':
                        body['use_dingtalk'] = False
                    r = await client.post(path, json=body)
                    assert r.status_code == 200, r.text
                    rid = r.json()['data']['id']
                    async with SessionLocal() as session:
                        if mode == 'order':
                            stored = (await session.execute(select(OrderDraftItem).where(OrderDraftItem.draft_id == rid))).scalar_one()
                            assert stored.unit_price is None
                            assert stored.source_snapshot['original_quantity'] == source_line['original_quantity']
                        else:
                            stored = (await session.execute(select(SampleItem).where(SampleItem.sample_request_id == rid))).scalar_one()
                            assert stored.original_quantity == original_qty
                        assert stored.quantity == Decimal(qty)
                        original = await session.get(CustomInquiry, inquiry_id)
                        assert original.quantity == original_qty
                    print('PASS inquiry source', mode, 'exact original or unknown preserved; new quantity', qty, flush=True)
    finally:
        await engine.dispose()


if __name__ == '__main__':
    asyncio.run(main())
