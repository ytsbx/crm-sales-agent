"""B02-07 / B18-01 历史外币数据路径：直接造一条已接受 USD 报价，再走真实转单服务。

只连接一次性隔离库。预期在真正的写订单之前被 CNY 闸门拒绝。
"""

import asyncio
import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.main  # noqa: F401  注册全部 ORM 模型
from app.core.errors import AppError
from app.modules.order.model import SalesOrder
from app.modules.order.service import create_order_from_quote
from app.modules.quote.model import Quote, QuoteVersion


URL = os.environ["DATABASE_URL"]


async def main() -> None:
    engine = create_async_engine(URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        old_quote = await session.scalar(
            select(Quote).where(Quote.quote_no == "HIST-FX-USD-001")
        )
        if old_quote is None:
            quote = Quote(
                quote_no="HIST-FX-USD-001",
                customer_id=1,
                owner_id=1,
                status="accepted",
                valid_until=date.today() + timedelta(days=30),
                created_by=1,
            )
            session.add(quote)
            await session.flush()
            version = QuoteVersion(
                quote_id=quote.id,
                version_no=1,
                total_amount=Decimal("100.00"),
                currency="USD",
                approval_status="approved",
                approval_required=False,
                created_at=datetime.now(timezone.utc),
                sent_at=datetime.now(timezone.utc),
                accepted_at=datetime.now(timezone.utc),
            )
            session.add(version)
            await session.flush()
            quote.current_version_id = version.id
            await session.commit()
        else:
            version = await session.scalar(
                select(QuoteVersion).where(QuoteVersion.quote_id == old_quote.id)
            )
            quote = old_quote

        version_id = int(version.id)
        quote_no = quote.quote_no
        currency = version.currency
        before = await session.scalar(
            select(SalesOrder.id).where(SalesOrder.quote_version_id == version_id)
        )
        try:
            await create_order_from_quote(session, version=version, user_id=1)
        except AppError as exc:
            await session.rollback()
            print(f"[HIST-FX] rejected code={exc.code} http={exc.http_status} message={exc.message}")
        else:
            await session.rollback()
            raise AssertionError("historical USD quote unexpectedly converted")

        after = await session.scalar(
            select(SalesOrder.id).where(SalesOrder.quote_version_id == version_id)
        )
        print(f"[HIST-FX] quote={quote_no} currency={currency} order_before={before} order_after={after}")
        assert before is None and after is None
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
