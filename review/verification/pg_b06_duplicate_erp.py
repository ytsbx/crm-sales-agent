"""PostgreSQL 并发验证：同一 erp_order_id 能否被两张订单同时写入。

只连接一次性隔离库；不写生产/演示库。运行：
DATABASE_URL=postgresql+asyncpg://postgres@127.0.0.1:5432/crm_review_smoke_20261123 \
  backend/.venv/bin/python review/verification/pg_b06_duplicate_erp.py
"""

import asyncio
import os
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.base import Base
import app.main  # noqa: F401  注册全部 ORM 模型，避免外键找不到 customers
from app.modules.order.model import SalesOrder


URL = os.environ["DATABASE_URL"]
ERP_ID = "ERP-CONCURRENT-DUP-001"


async def insert_order(order_no: str, ready: asyncio.Event) -> str:
    engine = create_async_engine(URL, pool_size=2, max_overflow=0)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as session:
            row = SalesOrder(
                order_no=order_no,
                customer_id=1,
                total_amount=Decimal("1.00"),
                currency="CNY",
                status="pending",
                erp_order_id=ERP_ID,
            )
            session.add(row)
            ready.set()
            await asyncio.sleep(0.2)
            await session.commit()
            return f"{order_no}: committed id={row.id}"
    finally:
        await engine.dispose()


async def main() -> None:
    engine = create_async_engine(URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        # 清理上次运行残留，保证可重复。
        await session.execute(
            SalesOrder.__table__.delete().where(SalesOrder.erp_order_id == ERP_ID)
        )
        await session.commit()

    ready = asyncio.Event()
    first = asyncio.create_task(insert_order("PG-DUP-A", ready))
    await ready.wait()
    second = asyncio.create_task(insert_order("PG-DUP-B", ready))
    results = await asyncio.gather(first, second, return_exceptions=True)
    print("results:", results)

    async with Session() as session:
        count = await session.scalar(
            select(func.count()).select_from(SalesOrder).where(SalesOrder.erp_order_id == ERP_ID)
        )
        rows = (
            await session.execute(
                select(SalesOrder.id, SalesOrder.order_no, SalesOrder.erp_order_id)
                .where(SalesOrder.erp_order_id == ERP_ID)
                .order_by(SalesOrder.id)
            )
        ).all()
    print("same erp_order_id count:", count)
    print("rows:", rows)
    assert count == 2, f"expected 2 duplicate rows, got {count}"
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
