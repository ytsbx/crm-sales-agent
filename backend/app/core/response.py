"""统一响应结构与分页。"""

from typing import Any, TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

T = TypeVar("T")


def ok(data: Any = None, message: str = "ok") -> dict[str, Any]:
    return {"code": 0, "message": message, "data": data}


def page_data(items: list[Any], total: int, page: int, page_size: int) -> dict[str, Any]:
    return {"items": items, "page": page, "page_size": page_size, "total": total}


async def paginate(
    session: AsyncSession,
    stmt: Select,
    page: int,
    page_size: int,
) -> tuple[list[Any], int]:
    """按页查询，返回 (当前页对象, 总数)。"""
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
    total = (await session.execute(count_stmt)).scalar_one()
    rows = (
        await session.execute(stmt.offset((page - 1) * page_size).limit(page_size))
    ).scalars().all()
    return list(rows), int(total)
