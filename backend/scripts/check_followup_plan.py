"""§3.2 跟进计划闭环：隔离库、零外部请求、页面/接口/Agent 共用写入口。"""
import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse
from uuid import uuid4

from sqlalchemy import String, delete, func, select
from _test_support import require_isolated_db

require_isolated_db()

from app.core.audit import AuditLog
from app.core.config import settings
from app.core.database import SessionLocal
from app.modules.agent.model import AgentAction, AgentExecution, AgentMessage, AgentSession
from app.modules.customer.model import Customer
from app.modules.followup.model import FollowUp
from app.modules.notification.model import BusinessEvent, Notification
from app.modules.task.model import Task
from app.modules.user.model import User
from scripts.check_review_regressions import BASE, call, login


async def main():
    import app.main
    _ = app.main
    assert urlparse(BASE).hostname in {'127.0.0.1', 'localhost', '::1'}
    assert urlparse(settings.database_url).hostname in {'127.0.0.1', 'localhost', '::1'}
    assert 'test' in urlparse(settings.database_url).path.lower() or os.getenv('CI') == 'true'
    assert settings.wecom_push_off and settings.dingtalk_push_off
    assert not settings.scheduler_enabled and not settings.wecom_transfer_enabled
    prefix = 'CHKPLAN' + uuid4().hex[:8]
    token = login('zhangsan', '123456')
    customer_ids, session_ids = [], []
    due = (datetime.now(UTC) + timedelta(days=2)).replace(microsecond=0)
    later = due + timedelta(days=1)

    def request(method, path, body=None, expected=200):
        status, result = call(method, path, token=token, body=body)
        assert status == expected, (status, result, path)
        if expected == 200:
            assert result.get('code') == 0, result
        return result.get('data')

    async def state():
        async with SessionLocal() as s:
            return tuple([(await s.execute(select(func.count()).select_from(model).where(condition))).scalar_one()
                         for model, condition in [
                             (FollowUp, FollowUp.customer_id.in_(customer_ids)),
                             (Task, Task.customer_id.in_(customer_ids)),
                             (BusinessEvent, BusinessEvent.customer_id.in_(customer_ids)),
                             (Notification, Notification.content.contains(prefix)),
                             (AuditLog, AuditLog.after_data.cast(String).contains(prefix)),
                         ]])

    async def next_time(customer_id):
        async with SessionLocal() as s:
            return (await s.get(Customer, customer_id)).next_followup_at

    async def agent_action(payload):
        async with SessionLocal() as s:
            a = AgentSession(user_id=sales_id, title=prefix)
            s.add(a); await s.flush(); session_ids.append(a.id)
            action = AgentAction(session_id=a.id, action_type='create_followup', tool_name='create_followup',
                                 risk_level='L2', business_type='customer', title=prefix,
                                 proposed_payload=payload, status='awaiting_confirmation')
            s.add(action); await s.commit()
            return action.id

    try:
        async with SessionLocal() as s:
            users = {u.username: u for u in (await s.execute(select(User))).scalars()}
            sales_id = users['zhangsan'].id
            for owner in [sales_id, users['admin'].id]:
                customer = Customer(name=prefix+str(owner), owner_id=owner)
                s.add(customer); await s.flush(); customer_ids.append(customer.id)
            await s.commit()
        cid = customer_ids[0]
        base = {'customer_id': cid, 'content': prefix+'已沟通', 'next_action': prefix+'回访',
                'task_due_at': due.isoformat(), 'request_key': prefix+'-one'}
        baseline = await state()
        # 模拟业务保存后整笔事务失败：跟进、任务、时钟、通知待办一起回滚。
        from app.core.deps import CurrentUser
        from app.modules.followup.mutations import create_manual_followup
        from app.modules.followup.schema import FollowUpCreate
        async with SessionLocal() as s:
            user = await s.get(User, sales_id)
            actor = CurrentUser(user, {"followup:create"}, ["sales"], "self")
            await create_manual_followup(s, actor, FollowUpCreate(**base))
            await s.rollback()
        assert await state() == baseline
        assert await next_time(cid) is None
        for bad in [
            {'next_action': None}, {'next_action': '  '}, {'task_due_at': None},
            {'task_due_at': due.replace(tzinfo=None).isoformat()},
            {'exemption_reason': 'not-a-reason'}, {'exemption_reason': 'business_closed'},
            {'content': '   '}, {'followup_type': '系统'},
        ]:
            request('POST', '/followups', {**base, **bad}, expected=422 if bad.get('followup_type') == '系统' else 400)
            assert await state() == baseline, bad
        request('POST', '/followups', {**base, 'customer_id': customer_ids[1]}, expected=403)
        assert await state() == baseline
        # 同一次提交并发/重试复用原任务、原事实及通知。
        with ThreadPoolExecutor(max_workers=4) as pool:
            replies = list(pool.map(lambda _: request('POST', '/followups', base), range(4)))
        assert len({r['followup']['id'] for r in replies}) == 1
        assert len({r['task_id'] for r in replies}) == 1
        assert sum(not r['replayed'] for r in replies) == 1
        followup_id, task_id = replies[0]['followup']['id'], replies[0]['task_id']
        saved = await state()
        assert saved[:3] == (1, 1, 1) and saved[3] == 1, saved
        request('POST', '/followups', {**base, 'content': prefix+'不同内容'}, expected=409)
        assert await state() == saved
        assert await next_time(cid) == due
        async with SessionLocal() as s:
            c = await s.get(Customer, cid)
            assert c.last_followup_at and c.last_progress_at is None
            assert (await s.get(Task, task_id)).title == base['next_action']
        # 改计划只修改已有未完任务；失败无任何局部改动。
        for bad in [{'next_action': None}, {'task_due_at': None}, {'exemption_reason': 'business_closed'}]:
            request('PATCH', f'/followups/{followup_id}', bad, expected=422)
            assert await state() == saved
        request('PATCH', f'/followups/{followup_id}', {'task_due_at': later.isoformat(), 'next_action': prefix+'新回访'})
        assert await next_time(cid) == later
        async with SessionLocal() as s:
            assert (await s.get(FollowUp, followup_id)).next_task_id == task_id
            assert (await s.get(Task, task_id)).due_at == later
        request('PATCH', f'/tasks/{task_id}', {'due_at': due.isoformat()})
        assert await next_time(cid) == due
        request('POST', f'/tasks/{task_id}/complete', {})
        assert await next_time(cid) is None
        before_replay = await state()
        assert request('POST', '/followups', base)['task_id'] == task_id
        assert await state() == before_replay and await next_time(cid) is None
        # 已经完成的任务不因提交重放重新打开；真实新计划可以建另一条。
        second = request('POST', '/followups', {**base, 'request_key': prefix+'-two'})
        assert second['task_id'] != task_id
        request('POST', f'/tasks/{second["task_id"]}/cancel')
        assert await next_time(cid) is None
        third = request('POST', '/followups', {**base, 'request_key': prefix+'-three'})
        request('PATCH', f'/followups/{third["followup"]["id"]}',
                {'next_action': None, 'task_due_at': None, 'exemption_reason': 'customer_declined'})
        assert await next_time(cid) is None
        async with SessionLocal() as s:
            assert (await s.get(Task, third['task_id'])).status == 'cancelled'
        for reason in ['customer_declined', 'business_closed', 'waiting_external']:
            exempt = request('POST', '/followups', {'customer_id': cid, 'content': prefix+reason,
                             'exemption_reason': reason, 'request_key': prefix+reason})
            assert exempt['task_id'] is None and exempt['followup']['exemption_reason'] == reason
        # 历史缺计划的数据仍能读取和修改正文，不猜免填原因、不补造任务。
        async with SessionLocal() as s:
            old = FollowUp(customer_id=cid, owner_id=sales_id, content=prefix+'历史', followup_type='电话')
            s.add(old); await s.commit(); old_id = old.id
        assert request('GET', f'/followups/{old_id}')['exemption_reason'] is None
        request('PATCH', f'/followups/{old_id}', {'content': prefix+'历史更正'})
        assert request('GET', f'/followups/{old_id}')['next_task_id'] is None
        # 补历史任务重试复用同一条，即使它已经完成。
        next_payload = {'due_at': due.isoformat()}
        a = request('POST', f'/followups/{old_id}/create-next-task', next_payload)
        b = request('POST', f'/followups/{old_id}/create-next-task', next_payload)
        assert a['task_id'] == b['task_id']
        request('POST', f'/tasks/{a["task_id"]}/complete', {})
        assert request('POST', f'/followups/{old_id}/create-next-task', next_payload)['task_id'] == a['task_id']
        assert await next_time(cid) is None
        # Agent 无计划/越权同样拒绝；成功确认与业务在同事务，重复确认不多建。
        bad_id = await agent_action({'customer_id': cid, 'content': prefix+'AI缺计划'})
        before = await state()
        request('POST', f'/agent/actions/{bad_id}/confirm', expected=422)
        assert (await state())[:4] == before[:4]
        denied_id = await agent_action({**base, 'customer_id': customer_ids[1]})
        before = await state()
        request('POST', f'/agent/actions/{denied_id}/confirm', expected=403)
        assert (await state())[:4] == before[:4]
        ai_id = await agent_action({**base, 'content': prefix+'AI真实跟进'})
        ai = request('POST', f'/agent/actions/{ai_id}/confirm')['result']
        assert ai['task_id']
        before = await state()
        request('POST', f'/agent/actions/{ai_id}/confirm', expected=400)
        assert await state() == before
        async with SessionLocal() as s:
            f = await s.get(FollowUp, ai['followup_id'])
            assert f.request_key == f'agent:{ai_id}'
            assert (await s.get(Task, ai['task_id'])).source == 'agent'
            assert (await s.execute(select(AuditLog.source).where(AuditLog.business_type == 'followup',
                     AuditLog.business_id == f.id, AuditLog.action == 'create'))).scalar_one() == 'AGENT'
            assert (await s.get(AgentAction, ai_id)).status == 'executed'
            assert (await s.execute(select(BusinessEvent.id).where(BusinessEvent.event_key == f'followup:create:{f.id}'))).scalar_one()
        print('OK 跟进计划：必填/免填、无副作用拒绝、并发重试、计划修改、任务延期完成取消、历史兼容及 AI 确认')
    finally:
        async with SessionLocal() as s:
            ids = select(FollowUp.id).where(FollowUp.customer_id.in_(customer_ids))
            task_ids = select(Task.id).where(Task.customer_id.in_(customer_ids))
            await s.execute(delete(AuditLog).where(AuditLog.business_type == 'followup', AuditLog.business_id.in_(ids)))
            await s.execute(delete(AuditLog).where(AuditLog.business_type == 'task', AuditLog.business_id.in_(task_ids)))
            await s.execute(delete(AuditLog).where(AuditLog.after_data.cast(String).contains(prefix)))
            await s.execute(delete(Notification).where(Notification.content.contains(prefix)))
            await s.execute(delete(BusinessEvent).where(BusinessEvent.customer_id.in_(customer_ids)))
            await s.execute(delete(FollowUp).where(FollowUp.customer_id.in_(customer_ids)))
            await s.execute(delete(Task).where(Task.customer_id.in_(customer_ids)))
            await s.execute(delete(Customer).where(Customer.id.in_(customer_ids)))
            for model in [AgentMessage, AgentExecution, AgentAction]:
                await s.execute(delete(model).where(model.session_id.in_(session_ids)))
            await s.execute(delete(AgentSession).where(AgentSession.id.in_(session_ids)))
            await s.commit()


if __name__ == '__main__':
    asyncio.run(main())
