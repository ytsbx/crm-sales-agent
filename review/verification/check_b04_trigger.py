"""核验 B04-01 的前提：客户接受后还能不能通过改费用接口改总额。"""
import asyncio
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

from app.core.errors import AppError  # noqa: E402
from app.modules.quote.model import QuoteVersion  # noqa: E402
from app.modules.quote.service import ensure_charge_change_allowed  # noqa: E402

async def main():
    version = QuoteVersion(
        id=1, quote_id=1, version_no=1, created_at=datetime.now(UTC),
        currency="CNY", subtotal_amount=Decimal("100"), total_amount=Decimal("100"),
        approval_status="approved", sent_at=datetime.now(UTC), accepted_at=datetime.now(UTC),
    )
    after = {"charge_type": "logistics", "amount": Decimal("20"), "is_discount": False}
    try:
        await ensure_charge_change_allowed(version, before=None, after=after)
        print("[B04-01触发] 已接受版本仍可改运费：闸门放行")
    except AppError as exc:
        print(f"[B04-01触发] 已接受版本改运费被拒：code={exc.code} message={exc.message}")

if __name__ == "__main__":
    asyncio.run(main())
