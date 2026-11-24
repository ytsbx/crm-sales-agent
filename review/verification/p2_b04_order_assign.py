"""P2/B04-04：订单改派只查 order:assign + 目标 active，不查数据范围。"""
import asyncio
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

from sqlalchemy.orm import Session  # noqa: E402

from conftest import SyncSessionAsAsync, make_engine, make_user  # noqa: E402
from app.core.audit import AuditLog  # noqa: E402
from app.modules.order import router as order_router  # noqa: E402
from app.modules.order.model import SalesOrder  # noqa: E402
from app.modules.order.schema import OrderUpdate  # noqa: E402
from app.modules.user.model import User  # noqa: E402

NOW = datetime.now(UTC)
REQ = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), headers={})


async def main():
    engine = make_engine()
    AuditLog.__table__.create(engine, checkfirst=True)
    with Session(engine, expire_on_commit=False) as raw:
        raw.add_all([
            User(id=101, name="张业务", username="zhang", password_hash="x",
                 department_id=1, status="active"),
            User(id=202, name="外部门李", username="li", password_hash="x",
                 department_id=99, status="active"),
            SalesOrder(
                id=1, order_no="P2-B04-ASSIGN", customer_id=1, owner_id=101,
                total_amount=Decimal("100.00"), currency="CNY", status="pending",
            ),
        ])
        raw.commit()
        session = SyncSessionAsAsync(raw)
        user = make_user(
            permissions=("order:manage", "order:assign"),
            roles=("salesperson",), data_scope="self",
        )
        await order_router.update_order(
            order_id=1, payload=OrderUpdate(owner_id=202),
            request=REQ, user=user, session=session,
        )
        order = raw.get(SalesOrder, 1)
        print(f"[B04-04] 调用者 data_scope=self，订单 owner 101 -> {order.owner_id}（外部门 202）")

    engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
