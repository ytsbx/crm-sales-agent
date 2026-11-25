"""P2 回款/应收重点隔离复现：B05-08 已取消节点可改金额；B05-09 驳回回款阻止删除。"""
import asyncio
import sys
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

from sqlalchemy.orm import Session  # noqa: E402

from conftest import SyncSessionAsAsync, make_engine, make_user  # noqa: E402
from app.core.audit import AuditLog  # noqa: E402
from app.modules.order.model import OrderMilestone, SalesOrder  # noqa: E402
from app.modules.payment import router as payment_router  # noqa: E402
from app.modules.payment.model import PaymentAllocation, PaymentRecord, ReceivablePlan  # noqa: E402
from app.modules.payment import service as payment_service  # noqa: E402
from app.modules.payment.schema import ReceivableUpdate  # noqa: E402

NOW = datetime.now(UTC)


async def main():
    engine = make_engine()
    AuditLog.__table__.create(engine, checkfirst=True)
    with Session(engine, expire_on_commit=False) as raw:
        raw.add(SalesOrder(
            id=1, order_no="P2-SO-1", customer_id=1, owner_id=101,
            total_amount=Decimal("100.00"), currency="CNY", status="pending",
        ))
        raw.add_all([
            ReceivablePlan(
                id=1, order_id=1, plan_name="已取消的期次", due_date=date.today() - timedelta(days=10),
                amount=Decimal("100.00"), currency="CNY", status="cancelled", created_at=NOW,
            ),
            ReceivablePlan(
                id=2, order_id=1, plan_name="正常期次", due_date=date.today() + timedelta(days=10),
                amount=Decimal("100.00"), currency="CNY", status="pending", created_at=NOW,
            ),
        ])
        raw.commit()
        session = SyncSessionAsAsync(raw)
        user = make_user(permissions=("payment:manage",), roles=("finance",))

        # B05-08：对已取消节点改金额。
        payload = ReceivableUpdate(amount=Decimal("200.00"), remark="P2 取消后改金额")
        await payment_router.update_receivable(
            plan_id=1, payload=payload, request=SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), headers={}),
            user=user, session=session,
        )
        plan = raw.get(ReceivablePlan, 1)
        print(f"[B05-08] 已取消节点改金额后 status={plan.status} amount={plan.amount}")

        # B05-10：回款记录的 receivable_plan_id 指向 A 单，但 order_id 属于 B 单。
        raw.add(SalesOrder(
            id=2, order_no="P2-SO-2", customer_id=1, owner_id=101,
            total_amount=Decimal("100.00"), currency="CNY", status="pending",
        ))
        raw.add(PaymentRecord(
            id=11, order_id=2, receivable_plan_id=1, received_amount=Decimal("50.00"),
            currency="CNY", status="confirmed", received_date=date.today(), created_at=NOW,
        ))
        raw.commit()
        rows = await payment_router.plan_payments(plan_id=1, user=user, session=session)
        print(f"[B05-10] 查 A 单期次 1 的回款，返回 order_id={[r['order_id'] for r in rows['data']]}")

        # B05-12：service 层手动分配允许分配额 > 回款额。
        raw.add(PaymentRecord(
            id=12, order_id=1, receivable_plan_id=2, received_amount=Decimal("100.00"),
            currency="CNY", status="confirmed", allocation_mode="manual",
            received_date=date.today(), created_at=NOW,
        ))
        raw.add(PaymentAllocation(
            id=1, payment_id=12, plan_id=2, amount=Decimal("150.00"), source="manual", created_at=NOW, updated_at=NOW,
        ))
        raw.commit()
        record = raw.get(PaymentRecord, 12)
        allocated = await payment_service.allocate_payment(session, record, operator_id=101)
        print(f"[B05-12] 回款 100.00 的手工分配合计=150.00，service 返回={allocated}（未拒绝）")

        # B05-09：一条 rejected 回款是否会阻止删除节点。
        raw.add(PaymentRecord(
            id=10, order_id=1, receivable_plan_id=2, received_amount=Decimal("50.00"),
            currency="CNY", status="rejected", received_date=date.today(), created_at=NOW,
        ))
        raw.commit()
        try:
            await payment_router.delete_receivable(
                plan_id=2, request=SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), headers={}),
                user=user, session=session,
            )
            print("[B05-09] rejected 回款未阻止删除（与预期不符）")
        except Exception as exc:
            print(f"[B05-09] 有 rejected 回款时删除被拒：{type(exc).__name__} {exc}")

    engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
