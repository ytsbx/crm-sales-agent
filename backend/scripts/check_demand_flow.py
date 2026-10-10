"""隔离本机库验收：商机承载独立需求，正式发送才自动推进已报价。"""
import asyncio
import os
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

from sqlalchemy import String, delete, select
from _test_support import require_isolated_db

require_isolated_db()

from app.core.audit import AuditLog
from app.core.config import settings
from app.core.database import SessionLocal
from app.modules.customer.model import Customer
from app.modules.followup.model import FollowUp
from app.modules.inquiry.model import CustomInquiry
from app.modules.notification.model import BusinessEvent, Notification
from app.modules.opportunity.model import Opportunity, OpportunityStage, OpportunityStageHistory
from app.modules.order.model import SalesOrder, SalesOrderItem, OrderStatusHistory, OrderMilestone
from app.modules.payment.model import ReceivablePlan
from app.modules.quote.model import Quote, QuoteVersion, QuoteItem, QuoteSendLog, QuoteCharge
from app.modules.sample.model import SampleRequest, SampleItem
from app.modules.task.model import Task
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
    marker = 'CHKDF' + uuid4().hex[:8]
    cids = []; oids = []; qids = []; vids = []; inquiry_ids = []; sample_ids = []
    admin = login('admin', 'admin123')
    salesman = login('zhangsan', '123456')

    def request(method, path, body=None, token=None, expected=200):
        status, result = call(method, path, body=body, token=token or admin)
        assert status == expected, (method, path, status, result)
        return result.get('data')

    async def state(oid):
        async with SessionLocal() as s:
            opp = await s.get(Opportunity, oid)
            history = list((await s.execute(select(OpportunityStageHistory).where(
                OpportunityStageHistory.opportunity_id == oid
            ).order_by(OpportunityStageHistory.id))).scalars())
            return opp.stage_id, opp.status, [(h.from_stage_id, h.to_stage_id, h.operator_id,
                                              h.remark, h.left_at) for h in history]

    async def fixture(label, *, stage_code='new_inquiry', status='open', linked=True, deleted=False):
        async with SessionLocal() as s:
            opp = Opportunity(customer_id=cids[0], title=f'{marker}-{label}', owner_id=admin_id,
                              stage_id=stages[stage_code].id, status=status,
                              deleted_at=datetime.now(UTC) if deleted else None)
            s.add(opp); await s.flush(); oids.append(opp.id)
            s.add(OpportunityStageHistory(opportunity_id=opp.id, to_stage_id=opp.stage_id,
                  operator_id=admin_id, entered_at=datetime.now(UTC)))
            quote = Quote(quote_no=f'{marker}-{label}', customer_id=cids[0],
                          opportunity_id=opp.id if linked else None, owner_id=admin_id,
                          status='approved', valid_until=(datetime.now(UTC)+timedelta(days=10)).date())
            s.add(quote); await s.flush(); qids.append(quote.id)
            version = QuoteVersion(quote_id=quote.id, version_no=1, approval_status='approved',
                                   created_at=datetime.now(UTC), total_amount=100)
            s.add(version); await s.flush(); vids.append(version.id)
            # 当前业务口径：正式发送前必须有已确认的物流费用；0 元也要显式确认。
            s.add(QuoteCharge(
                quote_version_id=version.id, charge_type='logistics',
                description='测试夹具零运费', amount=0,
                logistics_confirmed_at=datetime.now(UTC),
            ))
            quote.current_version_id = version.id
            await s.commit()
            return opp.id, version.id

    try:
        async with SessionLocal() as s:
            users = {u.username: u for u in (await s.execute(select(User))).scalars()}
            admin_id = users['admin'].id
            stages = {row.code: row for row in (await s.execute(select(OpportunityStage))).scalars()}
            for name in ('one', 'two'):
                customer = Customer(name=f'{marker}-{name}', owner_id=admin_id)
                s.add(customer); await s.flush(); cids.append(customer.id)
            await s.commit()

        # 同客户两笔业务：发送只能推进绑定的一条；并发重试只留一条阶段历史。
        oid, vid = await fixture('advance')
        other_oid, _ = await fixture('other-demand')
        baseline = await state(oid)
        request('POST', f'/quote-versions/{vid}/send-email', {'receiver': '虚构客户'})
        assert await state(oid) == baseline
        result = await asyncio.gather(*(asyncio.to_thread(call, 'POST',
            f'/quote-versions/{vid}/mark-sent', token=admin, body={}) for _ in range(2)))
        assert all(status == 200 for status, _ in result), result
        advanced = await state(oid)
        assert advanced[0] == stages['quoted'].id and len(advanced[2]) == 2, advanced
        assert advanced[2][0][-1] is not None and advanced[2][-1][2] == admin_id
        assert 'V1' in advanced[2][-1][3] and '正式发送报价' in advanced[2][-1][3]
        assert (await state(other_oid))[0] == stages['new_inquiry'].id
        request('POST', f'/quote-versions/{vid}/mark-sent', {'request_key': str(uuid4())})
        assert await state(oid) == advanced

        # 后续阶段/已关闭/已删除/未关联不退回、不重开。
        for label, kwargs in (
            ('quoted', {'stage_code': 'quoted'}), ('later', {'stage_code': 'negotiation'}),
            ('won', {'stage_code': 'won', 'status': 'win'}), ('lost', {'status': 'loss'}),
            ('deleted', {'deleted': True}), ('legacy-unlinked', {'linked': False}),
        ):
            oid, vid = await fixture(label, **kwargs)
            before = await state(oid)
            request('POST', f'/quote-versions/{vid}/mark-sent', {})
            assert await state(oid) == before, label

        # 正式发送与手动推进/确认成交竞争：统一商机 → 报价锁顺序，不发生死锁。
        for action in ('change-stage', 'confirm-win'):
            oid, vid = await fixture('race-' + action)
            body = {'stage_code': 'negotiation'} if action == 'change-stage' else {'win_quote_version_id': vid}
            result = await asyncio.gather(
                asyncio.to_thread(call, 'POST', f'/quote-versions/{vid}/mark-sent', token=admin, body={}),
                asyncio.to_thread(call, 'POST', f'/opportunities/{oid}/{action}', token=admin, body=body),
            )
            assert result[0][0] == 200 and result[1][0] in {200, 400, 422}, result
            if result[1][0] != 200:
                assert action == 'confirm-win' and result[1][1]['code'] == 40002, result
            if result[1][0] == 200:
                assert (await state(oid))[0] == stages['negotiation' if action == 'change-stage' else 'won'].id

        # 新需求可以选已有商机；客户带入，不重复建商机，修订链引用同一商机。
        oid, _ = await fixture('inquiry')
        original = request('POST', '/custom-inquiries', {'title': marker+'-inquiry', 'opportunity_id': oid, 'quantity': 20})
        inquiry_ids.append(original['id'])
        assert original['customer_id'] == cids[0] and original['opportunity_id'] == oid
        revised = request('POST', f"/custom-inquiries/{original['id']}/revise", {'revision_note': '修改尺寸'})
        inquiry_ids.append(revised['id'])
        request('POST', '/custom-inquiries', {'title': marker+'-bad', 'customer_id': cids[1], 'opportunity_id': oid}, expected=422)
        request('PATCH', f"/custom-inquiries/{revised['id']}", {'customer_id': cids[1]}, expected=422)
        request('POST', '/custom-inquiries', {'title': marker+'-scope', 'opportunity_id': oid}, token=salesman, expected=403)
        before = await state(oid)
        quote = request('POST', f"/custom-inquiries/{revised['id']}/create-quote", {'unit_cost': 10, 'quoted_price': 20})
        qids.append(quote['quote_id']); vids.append(quote['version_id'])
        assert quote['opportunity_id'] == oid and await state(oid) == before
        # 生成、下载文件不会制造正式发送事实或推进阶段。
        pdf_request = Request(f"{BASE}/quote-versions/{quote['version_id']}/generate-pdf",
                              headers={'Authorization': f'Bearer {admin}'}, method='POST')
        with urlopen(pdf_request, timeout=20) as response:
            assert response.status == 200 and response.read().startswith(b'%PDF')
        assert await state(oid) == before
        async with SessionLocal() as s:
            v = await s.get(QuoteVersion, quote['version_id']); v.approval_status = 'approved'
            q = await s.get(Quote, quote['quote_id']); q.status = 'approved'
            s.add(QuoteCharge(
                quote_version_id=quote['version_id'], charge_type='logistics',
                description='测试夹具零运费', amount=0,
                logistics_confirmed_at=datetime.now(UTC),
            ))
            await s.commit()
        assert await state(oid) == before
        request('POST', f"/quote-versions/{quote['version_id']}/mark-sent", {})
        assert (await state(oid))[0] == stages['quoted'].id
        sample = request('POST', '/samples', {'customer_id': cids[0],
            'items': [{'inquiry_id': revised['id'], 'quantity': 1}]})
        sample_ids.append(sample['id'])
        assert sample['opportunity_id'] == oid
        request('POST', '/samples', {'customer_id': cids[1], 'opportunity_id': oid}, expected=422)
        request('POST', '/samples', {'customer_id': cids[1],
            'items': [{'inquiry_id': revised['id'], 'quantity': 1}]}, expected=422)
        request('POST', '/samples', {'opportunity_id': other_oid,
            'items': [{'inquiry_id': revised['id'], 'quantity': 1}]}, expected=422)
        request('POST', f"/quote-versions/{quote['version_id']}/accept", {})
        order = request('POST', f"/quote-versions/{quote['version_id']}/convert-to-order", {})
        # 列表按商机筛选；同客户其他需求的单据不能混入。
        inquiry_list = request('GET', f'/custom-inquiries?opportunity_id={oid}')['items']
        assert {row['id'] for row in inquiry_list} == set(inquiry_ids)
        assert all(row['opportunity_id'] == oid for row in request('GET', f'/quotes?opportunity_id={oid}')['items'])
        assert {row['id'] for row in request('GET', f'/samples?opportunity_id={oid}')['items']} == set(sample_ids)
        assert {row['id'] for row in request('GET', f'/orders?opportunity_id={oid}')['items']} == {order['order_id']}
        assert request('GET', f'/orders?opportunity_id={other_oid}')['total'] == 0
        assert (await state(oid))[0] == stages['quoted'].id  # 打样/转单没有新增自动规则

        # 兼容旧逻辑只给被引用版本写了商机的历史链，不能另外创建商机。
        async with SessionLocal() as s:
            old_root = await s.get(CustomInquiry, original['id'])
            old_root.opportunity_id = None
            await s.commit()
        legacy_quote = request('POST', f"/custom-inquiries/{revised['id']}/create-quote",
                               {'unit_cost': 10, 'quoted_price': 20})
        qids.append(legacy_quote['quote_id']); vids.append(legacy_quote['version_id'])
        assert legacy_quote['opportunity_id'] == oid
        assert all(row['opportunity_id'] == oid for row in request(
            'GET', f"/custom-inquiries/{original['id']}/history"))

        # 旧需求先修订再报价，自动建商机只能建一次并写到整条修订链。
        root = request('POST', '/custom-inquiries', {'title': marker+'-auto', 'customer_id': cids[0]})
        inquiry_ids.append(root['id'])
        latest = request('POST', f"/custom-inquiries/{root['id']}/revise", {'revision_note': '新版'})
        inquiry_ids.append(latest['id'])
        result = await asyncio.gather(*(asyncio.to_thread(call, 'POST',
            f"/custom-inquiries/{latest['id']}/create-quote", token=admin,
            body={'unit_cost': 10, 'quoted_price': 20}) for _ in range(2)))
        for status, response in result:
            assert status == 200, response
            qids.append(response['data']['quote_id']); vids.append(response['data']['version_id'])
        auto_oids = {response['data']['opportunity_id'] for _, response in result}
        oids.extend(auto_oids)
        assert len(auto_oids) == 1
        history = request('GET', f"/custom-inquiries/{root['id']}/history")
        assert all(row['opportunity_id'] in auto_oids for row in history)
        print('OK 商机需求链、修订链复用、客户/商机范围校验、报价草稿及文件不推进、正式发送推进、重试/并发/不回退、打样/订单关联与筛选')
    finally:
        if cids:
            async with SessionLocal() as s:
                orders = select(SalesOrder.id).where(SalesOrder.customer_id.in_(cids))
                samples = select(SampleRequest.id).where(SampleRequest.customer_id.in_(cids))
                inquiries = select(CustomInquiry.id).where(CustomInquiry.customer_id.in_(cids))
                for model, condition in (
                    (Notification, Notification.content.contains(marker)),
                    (BusinessEvent, BusinessEvent.customer_id.in_(cids)),
                    (FollowUp, FollowUp.customer_id.in_(cids)), (Task, Task.customer_id.in_(cids)),
                    (AuditLog, AuditLog.after_data.cast(String).contains(marker) |
                     ((AuditLog.business_type == 'quote') & AuditLog.business_id.in_(qids)) |
                     ((AuditLog.business_type == 'opportunity') & AuditLog.business_id.in_(oids)) |
                     ((AuditLog.business_type == 'order') & AuditLog.business_id.in_(orders))),
                    (SalesOrderItem, SalesOrderItem.order_id.in_(orders)),
                    (ReceivablePlan, ReceivablePlan.order_id.in_(orders)),
                    (OrderStatusHistory, OrderStatusHistory.order_id.in_(orders)),
                    (OrderMilestone, OrderMilestone.order_id.in_(orders)),
                    (SalesOrder, SalesOrder.customer_id.in_(cids)),
                    (SampleItem, SampleItem.sample_request_id.in_(samples)),
                    (SampleRequest, SampleRequest.customer_id.in_(cids)),
                    (QuoteItem, QuoteItem.quote_version_id.in_(vids)),
                    (QuoteCharge, QuoteCharge.quote_version_id.in_(vids)),
                    (QuoteSendLog, QuoteSendLog.quote_version_id.in_(vids)),
                    (QuoteVersion, QuoteVersion.quote_id.in_(qids)), (Quote, Quote.id.in_(qids)),
                    (CustomInquiry, CustomInquiry.id.in_(inquiries)),
                    (OpportunityStageHistory, OpportunityStageHistory.opportunity_id.in_(oids)),
                    (Opportunity, Opportunity.id.in_(oids)), (Customer, Customer.id.in_(cids)),
                ):
                    await s.execute(delete(model).where(condition))
                await s.commit()


if __name__ == '__main__':
    asyncio.run(main())
