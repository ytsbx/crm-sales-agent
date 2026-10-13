#!/usr/bin/env python3
"""第七批合同交错回归：必须调用真实签署/取消入口，仅用于一次性隔离库。

先在隔离库运行 Alembic upgrade head，再设置 DATABASE_URL / PYTHONPATH=.
执行本脚本。异常、错误响应、没有真正签成或取消都导致非零退出。
用 PostgreSQL 锁等待事件证明取消确实在等待签署持有的订单锁。
"""
import asyncio
import os
import sys
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _test_support
_test_support.require_isolated_db()

from sqlalchemy import select, text
from starlette.requests import Request
from app.core.database import SessionLocal, engine
from app.core.deps import CurrentUser
from app.core.errors import AppError
from app.modules.user.model import User
from app.modules.customer.model import Customer
from app.modules.order.model import SalesOrder
from app.modules.contract.model import ContractDocument, ContractTemplate
from app.modules.file.model import BusinessFile, FileRecord
from app.modules.file import service as file_service
from app.modules.contract import router as contract_router
from app.modules.contract.schema import DocumentSign
from app.modules.order import router as order_router

REQUEST = Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1)})

async def fixture(user, tag):
    tag = f"P13-{tag}-{uuid4().hex[:10]}"
    async with SessionLocal() as s:
        c = Customer(name=tag, owner_id=user.id, level="C", status="active", pool_status="private")
        s.add(c); await s.flush()
        order = SalesOrder(order_no=tag, customer_id=c.id, owner_id=user.id,
                           total_amount=Decimal(100), currency="CNY", status="pending",
                           created_at=datetime.now(UTC))
        tpl = ContractTemplate(doc_type="contract", name=tag, body="正文", enabled=True,
                               created_at=datetime.now(UTC))
        scan = FileRecord(object_key=f"isolated-test/{tag}.pdf", file_name=f"{tag}.pdf",
                          mime_type="application/pdf", size=5, uploaded_by=user.id)
        s.add_all([order, tpl, scan]); await s.flush()
        doc = ContractDocument(doc_no=tag, doc_type="contract", title=tag, customer_id=c.id,
                               order_id=order.id, template_id=tpl.id, content_snapshot="正文",
                               status="draft", created_at=datetime.now(UTC))
        s.add(doc); await s.commit()
        return order.id, doc.id, scan.id

async def sign(user, did, fid):
    async with SessionLocal() as s:
        try:
            response = await contract_router.sign_document(did, DocumentSign(file_id=fid), REQUEST, user, s)
            assert response["data"]["status"] == "signed", response
        except BaseException:
            await s.rollback()
            raise

async def cancel(user, oid, pid_ready=None):
    async with SessionLocal() as s:
        if pid_ready is not None:
            pid_ready.set_result(await s.scalar(text("select pg_backend_pid()")))
        try:
            response = await order_router.cancel_order(oid, REQUEST, user, s)
            assert response["data"]["status"] == "cancelled", response
        except BaseException:
            await s.rollback()
            raise

async def expect_rejection(coro, message):
    try:
        await coro
    except AppError as exc:
        assert message in exc.message, exc.message
    else:
        raise AssertionError(f"实际业务入口未拦截：{message}")

async def terminal(oid, did, order_status, doc_status, signed_files):
    async with SessionLocal() as s:
        assert (await s.get(SalesOrder, oid)).status == order_status
        assert (await s.get(ContractDocument, did)).status == doc_status
        refs = (await s.scalars(select(BusinessFile).where(
            BusinessFile.business_type == "contract", BusinessFile.business_id == did,
            BusinessFile.category == "signed"))).all()
        assert len(refs) == signed_files

async def main():
    async with SessionLocal() as s:
        row = User(name="合同交错回归", username=f"sign-lock-{uuid4().hex}", password_hash="test-only")
        s.add(row); await s.commit()
        user = CurrentUser(row, {"order:manage", "order:view", "customer:view", "file:view"},
                           ["admin"], "all")

    oid, did, fid = await fixture(user, "交错")
    reached, release = asyncio.Event(), asyncio.Event()
    original_lock = file_service.lock_file_row

    async def pause_after_source_lock(session, file_id):
        record = await original_lock(session, file_id)
        if file_id == fid:
            reached.set()
            await asyncio.wait_for(release.wait(), timeout=15)
        return record

    file_service.lock_file_row = pause_after_source_lock
    signer = asyncio.create_task(sign(user, did, fid))
    canceller = None
    try:
        await asyncio.wait_for(reached.wait(), timeout=10)
        pid_ready = asyncio.get_running_loop().create_future()
        canceller = asyncio.create_task(cancel(user, oid, pid_ready))
        pid = await asyncio.wait_for(pid_ready, timeout=10)
        # 不靠睡多久推断是否拿锁；观察实际 PostgreSQL 会话的锁等待。
        deadline = asyncio.get_running_loop().time() + 10
        while True:
            assert not canceller.done(), "取消未等待签署事务的订单锁"
            async with SessionLocal() as observer:
                wait_type = await observer.scalar(text(
                    "select wait_event_type from pg_stat_activity where pid=:pid"), {"pid": pid})
            if wait_type == "Lock":
                break
            assert asyncio.get_running_loop().time() < deadline, "未观察到订单锁等待"
            await asyncio.sleep(0.02)
        release.set()
        await asyncio.wait_for(signer, timeout=10)
        await asyncio.wait_for(expect_rejection(canceller, "已签署"), timeout=10)
        await terminal(oid, did, "pending", "signed", 1)
        print("PASS 实际路由：签署持锁 → 取消等待 → 已签闸拒绝取消")
    finally:
        release.set()
        file_service.lock_file_row = original_lock
        tasks = [task for task in (signer, canceller) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    await expect_rejection(cancel(user, oid), "已签署")
    await terminal(oid, did, "pending", "signed", 1)
    print("PASS 实际路由：签完再取消被拒绝")

    oid2, did2, fid2 = await fixture(user, "先取消")
    await cancel(user, oid2)
    await terminal(oid2, did2, "cancelled", "draft", 0)
    await expect_rejection(sign(user, did2, fid2), "已被取消")
    await terminal(oid2, did2, "cancelled", "draft", 0)
    print("PASS 实际路由：未签订单正常取消；取消后签署拒绝且无签署附件")

if __name__ == "__main__":
    async def run():
        try:
            await main()
        finally:
            await engine.dispose()
    asyncio.run(run())
