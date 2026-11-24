"""P2 报价状态：B02-03 联系人不校验；B02-04 已接受未转单仍可软删。"""
import asyncio
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

from sqlalchemy.orm import Session  # noqa: E402

from conftest import SyncSessionAsAsync, make_engine, make_user  # noqa: E402
from app.core.audit import AuditLog  # noqa: E402
from app.modules.customer.model import Customer  # noqa: E402
from app.modules.quote import router as quote_router  # noqa: E402
from app.modules.quote.model import Quote, QuoteVersion  # noqa: E402
from app.modules.quote.schema import QuoteUpdate  # noqa: E402

NOW = datetime.now(UTC)
REQ = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), headers={})


async def main():
    engine = make_engine()
    AuditLog.__table__.create(engine, checkfirst=True)
    with Session(engine, expire_on_commit=False) as raw:
        raw.add(Customer(id=1, name="P2报价客户", owner_id=101))
        raw.add(Quote(
            id=1, quote_no="P2-Q-1", customer_id=1, owner_id=101,
            status="accepted", current_version_id=None, valid_until=None,
        ))
        raw.add(Quote(
            id=2, quote_no="P2-Q-2", customer_id=1, owner_id=101,
            status="draft", current_version_id=2, valid_until=date(2026, 12, 1),
        ))
        raw.add(QuoteVersion(
            id=2, quote_id=2, version_no=1, created_at=NOW, currency="CNY",
            subtotal_amount=Decimal("100.00"), total_amount=Decimal("100.00"),
            approval_status="draft", valid_until_snapshot=date(2026, 12, 1),
        ))
        raw.commit()
        session = SyncSessionAsAsync(raw)
        user = make_user(permissions=("quote:manage",), roles=("salesperson",))

        # B02-03：contact_id 填一个不存在的 id，更新是否校验。
        await quote_router.update_quote(
            quote_id=1, payload=QuoteUpdate(contact_id=999), request=REQ,
            user=user, session=session,
        )
        quote = raw.get(Quote, 1)
        print(f"[B02-03] 不存在的 contact_id=999 被写入：contact_id={quote.contact_id}")

        # B02-12：显式传 valid_until=null，是否会清掉主单与当前版快照。
        await quote_router.update_quote(
            quote_id=2, payload=QuoteUpdate(valid_until=None), request=REQ,
            user=user, session=session,
        )
        q2 = raw.get(Quote, 2)
        v2 = raw.get(QuoteVersion, 2)
        print(f"[B02-12] 显式 null 后 quote.valid_until={q2.valid_until} "
              f"version.valid_until_snapshot={v2.valid_until_snapshot}")

        # B02-04：已接受、但没有订单的报价，delete 是否放行。
        await quote_router.delete_quote(quote_id=1, request=REQ, user=user, session=session)
        quote = raw.get(Quote, 1)
        print(f"[B02-04] status=accepted 且无订单，删除后 deleted_at={quote.deleted_at}")

    engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
