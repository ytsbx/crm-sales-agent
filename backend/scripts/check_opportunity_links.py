"""本机隔离库回归：商机新建、编辑及换客户复制时的归属校验。"""
import asyncio
import os
from datetime import UTC, datetime
from urllib.parse import urlparse
from uuid import uuid4

from sqlalchemy import delete, select
from _test_support import require_isolated_db

require_isolated_db()

from app.core.audit import AuditLog
from app.core.config import settings
from app.core.database import SessionLocal
from app.modules.customer.model import Contact, Customer
from app.modules.opportunity.model import Opportunity, OpportunityItem, OpportunityStageHistory
from app.modules.user.model import User
from scripts.check_review_regressions import BASE, call, login


async def main():
    import app.main
    _ = app.main
    assert urlparse(BASE).hostname in {'localhost', '127.0.0.1', '::1'}
    db = urlparse(settings.database_url)
    assert db.hostname in {'localhost', '127.0.0.1', '::1'}
    assert 'test' in db.path.lower() or os.getenv('CI', '').lower() == 'true'
    assert settings.wecom_push_off and settings.dingtalk_push_off
    assert not settings.scheduler_enabled and not settings.wecom_transfer_enabled
    marker = 'CHKOL' + uuid4().hex[:8]
    cids = []; contact_ids = []; oids = []
    admin = login('admin', 'admin123')
    salesman = login('zhangsan', '123456')

    def request(method, path, body=None, token=None, expected=200):
        status, result = call(method, path, body=body, token=token or admin)
        assert status == expected, (method, path, status, result)
        return result.get('data')

    try:
        async with SessionLocal() as s:
            users = {u.username: u for u in (await s.execute(select(User))).scalars()}
            for name, owner, deleted in (
                ('self', users['zhangsan'].id, False),
                ('other', users['admin'].id, False),
                ('deleted', users['zhangsan'].id, True),
            ):
                customer = Customer(name=f'{marker}-{name}', owner_id=owner,
                                    deleted_at=datetime.now(UTC) if deleted else None)
                s.add(customer); await s.flush(); cids.append(customer.id)
                contact = Contact(customer_id=customer.id, name=f'{marker}-{name}-联系人')
                s.add(contact); await s.flush(); contact_ids.append(contact.id)
            await s.commit()

        source = request('POST', '/opportunities', {
            'customer_id': cids[0], 'title': marker+'-source', 'primary_contact_id': contact_ids[0],
        }, token=salesman)
        oids.append(source['id'])
        # 同客户复制保留联系人，跨客户复制清空原联系人，不修改原商机。
        same = request('POST', f"/opportunities/{source['id']}/clone", {}, token=salesman)
        oids.append(same['id'])
        assert same['customer_id'] == cids[0] and same['primary_contact_id'] == contact_ids[0]
        other = request('POST', f"/opportunities/{source['id']}/clone", {'customer_id': cids[1]})
        oids.append(other['id'])
        assert other['customer_id'] == cids[1] and other['primary_contact_id'] is None
        assert request('GET', f"/opportunities/{source['id']}")['primary_contact_id'] == contact_ids[0]
        request('POST', f"/opportunities/{source['id']}/clone", {'customer_id': cids[1]}, token=salesman, expected=403)
        request('POST', f"/opportunities/{source['id']}/clone", {'customer_id': cids[2]}, expected=404)
        request('POST', f"/opportunities/{source['id']}/clone", {'customer_id': 9223372036854775000}, expected=404)

        # 同样的跨客户引用在新建/编辑入口也拒绝，失败不写脏关联。
        request('POST', '/opportunities', {'customer_id': cids[1], 'title': marker+'-denied'}, token=salesman, expected=403)
        request('POST', '/opportunities', {'customer_id': cids[0], 'title': marker+'-wrong-contact',
                                         'primary_contact_id': contact_ids[1]}, expected=422)
        request('PATCH', f"/opportunities/{source['id']}", {'primary_contact_id': contact_ids[1]}, expected=422)
        assert request('GET', f"/opportunities/{source['id']}")['primary_contact_id'] == contact_ids[0]
        request('PATCH', f"/opportunities/{source['id']}", {'primary_contact_id': None})
        assert request('GET', f"/opportunities/{source['id']}")['primary_contact_id'] is None
        async with SessionLocal() as s:
            contact = await s.get(Contact, contact_ids[0]); contact.deleted_at = datetime.now(UTC)
            await s.commit()
        request('PATCH', f"/opportunities/{source['id']}", {'primary_contact_id': contact_ids[0]}, expected=404)
        request('POST', '/opportunities', {'customer_id': cids[0], 'title': marker+'-deleted-contact',
                                         'primary_contact_id': contact_ids[0]}, expected=404)
        async with SessionLocal() as s:
            rows = list((await s.execute(select(Opportunity.id).where(Opportunity.title.startswith(marker)))).scalars())
            assert set(rows) == set(oids), rows
        print('OK 商机客户范围、联系人归属、新建/编辑守卫、同客户复制保留、换客户复制清空、软删/越权不产生新商机')
    finally:
        if cids:
            async with SessionLocal() as s:
                own_opportunities = select(Opportunity.id).where(Opportunity.customer_id.in_(cids))
                for model, condition in (
                    (AuditLog, (AuditLog.business_type == 'opportunity') & AuditLog.business_id.in_(own_opportunities)),
                    (OpportunityItem, OpportunityItem.opportunity_id.in_(own_opportunities)),
                    (OpportunityStageHistory, OpportunityStageHistory.opportunity_id.in_(own_opportunities)),
                    (Opportunity, Opportunity.customer_id.in_(cids)),
                    (Contact, Contact.customer_id.in_(cids)), (Customer, Customer.id.in_(cids)),
                ):
                    await s.execute(delete(model).where(condition))
                await s.commit()


if __name__ == '__main__':
    asyncio.run(main())
