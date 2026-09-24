"""通知接口。"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import CurrentUser, get_current_user
from app.core.response import ok, page_data, paginate
from app.modules.notification.model import Notification

router = APIRouter(tags=["Notification"])


def serialize(row: Notification) -> dict:
    return {
        "id": row.id,
        "type": row.type,
        "title": row.title,
        "content": row.content,
        "business_type": row.business_type,
        "business_id": row.business_id,
        "read": row.read_at is not None,
        "created_at": row.created_at,
    }


@router.get("/notifications")
async def list_notifications(
    unread_only: bool = False,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    stmt = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    rows, total = await paginate(session, stmt.order_by(Notification.id.desc()), page, page_size)
    return ok(page_data([serialize(row) for row in rows], total, page, page_size))


@router.get("/notifications/unread-count")
async def unread_count(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    count = (
        await session.execute(
            select(func.count(Notification.id)).where(
                Notification.user_id == user.id, Notification.read_at.is_(None)
            )
        )
    ).scalar_one()
    return ok({"count": int(count)})


@router.post("/notifications/{notification_id}/read")
async def mark_read(
    notification_id: int,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    await session.execute(
        update(Notification)
        .where(Notification.id == notification_id, Notification.user_id == user.id)
        .values(read_at=datetime.now(UTC))
    )
    await session.commit()
    return ok(None, "已读")


@router.post("/notifications/read-all")
async def mark_all_read(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    await session.execute(
        update(Notification)
        .where(Notification.user_id == user.id, Notification.read_at.is_(None))
        .values(read_at=datetime.now(UTC))
    )
    await session.commit()
    return ok(None, "全部已读")
