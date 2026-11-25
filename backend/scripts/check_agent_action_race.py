"""一次性PostgreSQL：AI动作确认、拒绝与取消必须共享唯一终态，不调用模型或外部服务。"""
import asyncio
import uuid
from urllib.parse import urlparse

from _test_support import require_isolated_db

_database_name = require_isolated_db()  # 必须先守隔离库，再导入会创建engine的app模块。
if not (_database_name.startswith(("crm_iso", "crm_check", "crm_test")) or _database_name.endswith("_test")):
    raise SystemExit("本套件不允许隔离库保护的逃生开关放行正式/演示库")
import os

_database_url = urlparse(os.environ['DATABASE_URL'])
if _database_url.hostname not in ('localhost', '127.0.0.1') or not _database_url.scheme.startswith('postgresql'):
    raise SystemExit('本套件只允许本机一次性PostgreSQL，拒绝远端或非PG数据库')

from sqlalchemy import delete, func, select
from starlette.requests import Request
from app.core.audit import AuditLog
from app.core.database import SessionLocal, engine
from app.core.deps import CurrentUser
from app.core.errors import AppError, ErrorCode
from app.modules.agent.model import AgentAction, AgentExecution, AgentMessage, AgentSession
from app.modules.agent.router import cancel_action, confirm_action, reject_action
from app.modules.agent.schema import ActionReject
from app.modules.task.model import Task
from app.modules.user.model import User

REQUEST = Request({'type': 'http', 'headers': [], 'client': ('127.0.0.1', 1)})


class PauseOwnershipRead:
    """在真实归属查询返回前控制交错，不替换SQL、锁或业务工具。"""
    def __init__(self, session, entered, resume):
        self.session, self.entered, self.resume = session, entered, resume

    def __getattr__(self, name):
        return getattr(self.session, name)

    async def get(self, entity, ident, *args, **kwargs):
        row = await self.session.get(entity, ident, *args, **kwargs)
        if entity is AgentSession:
            self.entered.set()
            await asyncio.wait_for(self.resume.wait(), 15)
        return row


class OwnershipBarrier:
    """两请求都已读到原状态，再继续，让旧状态覆盖风险可重复触发。"""
    def __init__(self):
        self.entered = 0
        self.ready = asyncio.Event()

    def wrap(self, session):
        barrier = self

        class Wrapped:
            def __getattr__(self, name):
                return getattr(session, name)

            async def get(self, entity, ident, *args, **kwargs):
                row = await session.get(entity, ident, *args, **kwargs)
                if entity is AgentSession:
                    barrier.entered += 1
                    if barrier.entered == 2:
                        barrier.ready.set()
                    await asyncio.wait_for(barrier.ready.wait(), 15)
                return row

        return Wrapped()


async def invoke(kind, action_id, actor, session):
    try:
        if kind == 'confirm':
            result = await confirm_action(action_id, actor, session)
        elif kind == 'cancel':
            result = await cancel_action(action_id, ActionReject(reason='隔离回归取消'), actor, session)
        else:
            result = await reject_action(action_id, ActionReject(reason='隔离回归取消'), REQUEST, actor, session)
        return result['code']
    except AppError as error:
        await session.rollback()
        return error.code


