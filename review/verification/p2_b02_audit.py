"""P2 审计字段：B02-15 客户接受报价的审计是否记录金额/币种。"""
import asyncio
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from conftest import SyncSessionAsAsync, make_engine  # noqa: E402
from app.core.audit import AuditLog  # noqa: E402
from app.modules.quote import lifecycle  # noqa: E402
from app.modules.quote.model import Quote, QuoteVersion  # noqa: E402

NOW = datetime.now(UTC)


async def main():
    engine = make_engine()
    AuditLog.__table__.create(engine, checkfirst=True)
    with Session(engine, expire_on_commit=False) as raw:
        raw.add(Quote(
            id=1, quote_no="P2-Q-AUDIT", customer_id=1, owner_id=101,
            status="sent", current_version_id=1, valid_until=None,
        ))
        raw.add(QuoteVersion(
            id=1, quote_id=1, version_no=1, created_at=NOW, currency="CNY",
            subtotal_amount=Decimal("100.00"), total_amount=Decimal("120.00"),
            approval_status="approved", sent_at=NOW,
        ))
        raw.commit()
        session = SyncSessionAsAsync(raw)
        quote = raw.get(Quote, 1)
        version = raw.get(QuoteVersion, 1)
        with patch.object(lifecycle, "record_and_notify", AsyncMock()):
            changed = await lifecycle.accept_version(
                session, quote=quote, version=version, operator_id=101, ip="127.0.0.1",
            )
            await session.flush()
        row = raw.execute(
            select(AuditLog).where(
                AuditLog.business_type == "quote",
                AuditLog.business_id == 1,
                AuditLog.action == "accept",
            )
        ).scalars().first()
        print(f"[B02-15] accept changed={changed} audit_after={row.after_data if row else None}")
        print("[B02-15] 期望审计含 amount/currency；实际只有 version_id/version_no")

    engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
