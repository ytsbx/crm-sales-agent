"""本机一次性PG：成本导入首次无行也需串行，不调用外部服务。"""
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
import csv
from datetime import date
from decimal import Decimal
from io import BytesIO, StringIO
from uuid import uuid4
from sqlalchemy import delete, select, text
from starlette.requests import Request
from starlette.datastructures import UploadFile
from app.core.audit import AuditLog
from app.core.database import SessionLocal, engine
from app.core.deps import CurrentUser
from app.core.errors import AppError, ErrorCode
from app.modules.pricing import io_router, router
from app.modules.pricing.model import ProductCost
from app.modules.pricing.schema import CostCreate
from app.modules.product import service as product_service
from app.modules.product.model import Product, Sku
from app.modules.user.model import User

REQUEST = Request({'type': 'http', 'headers': [], 'client': ('127.0.0.1', 1)})


class PauseQuery:
    def __init__(self, session, entity, entered, resume):
        self.session, self.entity = session, entity
        self.entered, self.resume = entered, resume
        self.once = False

    def __getattr__(self, name):
        return getattr(self.session, name)

    async def execute(self, stmt, *args, **kwargs):
        result = await self.session.execute(stmt, *args, **kwargs)
        columns = getattr(stmt, 'column_descriptions', [])
        if not self.once and columns and columns[0].get('entity') is self.entity:
            self.once = True
            self.entered.set()
            await asyncio.wait_for(self.resume.wait(), 5)
        return result


async def assert_waiting(waiter, blocker, task):
    async with SessionLocal() as observer:
        for _ in range(100):
            assert not task.done(), '后到导入未等待，首次空行没有互斥'
            pids = await observer.scalar(text('SELECT pg_blocking_pids(:pid)'), {'pid': waiter})
            if blocker in pids:
                return
            await asyncio.sleep(0.01)
    raise AssertionError('未观察到预期数据库锁等待')


