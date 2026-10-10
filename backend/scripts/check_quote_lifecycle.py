"""文档 §2.3/§3.2/§3.4：正式发送、客户结果、重试、竞态、通知及转单。

仅运行于隔离本机测试库；全部客户/报价为虚构，禁止真实外部 HTTP。
"""
import asyncio
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlparse
from uuid import uuid4
from unittest.mock import patch
import httpx

from sqlalchemy import delete, select
from _test_support import require_isolated_db

require_isolated_db()

from app.core.audit import AuditLog
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.deps import CurrentUser
from app.modules.customer.model import Customer
from app.modules.followup.model import FollowUp
from app.modules.notification import service as ns
from app.modules.notification.model import BusinessEvent, Notification
from app.modules.opportunity.model import Opportunity, OpportunityStage, OpportunityStageHistory
from app.modules.order.model import SalesOrder, SalesOrderItem, OrderStatusHistory, OrderMilestone
from app.modules.payment.model import ReceivablePlan
from app.modules.quote import service as qs, lifecycle
from app.modules.quote.model import Quote, QuoteVersion, QuoteSendLog, QuoteCharge
from app.modules.quote.schema import SendRequest
from app.modules.task.model import Task
from app.modules.user.model import User
from scripts.check_review_regressions import BASE, call, login


