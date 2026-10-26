"""本机一次性PG：提醒与取消同序加锁，不在生产执行，不发送外部消息。"""
import os
from urllib.parse import urlparse

from _test_support import require_isolated_db

_name = require_isolated_db()
_url = urlparse(os.environ['DATABASE_URL'])
if not (_name.startswith(('crm_iso', 'crm_check', 'crm_test')) or _name.endswith('_test')):
    raise SystemExit('拒绝正式/演示库')
if _url.hostname not in ('127.0.0.1', 'localhost') or not _url.scheme.startswith('postgresql'):
    raise SystemExit('只允许本机一次性PostgreSQL')
os.environ.update(WECOM_PUSH_OFF='1', DINGTALK_PUSH_OFF='1', SCHEDULER_ENABLED='0',
                  SCHEDULER_OA_SYNC_ENABLED='0', DINGTALK_OA_STREAM_ENABLED='0')

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, text
from starlette.requests import Request
from app.core.audit import AuditLog
from app.core.database import SessionLocal, engine
from app.core.deps import CurrentUser
from app.core.timebase import today_business
from app.modules.customer.model import Customer
from app.modules.notification.model import Notification
from app.modules.order import milestones, router
from app.modules.order.model import (
    OrderMilestone, OrderShipmentBatch, OrderStatusHistory, SalesOrder,
)
from app.modules.user.model import User

REQUEST = Request({'type': 'http', 'headers': [], 'client': ('127.0.0.1', 1)})


class PauseChildRead:
    """真实子行查询返回前暂停；修复后此时订单和子行锁均已取得。"""
    def __init__(self, session, child, entered, resume):
        self.session, self.child = session, child
        self.entered, self.resume = entered, resume
        self.paused = False

    def __getattr__(self, name):
        return getattr(self.session, name)

    async def execute(self, statement, *args, **kwargs):
        result = await self.session.execute(statement, *args, **kwargs)
        columns = getattr(statement, 'column_descriptions', [])
        if not self.paused and columns and columns[0].get('entity') is self.child:
            self.paused = True
            self.entered.set()
            await asyncio.wait_for(self.resume.wait(), 5)
        return result


async def wait_for_blocker(waiting_pid, blocker_pid):
    async with SessionLocal() as observer:
        for _ in range(100):
            blockers = await observer.scalar(text('SELECT pg_blocking_pids(:pid)'), {'pid': waiting_pid})
            if blocker_pid in blockers:
                return
            await asyncio.sleep(0.01)
    raise AssertionError('取消请求未等待扫描的订单锁')