async def main():
    uid = None
    pids, sids = [], []
    try:
        async with SessionLocal() as session:
            user = User(username='cost_lock_' + uuid4().hex[:12], name='隔离成本用户', password_hash='!', status='active')
            session.add(user)
            await session.flush()
            uid = user.id
            actor = CurrentUser(user, {'price:manage'}, ['salesperson'], 'self')
            await session.commit()

        async def fixture():
            async with SessionLocal() as session:
                product = Product(name='隔离成本' + uuid4().hex[:8], status='active', created_by=uid)
                session.add(product)
                await session.flush()
                pids.append(product.id)
                sku = Sku(product_id=product.id, sku_code='COST-' + uuid4().hex[:12], status='active')
                session.add(sku)
                await session.flush()
                sids.append(sku.id)
                await session.commit()
                return product.id, sku.id, sku.sku_code

        async def invoke(session, entries, *, preview=False, token=None):
            out = StringIO()
            writer = csv.writer(out)
            writer.writerow(['SKU编码', '生效起始日(YYYY-MM-DD)', '采购成本', '币种(默认CNY)'])
            writer.writerows([[code, '2026-10-01', value, 'CNY'] for code, value in entries])
            try:
                result = await io_router.import_costs(REQUEST, UploadFile(BytesIO(out.getvalue().encode('utf-8-sig')), filename='isolated.csv'),
                                                     preview, token, actor, session)
                return result['code'], result['data']
            except AppError as exc:
                await session.rollback()
                return exc.code, None

        async def parallel(first_entries, second_entries, *, first_token=None, second_token=None):
            entered, resume = asyncio.Event(), asyncio.Event()
            async with SessionLocal() as first_session, SessionLocal() as second_session:
                first_pid = await first_session.scalar(text('SELECT pg_backend_pid()'))
                second_pid = await second_session.scalar(text('SELECT pg_backend_pid()'))
                first = asyncio.create_task(invoke(PauseQuery(first_session, ProductCost, entered, resume), first_entries, token=first_token))
                second = None
                try:
                    await asyncio.wait_for(entered.wait(), 5)
                    second = asyncio.create_task(invoke(second_session, second_entries, token=second_token))
                    await assert_waiting(second_pid, first_pid, second)
                    resume.set()
                    return await asyncio.wait_for(asyncio.gather(first, second), 5)
                finally:
                    resume.set()
                    for task in (first, second):
                        if task is not None and not task.done():
                            task.cancel()
                    await asyncio.gather(*[t for t in (first, second) if t is not None], return_exceptions=True)

        async def costs(sku_id):
            async with SessionLocal() as session:
                return (await session.scalars(select(ProductCost).where(ProductCost.sku_id == sku_id))).all()

        _, sid, code = await fixture()
        results = await parallel([(code, 10)], [(code, 10)])
        assert [r[0] for r in results] == [0, 0]
        assert results[0][1]['created_count'] == 1 and results[1][1]['skipped_count'] == 1
        assert len(await costs(sid)) == 1
        print('PASS 首次同文件并发：实际PG等待，新增1/跳过1，只有一条成本')

        _, sid, code = await fixture()
        results = await parallel([(code, 10)], [(code, 20)])
        assert results[0][1]['created_count'] == 1 and results[1][1]['updated_count'] == 1
        rows = await costs(sid)
        assert len(rows) == 1 and rows[0].purchase_cost == Decimal(20)
        print('PASS 不同内容后到重读并按原同日更新规则处理')

        a, b = await fixture(), await fixture()
        results = await parallel([(b[2], 10), (a[2], 10)], [(a[2], 10), (b[2], 10)])
        assert results[0][1]['created_count'] == 2 and results[1][1]['skipped_count'] == 2
        assert len(await costs(a[1])) == len(await costs(b[1])) == 1
        print('PASS 相反文件顺序、多产品先后锁顺序一致，无测试死锁')

        _, sid, code = await fixture()
        async with SessionLocal() as session:
            _, preview = await invoke(session, [(code, 10)], preview=True)
        results = await parallel([(code, 10)], [(code, 10)], first_token=preview['preview_token'], second_token=preview['preview_token'])
        assert [r[0] for r in results] == [0, ErrorCode.VERSION_CONFLICT]
        assert len(await costs(sid)) == 1
        print('PASS 同一新增预览同时确认：后到结论变化409，整批回滚')

        # 手工新增已有产品→SKU锁，成本导入应等待后重读它提交的版本。
        _, sid, code = await fixture()
        async with SessionLocal() as manual, SessionLocal() as importer:
            pair = await product_service.lock_sku_with_product(manual, sid)
            assert pair is not None
            manual_pid = await manual.scalar(text('SELECT pg_backend_pid()'))
            import_pid = await importer.scalar(text('SELECT pg_backend_pid()'))
            task = asyncio.create_task(invoke(importer, [(code, 10)]))
            try:
                await assert_waiting(import_pid, manual_pid, task)
                result = await router.create_cost(sid, CostCreate(purchase_cost=10, currency='CNY', effective_from=date(2026, 10, 1)), REQUEST, actor, manual)
                assert result['code'] == 0
                outcome = await asyncio.wait_for(task, 5)
                assert outcome[0] == 0 and outcome[1]['skipped_count'] == 1
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        assert len(await costs(sid)) == 1
        print('PASS 手工成本新增先持锁：导入等待并重查，不重复新增')

        # 导入已读旧SKU映射后，产品被真实级联软删：锁后不能沿用旧可用状态。
        pid, sid, code = await fixture()
        entered, resume = asyncio.Event(), asyncio.Event()
        async with SessionLocal() as importer, SessionLocal() as deletion:
            task = asyncio.create_task(invoke(PauseQuery(importer, Sku, entered, resume), [(code, 10)]))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                product = await product_service.lock_product(deletion, pid)
                await product_service.delete_product(deletion, product)
                await deletion.commit()
                resume.set()
                outcome = await asyncio.wait_for(task, 5)
                assert outcome[0] == 0 and outcome[1]['failed_count'] == 1
            finally:
                resume.set()
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        assert not await costs(sid)
        print('PASS 产品删除先提交：锁后刷新拒绝旧SKU，零新增成本')

        pid, sid, code = await fixture()
        entered, resume = asyncio.Event(), asyncio.Event()
        async with SessionLocal() as importer, SessionLocal() as renaming:
            task = asyncio.create_task(invoke(PauseQuery(importer, Sku, entered, resume), [(code, 10)]))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                sku, _ = await product_service.lock_sku_with_product(renaming, sid)
                sku.sku_code = 'RENAMED-' + uuid4().hex[:12]
                await renaming.commit()
                resume.set()
                outcome = await asyncio.wait_for(task, 5)
                assert outcome[0] == 0 and outcome[1]['failed_count'] == 1
            finally:
                resume.set()
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        assert not await costs(sid)
        print('PASS SKU改码先提交：不按旧编码继续写入成本')
    finally:
        if uid is not None:
            async with SessionLocal() as session:
                await session.execute(delete(AuditLog).where(AuditLog.operator_id == uid))
                await session.execute(delete(ProductCost).where(ProductCost.sku_id.in_(sids)))
                await session.execute(delete(Sku).where(Sku.id.in_(sids)))
                await session.execute(delete(Product).where(Product.id.in_(pids)))
                await session.execute(delete(User).where(User.id == uid))
                await session.commit()


async def run():
    try:
        await main()
        print('成本导入真PG互斥验证全部通过')
    finally:
        await engine.dispose()


if __name__ == '__main__':
    asyncio.run(run())