async def main():
    user_ids, session_ids = [], []

    async def fixture(label):
        async with SessionLocal() as session:
            user = User(username='agent_race_' + uuid.uuid4().hex[:16], name='隔离竞态用户',
                        password_hash='!', status='active')
            session.add(user)
            await session.flush()
            user_ids.append(user.id)
            actor = CurrentUser(user, {'agent:use', 'task:manage'}, ['salesperson'], 'self')
            chat = AgentSession(user_id=user.id, title=label)
            session.add(chat)
            await session.flush()
            session_ids.append(chat.id)
            action = AgentAction(session_id=chat.id, action_type='create_task', tool_name='create_task',
                                 risk_level='L2', title=label, proposed_payload={'title': label},
                                 status='awaiting_confirmation')
            session.add(action)
            await session.flush()
            action_id = action.id
            await session.commit()
            return action_id, actor

    async def assert_state(action_id, actor, status, tasks, executions, decisions):
        async with SessionLocal() as session:
            action = await session.get(AgentAction, action_id)
            assert action.status == status, action.status
            assert await session.scalar(select(func.count()).select_from(Task).where(Task.owner_id == actor.id)) == tasks
            assert await session.scalar(select(func.count()).select_from(AgentExecution).where(
                AgentExecution.action_id == action_id, AgentExecution.status == 'success')) == executions
            actual = (await session.execute(select(AuditLog.action).where(
                AuditLog.operator_id == actor.id,
                AuditLog.action.in_(['confirm_agent_action', 'reject_agent_action', 'cancel']),
            ))).scalars().all()
            assert actual == decisions, actual
            if status == 'executed':
                task = await session.get(Task, action.result['task_id'])
                assert task.owner_id == actor.id and action.confirmed_at is not None
            if status == 'rejected':
                assert action.result == {'reason': '隔离回归取消'}

    try:
        # 拒绝读旧状态，确认先提交；修前二者都成功且最后被写成rejected。
        action_id, actor = await fixture('AI动作确认后迟到取消')
        entered, resume = asyncio.Event(), asyncio.Event()
        async with SessionLocal() as confirmation, SessionLocal() as rejection:
            late_reject = asyncio.create_task(invoke('reject', action_id, actor,
                                                     PauseOwnershipRead(rejection, entered, resume)))
            try:
                await asyncio.wait_for(entered.wait(), 15)
                assert await asyncio.wait_for(invoke('confirm', action_id, actor, confirmation), 15) == 0
                resume.set()
                assert await asyncio.wait_for(late_reject, 15) == ErrorCode.STATUS_NOT_ALLOWED
            finally:
                resume.set()
                if not late_reject.done():
                    late_reject.cancel()
                await asyncio.gather(late_reject, return_exceptions=True)
        await assert_state(action_id, actor, 'executed', 1, 1, ['confirm_agent_action'])
        print('PASS 确认先提交，迟到拒绝不能覆盖已执行动作')

        action_id, actor = await fixture('AI动作拒绝优先')
        async with SessionLocal() as rejection, SessionLocal() as confirmation:
            assert await invoke('reject', action_id, actor, rejection) == 0
            assert await invoke('confirm', action_id, actor, confirmation) == ErrorCode.STATUS_NOT_ALLOWED
        await assert_state(action_id, actor, 'rejected', 0, 0, ['reject_agent_action'])
        print('PASS 拒绝先提交，不再执行任务')

        for kind in ('reject', 'confirm', 'cancel'):
            action_id, actor = await fixture('AI动作并发双击-' + kind)
            barrier = OwnershipBarrier()
            async with SessionLocal() as first, SessionLocal() as second:
                results = await asyncio.wait_for(asyncio.gather(
                    invoke(kind, action_id, actor, barrier.wrap(first)),
                    invoke(kind, action_id, actor, barrier.wrap(second)),
                ), 20)
            assert sorted(results) == [0, ErrorCode.STATUS_NOT_ALLOWED], results
            if kind == 'reject':
                await assert_state(action_id, actor, 'rejected', 0, 0, ['reject_agent_action'])
            elif kind == 'cancel':
                await assert_state(action_id, actor, 'cancelled', 0, 0, ['cancel'])
            else:
                await assert_state(action_id, actor, 'executed', 1, 1, ['confirm_agent_action'])
            print('PASS 并发双击只有一次终态转换：' + kind)

        action_id, actor = await fixture('AI动作确认后独立取消接口')
        entered, resume = asyncio.Event(), asyncio.Event()
        async with SessionLocal() as confirmation, SessionLocal() as cancellation:
            late_cancel = asyncio.create_task(invoke('cancel', action_id, actor,
                                                     PauseOwnershipRead(cancellation, entered, resume)))
            try:
                await asyncio.wait_for(entered.wait(), 15)
                assert await asyncio.wait_for(invoke('confirm', action_id, actor, confirmation), 15) == 0
                resume.set()
                assert await asyncio.wait_for(late_cancel, 15) == ErrorCode.STATUS_NOT_ALLOWED
            finally:
                resume.set()
                if not late_cancel.done():
                    late_cancel.cancel()
                await asyncio.gather(late_cancel, return_exceptions=True)
        await assert_state(action_id, actor, 'executed', 1, 1, ['confirm_agent_action'])
        print('PASS 确认先提交，独立取消接口不能覆盖已执行动作')

        action_id, actor = await fixture('AI动作独立取消优先')
        async with SessionLocal() as cancellation, SessionLocal() as confirmation:
            assert await invoke('cancel', action_id, actor, cancellation) == 0
            assert await invoke('confirm', action_id, actor, confirmation) == ErrorCode.STATUS_NOT_ALLOWED
        await assert_state(action_id, actor, 'cancelled', 0, 0, ['cancel'])
        print('PASS 独立取消先提交，不再执行任务')

        action_id, actor = await fixture('AI动作拒绝与取消并发')
        barrier = OwnershipBarrier()
        async with SessionLocal() as rejection, SessionLocal() as cancellation:
            results = await asyncio.wait_for(asyncio.gather(
                invoke('reject', action_id, actor, barrier.wrap(rejection)),
                invoke('cancel', action_id, actor, barrier.wrap(cancellation)),
            ), 20)
        assert sorted(results) == [0, ErrorCode.STATUS_NOT_ALLOWED], results
        if results[0] == 0:
            await assert_state(action_id, actor, 'rejected', 0, 0, ['reject_agent_action'])
        else:
            await assert_state(action_id, actor, 'cancelled', 0, 0, ['cancel'])
        print('PASS 拒绝与独立取消并发只有一个决策')

        action_id, actor = await fixture('AI动作归属检查')
        _, other = await fixture('另一用户会话')
        async with SessionLocal() as session:
            assert await invoke('reject', action_id, other, session) == ErrorCode.NOT_FOUND
        await assert_state(action_id, actor, 'awaiting_confirmation', 0, 0, [])
        async with SessionLocal() as session:
            assert await invoke('cancel', action_id, other, session) == ErrorCode.NOT_FOUND
        await assert_state(action_id, actor, 'awaiting_confirmation', 0, 0, [])
        print('PASS 非本人不能拒绝或独立取消')

        action_id, actor = await fixture('AI动作撤权检查')
        actor.permissions = {'agent:use'}
        async with SessionLocal() as session:
            assert await invoke('confirm', action_id, actor, session) == ErrorCode.FORBIDDEN
        await assert_state(action_id, actor, 'awaiting_confirmation', 0, 0, [])
        print('PASS 确认时工具权限已收回则拒绝')
    finally:
        # 只清理本次明确记录的隔离用户及会话，不按时间或模糊业务前缀删除。
        if user_ids:
            async with SessionLocal() as session:
                await session.execute(delete(AuditLog).where(AuditLog.operator_id.in_(user_ids)))
                await session.execute(delete(Task).where(Task.owner_id.in_(user_ids)))
                await session.execute(delete(AgentExecution).where(AgentExecution.user_id.in_(user_ids)))
                await session.execute(delete(AgentMessage).where(AgentMessage.session_id.in_(session_ids)))
                await session.execute(delete(AgentAction).where(AgentAction.session_id.in_(session_ids)))
                await session.execute(delete(AgentSession).where(AgentSession.id.in_(session_ids)))
                await session.execute(delete(User).where(User.id.in_(user_ids)))
                await session.commit()


async def run():
    try:
        await main()
        print('AI动作确认/拒绝/取消真PG回归全部通过')
    finally:
        await engine.dispose()


if __name__ == '__main__':
    asyncio.run(run())
