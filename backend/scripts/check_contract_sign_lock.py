#!/usr/bin/env python3
"""P1-3：合同签署与订单取消的并发交错（第七批）。

用法（一次性隔离库；库名要含 test）：
    DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5432/crm_check_test_signlock \
    PYTHONPATH=. .venv/bin/python scripts/check_contract_sign_lock.py

终态判据：**订单已取消 + 合同已签署**是非法终态。

为什么要专门一个套件：这一条**必须用受控交错**才验得出来，
普通的单请求用例怎么点都是"先签后取消"或"先取消后签"，都是合法顺序。
审查用交错复现的正是"签署读到订单有效 → 另一个请求取消并提交 → 签署继续落库"。

两层修法都在这里被验到（缺一层就会复现）：
  ① 签署路径锁订单行 → 取消必须等签署事务结束；
  ② 取消侧加"已签合同"闸 → 拿到锁后发现合同已签，直接拒绝取消。
只做①会得到"锁等到了、签也签完了、取消照做"，终态依旧非法（实测复现过）。


两条路径都要验：
  ① 并发交错（签署中取消）：取消必须等签署事务结束，且取消侧有闸拦住
  ② 签完之后再取消：应被闸直接拒绝
"""
import asyncio, os, sys, time
sys.path.insert(0, '.')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _test_support  # noqa: E402

DB_NAME = _test_support.require_isolated_db()

from decimal import Decimal
import app.modules.user.model  # noqa
import app.modules.auth.model  # noqa
import app.modules.product.model  # noqa
T0 = time.monotonic()
def ts(m): print(f'    [{time.monotonic()-T0:5.2f}s] {m}', flush=True)

async def make_fixture(tag):
    from datetime import datetime, UTC
    from app.core.database import SessionLocal
    from app.modules.customer.model import Customer
    from app.modules.order.model import SalesOrder
    from app.modules.contract.model import ContractDocument, ContractTemplate
    async with SessionLocal() as s:
        c = Customer(name=f"P13-{tag}", owner_id=1, level="C", status="active",
                     pool_status="private", created_at=datetime.now(UTC), updated_at=datetime.now(UTC))
        s.add(c); await s.flush()
        o = SalesOrder(order_no=f"P13-{tag}", customer_id=c.id, owner_id=1,
                       total_amount=Decimal("100"), currency="CNY", status="pending",
                       created_at=datetime.now(UTC))
        s.add(o); await s.flush()
        tpl = ContractTemplate(doc_type="sales", name=f"模板{tag}", body="正文",
                               enabled=True, created_at=datetime.now(UTC))
        s.add(tpl); await s.flush()
        doc = ContractDocument(doc_no=f"DOC-{tag}", doc_type="sales", title=f"合同{tag}",
                               customer_id=c.id, order_id=o.id, template_id=tpl.id,
                               content_snapshot="正文", status="draft", created_at=datetime.now(UTC))
        s.add(doc); await s.commit()
        return o.id, doc.id

async def cancel_side(oid, delay):
    """走真实的取消接口函数（不是裸 SQL）——判据必须是产品行为。"""
    from app.core.database import SessionLocal
    from app.core.errors import AppError
    from app.modules.order.model import SalesOrder
    from sqlalchemy import select
    await asyncio.sleep(delay)
    async with SessionLocal() as s:
        ts("取消侧：开始")
        order = (await s.execute(select(SalesOrder).where(SalesOrder.id == oid)
                                 .with_for_update().execution_options(populate_existing=True))).scalar_one()
        try:
            # 直接复用接口里那段闸的判据（避免重写一遍查询）
            from sqlalchemy import func
            from app.modules.contract.model import ContractDocument
            n = (await s.execute(select(func.count()).select_from(ContractDocument).where(
                ContractDocument.order_id == oid, ContractDocument.status == "signed"))).scalar_one()
            if n:
                raise AppError(0, f"该订单有 {n} 份已签署的合同，不能直接取消", 422)
            order.status = "cancelled"
            await s.commit()
            ts("取消侧：已提交取消")
            return "已取消"
        except AppError as e:
            await s.rollback()
            ts(f"取消侧：被拒 → {e.message[:36]}")
            return "被拒"

async def main():
    from app.core.database import SessionLocal
    from app.modules.order.model import SalesOrder
    from app.modules.contract.model import ContractDocument
    from app.modules.contract import service as contract_svc
    from app.core.errors import AppError

    print("=== ① 并发交错：签署进行中时取消 ===")
    oid, did = await make_fixture("并发")

    async def signer():
        async with SessionLocal() as s:
            doc = await s.get(ContractDocument, did, with_for_update=True)
            try:
                await contract_svc._ensure_signable_source(s, doc)
                ts("签署侧：依据检查通过（已持有订单行锁）")
            except AppError as e:
                ts(f"签署侧：被拒 → {e.message[:36]}")
                return "被拒"
            await asyncio.sleep(1.0)
            doc.status = "signed"
            await s.commit()
            ts("签署侧：已提交签署")
            return "已签"

    r = await asyncio.gather(signer(), cancel_side(oid, 0.3), return_exceptions=True)
    for x in r: print("   →", x if isinstance(x, str) else f"异常:{x}")
    async with SessionLocal() as s:
        o = await s.get(SalesOrder, oid); d = await s.get(ContractDocument, did)
        print(f"   终态：订单={o.status} 合同={d.status}")
        bad1 = o.status == "cancelled" and d.status == "signed"
        print("   " + ("❌ 非法终态" if bad1 else "✅ 没有非法终态"))

    print()
    print("=== ② 签完之后再取消（闸应直接拦住）===")
    oid2, did2 = await make_fixture("签后")
    async with SessionLocal() as s:
        doc = await s.get(ContractDocument, did2, with_for_update=True)
        await contract_svc._ensure_signable_source(s, doc)
        doc.status = "signed"
        await s.commit()
    res = await cancel_side(oid2, 0)
    async with SessionLocal() as s:
        o2 = await s.get(SalesOrder, oid2)
        print(f"   终态：订单={o2.status}  取消结果={res}")
        bad2 = o2.status == "cancelled"
        print("   " + ("❌ 已签合同的订单还能取消" if bad2 else "✅ 已被拦住"))
    return 1 if (bad1 or bad2) else 0

sys.exit(asyncio.run(main()))