async def main():
    uid = cid = None
    order_ids = []
    try:
        async with SessionLocal() as session:
            user = User(username='reminder_' + uuid.uuid4().hex[:12], name='隔离提醒用户',
                        password_hash='!', status='active')
            session.add(user)
            await session.flush()
            uid = user.id
            actor = CurrentUser(user, {'order:manage'}, ['salesperson'], 'self')
            customer = Customer(name='隔离提醒客户', owner_id=uid, status='active', pool_status='private')
            session.add(customer)
            await session.flush()
            cid = customer.id
            await session.commit()

        async def fixture(kind, *, both=False):
            async with SessionLocal() as session:
                order = SalesOrder(order_no='REM-' + uuid.uuid4().hex[:12], customer_id=cid,
                                   owner_id=uid, currency='CNY', total_amount=0, status='pending')
                session.add(order)
                await session.flush()
                order_ids.append(order.id)
                if kind == 'milestone' or both:
                    session.add(OrderMilestone(order_id=order.id, node='contract',
                        planned_date=today_business() - timedelta(days=2), created_at=datetime.now(UTC)))
                if kind == 'batch' or both:
                    session.add(OrderShipmentBatch(order_id=order.id, batch_no=1, status='planned',
                        planned_date=today_business() - timedelta(days=2), created_at=datetime.now(UTC)))
                await session.commit()
                return order.id

        async def scan(session, kind):
            fn = milestones.notify_overdue_milestones if kind == 'milestone' else milestones.notify_overdue_batches
            return await fn(session)

        async def close_order(oid):
            async with SessionLocal() as session:
                result = await router.cancel_order(oid, REQUEST, actor, session)
                assert result['code'] == 0

        async def rows_for(oid):
            async with SessionLocal() as session:
                return (await session.scalars(select(Notification).where(
                    Notification.business_type == 'order', Notification.business_id == oid))).all()

        for kind, child in (('milestone', OrderMilestone), ('batch', OrderShipmentBatch)):
            # 已取消的订单不能生成新提醒。
            oid = await fixture(kind)
            await close_order(oid)
            async with SessionLocal() as session:
                assert await scan(session, kind) == 0
                await session.commit()
            assert not await rows_for(oid)

            # 取消已持有父锁尚未提交：扫描跳过，不等待也不发过期提醒。
            oid = await fixture(kind)
            async with SessionLocal() as cancellation, SessionLocal() as worker:
                await cancellation.execute(select(SalesOrder).where(SalesOrder.id == oid).with_for_update())
                assert await asyncio.wait_for(scan(worker, kind), 2) == 0
                await worker.commit()
                assert (await router.cancel_order(oid, REQUEST, actor, cancellation))['code'] == 0
            assert not await rows_for(oid)
            print('PASS ' + kind + ': 已取消或正在取消均跳过')

            # 扫描先取得锁：取消必须确实等待同一订单锁，通知提交早于取消。
            oid = await fixture(kind)
            entered, resume = asyncio.Event(), asyncio.Event()
            async with SessionLocal() as worker, SessionLocal() as cancellation:
                worker_pid = await worker.scalar(text('SELECT pg_backend_pid()'))
                cancel_pid = await cancellation.scalar(text('SELECT pg_backend_pid()'))

                async def first_scan():
                    count = await scan(PauseChildRead(worker, child, entered, resume), kind)
                    await worker.commit()
                    return count

                first = asyncio.create_task(first_scan())
                cancel = None
                try:
                    await asyncio.wait_for(entered.wait(), 5)
                    cancel = asyncio.create_task(router.cancel_order(oid, REQUEST, actor, cancellation))
                    await wait_for_blocker(cancel_pid, worker_pid)
                    assert not cancel.done()
                    resume.set()
                    assert await asyncio.wait_for(first, 5) == 1
                    assert (await asyncio.wait_for(cancel, 5))['code'] == 0
                finally:
                    resume.set()
                    for task in (first, cancel):
                        if task is not None and not task.done():
                            task.cancel()
                    await asyncio.gather(*[t for t in (first, cancel) if t is not None], return_exceptions=True)
            rows = await rows_for(oid)
            async with SessionLocal() as session:
                order = await session.get(SalesOrder, oid)
                assert order.status == 'cancelled' and len(rows) == 1
                assert rows[0].created_at <= order.cancelled_at
                assert await scan(session, kind) == 0
            print('PASS ' + kind + ': 扫描先行，PG证明取消等待父锁且最终无死锁')

        # 两种扫描共享父锁；忙碌订单之外的订单仍可推进。
        oid = await fixture('milestone', both=True)
        other = await fixture('batch')
        entered, resume = asyncio.Event(), asyncio.Event()
        async with SessionLocal() as first_session, SessionLocal() as second_session:
            async def first_scan():
                count = await scan(PauseChildRead(first_session, OrderMilestone, entered, resume), 'milestone')
                await first_session.commit()
                return count
            first = asyncio.create_task(first_scan())
            try:
                await asyncio.wait_for(entered.wait(), 5)
                assert await asyncio.wait_for(scan(second_session, 'milestone'), 2) == 0
                assert await asyncio.wait_for(scan(second_session, 'batch'), 2) == 1
                await second_session.commit()
                assert len(await rows_for(other)) == 1 and not await rows_for(oid)
                resume.set()
                assert await asyncio.wait_for(first, 5) == 1
            finally:
                resume.set()
                if not first.done():
                    first.cancel()
                await asyncio.gather(first, return_exceptions=True)
        async with SessionLocal() as session:
            assert await scan(session, 'batch') == 1
            await session.commit()
            assert await scan(session, 'batch') == 0
            assert await scan(session, 'milestone') == 0
        assert len(await rows_for(oid)) == 2
        print('PASS 交叉扫描不阻塞其它订单；跳过目标下轮可补扫且不重复')
    finally:
        if uid is not None:
            async with SessionLocal() as session:
                await session.execute(delete(Notification).where(Notification.user_id == uid))
                await session.execute(delete(AuditLog).where(AuditLog.operator_id == uid))
                for model in (OrderMilestone, OrderShipmentBatch, OrderStatusHistory):
                    await session.execute(delete(model).where(model.order_id.in_(order_ids)))
                await session.execute(delete(SalesOrder).where(SalesOrder.id.in_(order_ids)))
                if cid is not None:
                    await session.execute(delete(Customer).where(Customer.id == cid))
                await session.execute(delete(User).where(User.id == uid))
                await session.commit()


async def run():
    try:
        await main()
        print('订单提醒锁序真PG验证全部通过')
    finally:
        await engine.dispose()


async def run_in_fresh_database():
    """全局扫描不能借用CI共用库；父进程创建临时空库，迁移后交给子进程。"""
    import subprocess
    import sys
    from pathlib import Path
    import asyncpg
    from sqlalchemy.engine import make_url

    source = make_url(os.environ['DATABASE_URL'])
    database = 'crm_iso_reminder_' + uuid.uuid4().hex[:20]
    admin = await asyncpg.connect(host=source.host, port=source.port or 5432,
                                  user=source.username, password=source.password, database=source.database)
    created = False
    try:
        await admin.execute(f'CREATE DATABASE "{database}"')
        created = True
        env = dict(os.environ, DATABASE_URL=source.set(database=database).render_as_string(hide_password=False),
                   CRM_REMINDER_DB_CHILD='1')
        backend = Path(__file__).resolve().parents[1]
        env['PYTHONPATH'] = str(backend)
        migration = await asyncio.create_subprocess_exec(
            sys.executable, '-m', 'alembic', 'upgrade', 'head', cwd=backend, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        try:
            _, error = await asyncio.wait_for(migration.communicate(), 60)
        finally:
            if migration.returncode is None:
                migration.kill()
                await migration.wait()
        if migration.returncode:
            raise RuntimeError('隔离迁移失败：' + error.decode(errors='replace')[-2000:])
        check = await asyncio.create_subprocess_exec(sys.executable, str(Path(__file__).resolve()), cwd=backend, env=env)
        try:
            code = await asyncio.wait_for(check.wait(), 60)
        finally:
            if check.returncode is None:
                check.kill()
                await check.wait()
        if code:
            raise RuntimeError(f'独立库提醒回归失败，退出码{code}')
    finally:
        if created:
            await admin.execute(f'DROP DATABASE "{database}" WITH (FORCE)')
        await admin.close()


if __name__ == '__main__':
    if os.environ.get('CRM_REMINDER_DB_CHILD') == '1':
        asyncio.run(run())
    else:
        asyncio.run(run_in_fresh_database())
