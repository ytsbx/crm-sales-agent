"""P0/F01-01 + B04-01 隔离 SQLite 复现脚本（只读业务代码，不连生产库）。

运行：backend/.venv/bin/python review/verification/p0_quote_duplicate_convert.py
"""
import asyncio
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

from sqlalchemy.orm import Session  # noqa: E402

from conftest import SyncSessionAsAsync, make_engine  # noqa: E402
from app.core.errors import AppError  # noqa: E402
from app.modules.customer.model import Customer  # noqa: E402
from app.modules.order import service as order_service  # noqa: E402
from app.modules.order.model import SalesOrder, SalesOrderItem, OrderStatusHistory  # noqa: E402
from app.modules.quote.model import Quote, QuoteItem, QuoteVersion  # noqa: E402

NOW = datetime.now(UTC)
USER_ID = 101


def prepare(engine, *, version2_total: Decimal | None = None):
    """建两张报价版本：同 quote、不同 version，均已被客户接受。"""
    for table in (QuoteItem, SalesOrderItem, OrderStatusHistory):
        table.__table__.create(engine, checkfirst=True)

    with Session(engine, expire_on_commit=False) as raw:
        raw.add(Customer(id=1, name="P0复现客户", owner_id=USER_ID))
        quote = Quote(
            id=1, quote_no="P0-Q", customer_id=1, owner_id=USER_ID,
            current_version_id=1, status="accepted", valid_until=None,
        )
        version1 = QuoteVersion(
            id=1, quote_id=1, version_no=1, created_at=NOW, currency="CNY",
            subtotal_amount=Decimal("100.00"), total_amount=Decimal("100.00"),
            approval_status="approved", sent_at=NOW, accepted_at=NOW,
        )
        version2 = QuoteVersion(
            id=2, quote_id=1, version_no=2, created_at=NOW, currency="CNY",
            subtotal_amount=Decimal("100.00"),
            total_amount=(version2_total if version2_total is not None else Decimal("100.00")),
            approval_status="approved", sent_at=NOW, accepted_at=NOW,
        )
        raw.add_all([quote, version1, version2])
        raw.flush()
        for vid in (1, 2):
            raw.add(QuoteItem(
                id=vid, quote_version_id=vid, sku_id=None,
                sku_name_snapshot=f"P0行{vid}", quantity=Decimal("1"),
                quoted_price=Decimal("100.00"), cost_snapshot=Decimal("10"),
                cost_present_snapshot=True, cost_complete_snapshot=True,
            ))
        raw.commit()
        return raw, quote, version1, version2


async def main():
    engine = make_engine()
    raw, quote, version1, version2 = prepare(engine)
    session = SyncSessionAsAsync(raw)
    seq = {"n": 0}

    async def fake_order_no(_session):
        seq["n"] += 1
        return f"P0-SO-{seq['n']}"

    with patch.object(order_service, "generate_order_no", fake_order_no), \
         patch("app.modules.followup.service.record_and_notify", AsyncMock()):
        # 第一次转单：version1 是当前版本。
        quote.current_version_id = 1
        order1 = await order_service.create_order_from_quote(
            session, version=version1, user_id=USER_ID
        )
        await session.flush()
        print(f"[第一次转单] quote=1 version=1 -> order_no={order1.order_no} total={order1.total_amount}")

        # 模拟“客户接受后改运费/改价”：version2 是新建版本，金额改为 120。
        raw.query(Quote).filter(Quote.id == 1).update({"current_version_id": 2})
        raw.query(QuoteVersion).filter(QuoteVersion.id == 2).update({"total_amount": Decimal("120.00")})
        raw.commit()
        print(f"[改价后] 当前版本={raw.query(Quote).filter(Quote.id == 1).one().current_version_id} "
              f"version2.total={raw.query(QuoteVersion).filter(QuoteVersion.id == 2).one().total_amount}")

        try:
            order2 = await order_service.create_order_from_quote(
                session, version=version2, user_id=USER_ID
            )
            await session.flush()
            print(f"[第二次转单] quote=1 version=2 -> order_no={order2.order_no} total={order2.total_amount}")
            print(f"[结果] 同一报价生成订单数={raw.query(SalesOrder).count()}（期望 1，实际 2）")
        except AppError as exc:
            print(f"[第二次转单被拒] code={exc.code} message={exc.message}")

        # B04-01：新订单总额取的是 version2 当前值 120，而不是客户接受时的 100。
        order2_row = raw.query(SalesOrder).filter(SalesOrder.quote_version_id == 2).one_or_none()
        if order2_row is not None:
            print(f"[B04-01] 脚本模拟：接受时版本金额=100.00，接受后改成 120.00，订单落库总额={order2_row.total_amount}（无接受快照可比对）")

    raw.close()
    engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