async def main():
    import app.main
    _ = app.main
    assert urlparse(BASE).hostname in {'127.0.0.1', 'localhost', '::1'}
    db = urlparse(settings.database_url)
    assert db.hostname in {'127.0.0.1', 'localhost', '::1'}
    assert 'test' in db.path.lower() or os.getenv('CI', '').lower() == 'true'
    assert settings.wecom_push_off and settings.dingtalk_push_off
    assert not settings.scheduler_enabled and not settings.wecom_transfer_enabled
    marker = 'CHKQL' + uuid4().hex[:8]
    cids = []; qids = []; vids = []; oids = []; actor = owner = manager = None
    facts = {}
    previous_contact = datetime.now(UTC) - timedelta(days=40)

    def request(method, path, body=None, token=None):
        status, result = call(method, path, body=body, token=token or admin)
        assert status == 200 and result.get('code') == 0, (status, result)
        return result['data']

    async def snapshot(qid):
        async with SessionLocal() as s:
            q = await s.get(Quote, qid)
            versions = list((await s.execute(select(QuoteVersion).where(QuoteVersion.quote_id == qid))).scalars())
            customer = await s.get(Customer, q.customer_id)
            counts = []
            for model, condition in (
                (FollowUp, FollowUp.quote_id == qid),
                (BusinessEvent, (BusinessEvent.business_type == 'quote') & (BusinessEvent.business_id == qid)),
                (QuoteSendLog, QuoteSendLog.quote_version_id.in_([v.id for v in versions])),
                (Notification, (Notification.business_type == 'quote') & (Notification.business_id == qid)),
                (AuditLog, (AuditLog.business_type == 'quote') & (AuditLog.business_id == qid)),
            ):
                counts.append(len((await s.execute(select(model.id).where(condition))).all()))
            return (q.status, q.current_version_id,
                    [(v.id, v.sent_at, v.accepted_at, v.declined_at) for v in versions],
                    customer.last_followup_at, customer.last_progress_at, customer.next_followup_at, counts)

    async def blocked(qid, vid, action, body=None, status=422, token=None):
        # ⚠️ `convert-to-order` 加了"需要确认"那道拦之后，不带 confirm 会被
        # 42206 先拦下 —— 那是**因为错误的原因通过**：本函数要验的是业务原因
        # （未发送/已失效的报价不能转单）。所以这一条带 confirm 进去，
        # 让它走到真正的业务校验。
        suffix = '?confirm=true' if action == 'convert-to-order' else ''
        before = await snapshot(qid)
        actual, result = call(
            'POST', f'/quote-versions/{vid}/{action}{suffix}',
            body=body, token=token or admin,
        )
        assert actual == status, (action, actual, result)
        assert await snapshot(qid) == before, (action, '失败请求改变了业务事实')

    async def parallel(vid, actions, body=None):
        return await asyncio.gather(*(asyncio.to_thread(
            call, 'POST', f'/quote-versions/{vid}/{action}', token=admin, body=body)
            for action in actions))

    def forbidden_http(*args, **kwargs):
        raise AssertionError('本测试不得创建外部 HTTP 客户端')

    with patch.object(httpx, 'AsyncClient', forbidden_http):
        try:
            admin = login('admin', 'admin123')
            finance = login('wangwu', '123456')
            async with SessionLocal() as s:
                users = {u.username: u for u in (await s.execute(select(User))).scalars()}
                actor, owner, manager = users['admin'], users['zhangsan'], users['lisi']
                stage = (await s.execute(select(OpportunityStage).order_by(OpportunityStage.sequence).limit(1))).scalar_one()
                for label in ('accept', 'decline', 'race', 'unsent', 'stale', 'expired', 'win', 'retry'):
                    customer = Customer(name=f'{marker}-{label}', owner_id=owner.id,
                                        last_followup_at=previous_contact)
                    s.add(customer); await s.flush(); cids.append(customer.id)
                    opp = Opportunity(customer_id=customer.id, title=f'{marker}-{label}',
                                      owner_id=owner.id, stage_id=stage.id, status='open')
                    s.add(opp); await s.flush(); oids.append(opp.id)
                    q = Quote(quote_no=f'{marker}-{label}', customer_id=customer.id,
                              opportunity_id=opp.id, owner_id=owner.id, status='approved',
                              valid_until=(datetime.now(UTC) + timedelta(days=-1 if label == 'expired' else 10)).date())
                    s.add(q); await s.flush(); qids.append(q.id)
                    version = QuoteVersion(quote_id=q.id, version_no=1, approval_status='approved',
                                           created_at=datetime.now(UTC), total_amount=100)
                    s.add(version); await s.flush(); vids.append(version.id)
                    # 运费分离（2026-10-09）：正式发送前必须已经确认运费金额。
                    # 本套件验的是状态闸门与并发，不是运费，所以夹具补一条**已确认**的运费，
                    # 让发送能走到它真正要验的那一步（不是放宽闸门）。
                    s.add(QuoteCharge(quote_version_id=version.id, charge_type='logistics',
                                      description='夹具运费', amount=Decimal('10'),
                                      logistics_confirmed_at=datetime.now(UTC)))
                    q.current_version_id = version.id
                    facts[label] = (q.id, version.id, customer.id, opp.id)
                    if label == 'stale':
                        next_v = QuoteVersion(quote_id=q.id, version_no=2, approval_status='approved', created_at=datetime.now(UTC))
                        s.add(next_v); await s.flush(); vids.append(next_v.id); q.current_version_id = next_v.id
                        s.add(QuoteCharge(quote_version_id=next_v.id, charge_type='logistics',
                                          description='夹具运费', amount=Decimal('10'),
                                          logistics_confirmed_at=datetime.now(UTC)))
                await s.commit()

            qid, vid, _, _ = facts['unsent']
            await blocked(qid, vid, 'accept')
            await blocked(qid, vid, 'reject', {})
            await blocked(qid, vid, 'convert-to-order', {})
            before = await snapshot(qid)
            data = request('POST', f'/quote-versions/{vid}/send-email', {'receiver':'虚构客户'})
            assert not data['delivered']
            after = await snapshot(qid)
            assert before[:6] == after[:6] and after[-1][:2] == [0, 0]
            await blocked(qid, vid, 'accept')
            await blocked(qid, vid, 'mark-sent', {}, status=403, token=finance)

            qid, vid, _, _ = facts['stale']
            for action in ('mark-sent', 'accept', 'reject', 'expire', 'submit-approval', 'convert-to-order'):
                await blocked(qid, vid, action, {})
            qid, vid, _, _ = facts['expired']
            await blocked(qid, vid, 'mark-sent', {})
            async with SessionLocal() as s:
                q = await s.get(Quote, qid); q.status = 'sent'
                v = await s.get(QuoteVersion, vid); v.sent_at = datetime.now(UTC) - timedelta(days=2)
                await s.commit()
            await blocked(qid, vid, 'accept')

            qid, vid, cid, _ = facts['accept']
            key = str(uuid4()); body = {'channel':'本地验收', 'receiver':'虚构客户', 'request_key':key}
            result = await parallel(vid, ['mark-sent', 'mark-sent'], body)
            assert all(status == 200 for status, _ in result), result
            first = await snapshot(qid)
            assert first[-1][:4] == [1, 1, 1, 1], first
            request('POST', f'/quote-versions/{vid}/mark-sent', body)
            assert await snapshot(qid) == first
            await blocked(qid, vid, 'mark-sent', {**body, 'receiver':'另一个虚构收件人'})
            request('POST', f'/quote-versions/{vid}/mark-sent', {**body, 'request_key':str(uuid4())})
            assert (await snapshot(qid))[-1][:4] == [2, 2, 2, 2]
            result = await parallel(vid, ['accept', 'accept'])
            assert all(status == 200 for status, _ in result), result
            accepted = await snapshot(qid)
            assert accepted[-1][:4] == [3, 3, 2, 3], accepted
            await blocked(qid, vid, 'reject', {})
            await blocked(qid, vid, 'mark-sent', {**body, 'request_key':str(uuid4())})
            # 旧请求在客户已接受后重放，也不能把状态退回 sent。
            request('POST', f'/quote-versions/{vid}/mark-sent', body)
            assert await snapshot(qid) == accepted
            timeline = request('GET', f'/customers/{cid}/timeline')
            rows = [e for e in timeline if e['kind'] == 'followup']
            assert len(rows) == 3 and all(e['source']['type'] == 'quote' for e in rows), rows
            async with SessionLocal() as s:
                follows = list((await s.execute(select(FollowUp).where(FollowUp.quote_id == qid))).scalars())
                notices = list((await s.execute(select(Notification).where(Notification.business_type == 'quote',
                                                                 Notification.business_id == qid))).scalars())
                assert all(f.owner_id == actor.id and 'V1' in f.content for f in follows)
                assert all(n.user_id == manager.id and '操作者：'+actor.name in n.content for n in notices)
                assert accepted[3] == previous_contact and accepted[4] > previous_contact and accepted[5] is None
            created = request('POST', f'/quotes/{qid}/versions?confirm=true')
            vids.append(created['id'])
            draft = await snapshot(qid)
            request('POST', f'/quote-versions/{vid}/accept')
            request('POST', f'/quote-versions/{vid}/mark-sent', body)
            assert await snapshot(qid) == draft

            qid, vid, _, _ = facts['decline']
            request('POST', f'/quote-versions/{vid}/mark-sent', {})
            result = await parallel(vid, ['reject', 'reject'], {'reason':'交期不符合客户要求'})
            assert all(status == 200 for status, _ in result), result
            declined = await snapshot(qid)
            assert declined[-1][:4] == [2, 2, 1, 2], declined
            await blocked(qid, vid, 'accept')
            await blocked(qid, vid, 'convert-to-order', {})
            request('POST', f'/quote-versions/{vid}/mark-sent', {})
            assert await snapshot(qid) == declined
            async with SessionLocal() as s:
                reason = (await s.execute(select(AuditLog.after_data).where(AuditLog.business_id == qid,
                    AuditLog.business_type == 'quote', AuditLog.action == 'decline'))).scalar_one()
                assert reason['reason'] == '交期不符合客户要求'

            qid, vid, _, _ = facts['race']
            request('POST', f'/quote-versions/{vid}/mark-sent', {})
            result = await parallel(vid, ['accept', 'reject'], {})
            assert sorted(status for status, _ in result) == [200, 422], result
            state = await snapshot(qid)
            assert state[-1][:4] == [2, 2, 1, 2] and bool(state[2][0][2]) != bool(state[2][0][3]), state

            # 同一客户确认入口也写接受事实；重复确认不能改成交版本或重复建单。
            qid, vid, _, oppid = facts['win']
            request('POST', f'/quote-versions/{vid}/mark-sent', {})
            async def win():
                return await asyncio.to_thread(call, 'POST', f'/opportunities/{oppid}/confirm-win',
                                               token=admin, body={'win_quote_version_id':vid})
            result = await asyncio.gather(win(), win())
            assert all(status == 200 for status, _ in result), result
            assert result[0][1]['data']['order_id'] == result[1][1]['data']['order_id'], result
            async with SessionLocal() as s:
                event = list((await s.execute(select(BusinessEvent).where(BusinessEvent.event_key == f'quote:accept:{vid}'))).scalars())
                assert len(event) == 1

            # 通知生成失败保留事实和待办；恢复只补通知。回滚不留下半条事实。
            qid, vid, _, _ = facts['retry']
            viewer = CurrentUser(user=actor, roles=['admin'], permissions=set(), data_scope='all')
            async with SessionLocal() as s:
                v = await qs.get_visible_version(s, viewer, vid, for_update=True)
                q = await qs.get_visible_quote(s, viewer, qid)
                async def fail(*args, **kwargs):
                    raise RuntimeError('模拟主管通知写入失败')
                with patch.object(ns, 'notify', fail):
                    await lifecycle.mark_version_sent(s, quote=q, version=v, operator_id=actor.id, payload=SendRequest())
                await s.commit()
            failed = await snapshot(qid)
            assert failed[-1][:4] == [1, 1, 1, 0], failed
            async with SessionLocal() as s:
                result = await ns.materialize_business_notifications(s)
                await s.commit()
            assert result['processed'] >= 1
            assert (await snapshot(qid))[-1][:4] == [1, 1, 1, 1]
            before = await snapshot(qid)
            async with SessionLocal() as s:
                v = await qs.get_visible_version(s, viewer, vid, for_update=True)
                q = await qs.get_visible_quote(s, viewer, qid)
                await lifecycle.accept_version(s, quote=q, version=v, operator_id=actor.id)
                await s.rollback()
            assert await snapshot(qid) == before
            print('OK 正式报价：发送/客户结果、版本与状态闸门、重试与真实重发、竞态、成交入口、三个时钟、权限、时间线与通知恢复')
        finally:
            if cids:
                async with SessionLocal() as s:
                    orders = select(SalesOrder.id).where(SalesOrder.customer_id.in_(cids))
                    for model, condition in (
                        (Notification, Notification.content.contains(marker)),
                        (BusinessEvent, BusinessEvent.customer_id.in_(cids)),
                        (FollowUp, FollowUp.customer_id.in_(cids)), (Task, Task.customer_id.in_(cids)),
                        (AuditLog, ((AuditLog.business_type == 'quote') & AuditLog.business_id.in_(qids)) |
                         ((AuditLog.business_type == 'opportunity') & AuditLog.business_id.in_(oids)) |
                         ((AuditLog.business_type == 'order') & AuditLog.business_id.in_(orders))),
                        (SalesOrderItem, SalesOrderItem.order_id.in_(orders)),
                        (ReceivablePlan, ReceivablePlan.order_id.in_(orders)),
                        (OrderStatusHistory, OrderStatusHistory.order_id.in_(orders)),
                        (OrderMilestone, OrderMilestone.order_id.in_(orders)),
                        (SalesOrder, SalesOrder.customer_id.in_(cids)),
                        (QuoteSendLog, QuoteSendLog.quote_version_id.in_(vids)),
                        # 运费夹具（本套件新增的物流费用）先删：外键是 NO ACTION，
                        # 不删会让 QuoteVersion 删不掉、夹具残留下来。
                        (QuoteCharge, QuoteCharge.quote_version_id.in_(vids)),
                        (QuoteVersion, QuoteVersion.quote_id.in_(qids)), (Quote, Quote.id.in_(qids)),
                        (OpportunityStageHistory, OpportunityStageHistory.opportunity_id.in_(oids)),
                        (Opportunity, Opportunity.id.in_(oids)), (Customer, Customer.id.in_(cids)),
                    ):
                        await s.execute(delete(model).where(condition))
                    await s.commit()


if __name__ == '__main__':
    asyncio.run(main())
