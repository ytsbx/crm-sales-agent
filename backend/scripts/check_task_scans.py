"""文档 §2.3/§3.2/§3.4：扫描事实、去重、三个时钟及正式报价边界。"""
import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import os
from unittest.mock import patch
from urllib.parse import urlparse
from uuid import uuid4

from sqlalchemy import String, delete, select
from _test_support import require_isolated_db

require_isolated_db()

from app.core.audit import AuditLog
from app.core.config import settings
from app.core.database import SessionLocal
from app.modules.customer.model import Customer
from app.modules.customer.stage import stage_counts_map
from app.modules.followup.model import FollowUp
from app.modules.notification.model import Notification
from app.modules.order.model import SalesOrder
from app.modules.payment.model import ReceivablePlan
from app.modules.quote.model import Quote, QuoteVersion
from app.modules.quote.service import notify_expired_quotes
from app.modules.settings.model import TaskRule, PublicPoolRule
from app.modules.settings.service import run_auto_tasks, notify_due_followups
from app.modules.task.model import Task
from app.modules.user.model import User


async def main():
    import app.main
    from app.core.scheduler import run_auto_tasks_job
    from app.modules.wecom import client as wecom_client
    _ = app.main
    db = urlparse(settings.database_url)
    assert db.hostname in {'127.0.0.1', 'localhost', '::1'}
    assert 'test' in db.path.lower() or os.getenv('CI') == 'true'
    assert settings.dingtalk_push_off and settings.wecom_push_off
    assert not settings.scheduler_enabled and not settings.wecom_transfer_enabled
    prefix = 'CHKSCAN' + uuid4().hex[:8]
    now = datetime.now(UTC)
    old = now - timedelta(days=30)
    cids, qids, oids, rule_ids = [], [], [], []
    original_rules = []
    deadline_audit_id = None
    pool_rule_id = None
    from scripts.check_review_regressions import BASE, call, login
    assert urlparse(BASE).hostname in {'127.0.0.1', 'localhost', '::1'}
    token = login('admin', 'admin123')

    def patch_rule(path, body, expected=200):
        status, result = call('PATCH', path, token=token, body=body)
        assert status == expected, (status, result)
        return result.get('data')

    def no_external_http(*args, **kwargs):
        raise AssertionError('扫描测试禁止真实外部 HTTP')

    async def scan():
        async with SessionLocal() as s:
            return await run_auto_tasks(s, None, source='CHECK')

    async def deadlines(function):
        async with SessionLocal() as s:
            count = await function(s)
            await s.commit()
            return count

    with patch.object(wecom_client.httpx, 'AsyncClient', no_external_http):
        try:
            async with SessionLocal() as s:
                owner_id = (await s.execute(select(User.id).where(User.username == 'admin'))).scalar_one()
                original_rules = [(r.id, r.status) for r in (await s.execute(select(TaskRule))).scalars()]
                for rule_id, _ in original_rules:
                    (await s.get(TaskRule, rule_id)).status = 'disabled'
                customers = {}
                for label in ['quotes', 'communicated', 'fresh', 'ordered', 'deleted', 'silent', 'future', 'progress', 'expiry', 'stage', 'cancelled']:
                    c = Customer(name=prefix+label, owner_id=owner_id, pool_status='private', level='Z',
                                 last_followup_at=old, created_at=old-timedelta(days=30))
                    if label == 'progress':
                        c.last_progress_at = now
                    s.add(c); await s.flush(); cids.append(c.id); customers[label] = c
                quotes = {}
                for label, customer_label, status, expiry in [
                    ('await1', 'quotes', 'sent', False), ('await2', 'quotes', 'sent', False),
                    ('contact', 'communicated', 'sent', False), ('fresh', 'fresh', 'sent', False),
                    ('ordered', 'ordered', 'sent', False), ('deleted', 'deleted', 'sent', False),
                    ('expire', 'expiry', 'sent', True), ('draft', 'stage', 'draft', True),
                    ('approved', 'stage', 'approved', True), ('unsent-expired', 'stage', 'expired', True),
                ]:
                    q = Quote(quote_no=prefix+label, customer_id=customers[customer_label].id, owner_id=owner_id,
                              status=status, valid_until=(old if expiry else now+timedelta(days=30)).date())
                    if label == 'deleted': q.deleted_at = now
                    s.add(q); await s.flush(); qids.append(q.id); quotes[label] = q
                    if status == 'sent':
                        v = QuoteVersion(quote_id=q.id, version_no=1, created_at=old, sent_at=old)
                        s.add(v); await s.flush(); q.current_version_id = v.id
                fresh = QuoteVersion(quote_id=quotes['fresh'].id, version_no=2, created_at=now, sent_at=now)
                s.add(fresh); await s.flush(); quotes['fresh'].current_version_id = fresh.id
                # 系统进展及发送前联系不冒充发送后的实际沟通。
                s.add_all([
                    FollowUp(customer_id=customers['quotes'].id, quote_id=quotes['await1'].id,
                             owner_id=owner_id, followup_type='系统', content=prefix+'内部操作', created_at=now),
                    FollowUp(customer_id=customers['quotes'].id, quote_id=quotes['await1'].id,
                             owner_id=owner_id, followup_type='电话', content=prefix+'发送前联系', created_at=old-timedelta(days=1)),
                    FollowUp(customer_id=customers['communicated'].id, owner_id=owner_id,
                             followup_type='电话', content=prefix+'已沟通', created_at=now),
                    # 另一张报价的联系不能挡住 await1/await2。
                    FollowUp(customer_id=customers['quotes'].id, quote_id=quotes['ordered'].id,
                             owner_id=owner_id, followup_type='电话', content=prefix+'另一事项', created_at=now),
                ])
                # 既有数据的派生缓存漏写，但任务是真实未来计划。
                future = Task(title=prefix+'未来计划', task_type='followup', customer_id=customers['future'].id,
                              owner_id=owner_id, due_at=now+timedelta(days=5), status='pending', source='manual')
                s.add(future)
                for label, cancelled in [('ordered', False), ('cancelled', True)]:
                    o = SalesOrder(order_no=prefix+label, customer_id=customers[label].id,
                                   quote_id=quotes['ordered'].id if label == 'ordered' else None,
                                   owner_id=owner_id, status='cancelled' if cancelled else 'pending')
                    s.add(o); await s.flush(); oids.append(o.id)
                    if label == 'ordered':
                        # 单已存在的报价不催报价；真实逾期应收仍须派催收。
                        pass
                    s.add(ReceivablePlan(order_id=o.id, plan_name=prefix+'逾期', due_date=old.date(),
                                         amount=Decimal('100'), status='overdue', created_at=now))
                for trigger, config in [('quote_no_followup', {'days': 3}),
                                        ('customer_silent', {'days': 14, 'levels': ['Z']}),
                                        ('receivable_due', {'days': 7}),
                                        ('customer_silent', {'days': 'bad', 'levels': ['Z']})]:
                    # 冷落规则仅作用到三组专门的客户：其余客户最近业务进展有效。
                    if trigger == 'customer_silent' and config['days'] == 14:
                        for label, c in customers.items():
                            if label not in ['silent', 'future']:
                                c.last_progress_at = now
                    rule = TaskRule(code=prefix+str(len(rule_ids)), name=prefix+trigger,
                                    trigger_type=trigger, trigger_config=config,
                                    action_config={'title': prefix+trigger}, status='active')
                    s.add(rule); await s.flush(); rule_ids.append(rule.id)
                pool_rule = PublicPoolRule(level='Z', days=90, enabled=False, remark=prefix)
                s.add(pool_rule); await s.flush(); pool_rule_id = pool_rule.id
                await s.commit()
            # 与配置页完全相同的部分更新，不要求重发完整规则。
            pool = patch_rule(f'/public-pool/rules/{pool_rule_id}', {'days': 91})
            assert pool['days'] == 91 and pool['level'] == 'Z' and pool['enabled'] is False
            pool = patch_rule(f'/public-pool/rules/{pool_rule_id}', {'enabled': True})
            assert pool['enabled'] is True and pool['days'] == 91 and pool['remark'] == prefix
            patch_rule(f'/public-pool/rules/{pool_rule_id}', {'days': 0}, expected=400)
            patch_rule(f'/public-pool/rules/{pool_rule_id}', {'enabled': None}, expected=400)
            rule = patch_rule(f'/task-rules/{rule_ids[0]}', {'trigger_config': {'days': 3}})
            assert rule['code'] == prefix+'0' and rule['trigger_type'] == 'quote_no_followup'
            assert rule['name'] == prefix+'quote_no_followup' and rule['status'] == 'active'
            patch_rule(f'/task-rules/{rule_ids[0]}', {'name': None}, expected=400)
            # 手动与定时扫描同时执行也只创建一份；坏规则不挡住其余规则。
            results = await asyncio.gather(scan(), scan())
            assert sum(r['created_count'] for r in results) == 5, results
            # await1、await2、到期未成单 expire，加冷落、逾期应收，共五项。
            assert all(r['failed_rule_count'] == 1 for r in results)
            assert all(any(row['customer_id'] == customers['future'].id for row in r['agreed_skipped']) for r in results)
            assert (await scan())['created_count'] == 0
            async with SessionLocal() as s:
                tasks = list((await s.execute(select(Task).where(Task.source_rule_id.in_(rule_ids)))).scalars())
                quote_ids = {t.quote_id for t in tasks if t.quote_id}
                assert quote_ids == {quotes['await1'].id, quotes['await2'].id, quotes['expire'].id}, quote_ids
                assert len([t for t in tasks if t.task_type == 'payment']) == 1
                assert not [t for t in tasks if t.customer_id == customers['cancelled'].id]
                assert (await s.get(Customer, customers['quotes'].id)).next_followup_at is not None
                assert (await s.get(Customer, customers['future'].id)).next_followup_at == future.due_at
                assert (await s.get(Customer, customers['silent'].id)).last_followup_at == old
                audit = (await s.execute(select(AuditLog).where(AuditLog.action == 'run_auto_tasks')
                                        .order_by(AuditLog.id.desc()).limit(1))).scalar_one()
                assert audit.after_data['failed_rule_count'] == 1 and audit.after_data['agreed_skipped_count'] == 1
                # 未发送草稿/内部审批/手工标失效均不计对客阶段。
                assert (await stage_counts_map(s, [customers['stage'].id]))[customers['stage'].id][2] == 0
                quotes['approved'] = await s.get(Quote, quotes['approved'].id)
                version = QuoteVersion(quote_id=quotes['approved'].id, version_no=1, created_at=old, sent_at=old)
                s.add(version); await s.flush()
                # 正式发送事实保留：后续起草新版本不会抹去已有对客过程。
                quotes['approved'].status = 'draft'
                assert (await stage_counts_map(s, [customers['stage'].id]))[customers['stage'].id][2] == 1
                await s.rollback()
            # 到期通知根据任务事实扫描，旧的空缓存不使任务漏扫；并发和重跑去重。
            async with SessionLocal() as s:
                customer = await s.get(Customer, customers['silent'].id)
                customer.next_followup_at = None
                await s.commit()
            due_counts = await asyncio.gather(deadlines(notify_due_followups), deadlines(notify_due_followups))
            assert sum(due_counts) >= 4, due_counts  # 三张报价与一条冷落跟进
            async with SessionLocal() as s:
                notes = list((await s.execute(select(Notification).where(Notification.content.contains(prefix), Notification.business_type == 'task'))).scalars())
                assert len(notes) == 4, len(notes)
            assert await deadlines(notify_due_followups) == 0
            # 到期报价待办只针对已对客事实；并发不重复，创建后同步时钟。
            expiry_counts = await asyncio.gather(deadlines(notify_expired_quotes), deadlines(notify_expired_quotes))
            assert sum(expiry_counts) == 1, expiry_counts
            assert await deadlines(notify_expired_quotes) == 0
            async with SessionLocal() as s:
                expiry_tasks = list((await s.execute(select(Task).where(Task.quote_id == quotes['expire'].id,
                                       Task.source_rule_id.is_(None)))).scalars())
                assert len(expiry_tasks) == 1
                assert (await s.get(Customer, customers['expiry'].id)).next_followup_at is not None
                assert not (await s.execute(select(Task.id).where(Task.customer_id == customers['stage'].id))).all()
            # 关闭通知渠道不应虚报提醒数量；回滚不影响后续真实站内扫描。
            from unittest.mock import AsyncMock
            from app.modules.notification import service as notification_service
            async with SessionLocal() as s:
                with patch.object(notification_service, 'notify', AsyncMock(return_value=None)):
                    assert await notify_due_followups(s) == 0
                await s.rollback()
            # 实际每日任务入口有持久结果，且即使外部推送关闭也能独立完成站内扫描。
            await run_auto_tasks_job()
            async with SessionLocal() as s:
                audit = (await s.execute(select(AuditLog).where(AuditLog.action == 'run_followup_deadlines')
                                        .order_by(AuditLog.id.desc()).limit(1))).scalar_one()
                deadline_audit_id = audit.id
                assert audit.source == 'SCHEDULER' and audit.operator_id is None
                assert set(audit.after_data) == {'expired_quotes_count', 'due_followups_count', 'monthly_expiring_count'}
            print('OK 自动扫描：发送后联系、最新发送、按报价去重、已成单/删除过滤、逾期应收、三个时钟、坏规则隔离、并发、正式阶段与执行留痕')
        finally:
            async with SessionLocal() as s:
                tids = select(Task.id).where(Task.customer_id.in_(cids))
                await s.execute(delete(Notification).where(Notification.business_type == 'task', Notification.business_id.in_(tids)))
                await s.execute(delete(AuditLog).where(AuditLog.after_data.cast(String).contains(prefix)))
                if deadline_audit_id:
                    await s.execute(delete(AuditLog).where(AuditLog.id == deadline_audit_id))
                await s.execute(delete(Task).where(Task.customer_id.in_(cids)))
                await s.execute(delete(FollowUp).where(FollowUp.customer_id.in_(cids)))
                await s.execute(delete(ReceivablePlan).where(ReceivablePlan.order_id.in_(oids)))
                await s.execute(delete(SalesOrder).where(SalesOrder.id.in_(oids)))
                await s.execute(delete(QuoteVersion).where(QuoteVersion.quote_id.in_(qids)))
                await s.execute(delete(Quote).where(Quote.id.in_(qids)))
                await s.execute(delete(Customer).where(Customer.id.in_(cids)))
                await s.execute(delete(TaskRule).where(TaskRule.id.in_(rule_ids)))
                if pool_rule_id:
                    await s.execute(delete(PublicPoolRule).where(PublicPoolRule.id == pool_rule_id))
                for rule_id, status in original_rules:
                    rule = await s.get(TaskRule, rule_id)
                    if rule: rule.status = status
                await s.commit()


if __name__ == '__main__':
    asyncio.run(main())
