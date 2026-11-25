"""P2 状态重点隔离复现：B04-05 completed 订单仍可发起交期变更。"""
import asyncio
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from conftest import SyncSessionAsAsync, make_engine, make_user  # noqa: E402
from app.modules.order import schedule  # noqa: E402
from app.modules.order.model import SalesOrder  # noqa: E402
from app.modules.order.schedule import OrderScheduleChange  # noqa: E402

NOW = datetime.now(UTC)


async def run_case(status: str):
    engine = make_engine()
    OrderScheduleChange.__table__.create(engine, checkfirst=True)
    with Session(engine, expire_on_commit=False) as raw:
        order = SalesOrder(
            id=1, order_no=f"P2-B04-{status}", customer_id=1, owner_id=101,
            total_amount=Decimal("100.00"), currency="CNY", status=status,
            delivery_date=date.today(),
        )
        raw.add(order)
        raw.commit()
        session = SyncSessionAsAsync(raw)
        user = make_user(permissions=("order:manage",), roles=("salesperson",))
        affected = {
            "nodes": [{"id": 1}], "batches": [],
            "planning": {"before": "2026-11-23", "after": "2026-12-01"},
        }
        with patch.object(schedule, "lock_order", AsyncMock(return_value=order)), \
             patch.object(schedule, "preview", AsyncMock(return_value=affected)):
            try:
                row = await schedule.create_change(
                    session, order, user=user,
                    new_delivery_date=date(2026, 12, 1), reason="P2 复现",
                )
                await session.flush()
                print(f"[B04-05] status={status} -> 创建成功 change_id={row.id}")
            except Exception as exc:
                print(f"[B04-05] status={status} -> 拒绝 {type(exc).__name__}: {exc}")
    engine.dispose()


async def main():
    await run_case("completed")
    await run_case("cancelled")


if __name__ == "__main__":
    asyncio.run(main())
