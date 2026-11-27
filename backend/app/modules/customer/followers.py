"""客户跟进人的读写与不变量。

约定：
- 一个客户可以有多名有效跟进人（`customer_followers.deleted_at IS NULL`）；
- `customers.owner_id` 是**默认负责人**，必须是有效跟进人之一；
- 移除默认负责人前必须先把默认负责人换成别人（`remove_follower` 默认拒绝）。
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.modules.customer.model import Customer, CustomerFollower
from app.modules.user.model import User


async def followers_map(
    session: AsyncSession, customer_ids: list[int]
) -> dict[int, list[dict]]:
    """批量取有效跟进人（含姓名），供列表/详情序列化。"""
    if not customer_ids:
        return {}
    rows = (
        await session.execute(
            select(
                CustomerFollower.customer_id,
                CustomerFollower.user_id,
                CustomerFollower.source,
                CustomerFollower.created_at,
                User.name,
            )
            .join(User, User.id == CustomerFollower.user_id)
            .where(
                CustomerFollower.customer_id.in_(customer_ids),
                CustomerFollower.deleted_at.is_(None),
            )
            .order_by(CustomerFollower.customer_id, CustomerFollower.created_at)
        )
    ).all()
    result: dict[int, list[dict]] = {}
    for customer_id, user_id, source, created_at, name in rows:
        result.setdefault(customer_id, []).append(
            {
                "user_id": user_id,
                "name": name,
                "source": source,
                "created_at": created_at,
            }
        )
    return result


async def active_follower_ids(
    session: AsyncSession, customer_id: int, *, for_update: bool = False
) -> list[int]:
    stmt = select(CustomerFollower.user_id).where(
        CustomerFollower.customer_id == customer_id,
        CustomerFollower.deleted_at.is_(None),
    )
    if for_update:
        stmt = stmt.with_for_update()
    return list((await session.execute(stmt)).scalars().all())


async def add_followers(
    session: AsyncSession,
    customer_id: int,
    user_ids,
    *,
    source: str,
    created_by: int | None = None,
) -> list[int]:
    """幂等加入跟进人：已有效的不重复；软删过的恢复为有效。返回新增/恢复的 id。"""
    targets = {int(uid) for uid in user_ids if uid}
    if not targets:
        return []
    rows = (
        await session.execute(
            select(CustomerFollower).where(
                CustomerFollower.customer_id == customer_id,
                CustomerFollower.user_id.in_(targets),
            )
        )
    ).scalars().all()
    by_user = {row.user_id: row for row in rows}
    changed: list[int] = []
    for user_id in sorted(targets):
        row = by_user.get(user_id)
        if row is None:
            session.add(
                CustomerFollower(
                    customer_id=customer_id,
                    user_id=user_id,
                    source=source,
                    created_by=created_by,
                )
            )
            changed.append(user_id)
        elif row.deleted_at is not None:
            row.deleted_at = None
            row.source = source
            row.created_by = created_by
            changed.append(user_id)
    if changed:
        await session.flush()
    return changed


async def remove_follower(
    session: AsyncSession, customer: Customer, user_id: int, *, allow_default: bool = False
) -> bool:
    """软删一条跟进关系；默认负责人默认拒绝直接移除。"""
    if customer.owner_id == user_id and not allow_default:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            "默认负责人不能直接移除，请先更换默认负责人",
            422,
        )
    row = (
        await session.execute(
            select(CustomerFollower)
            .where(
                CustomerFollower.customer_id == customer.id,
                CustomerFollower.user_id == user_id,
                CustomerFollower.deleted_at.is_(None),
            )
            .with_for_update()
        )
    ).scalars().first()
    if row is None:
        return False
    row.deleted_at = datetime.now(UTC)
    await session.flush()
    return True


async def set_default_follower(session: AsyncSession, customer: Customer, user_id: int) -> None:
    """把某位有效跟进人设为默认负责人（不搬单据；搬单据走 transfer_customer）。"""
    follower_ids = await active_follower_ids(session, customer.id, for_update=True)
    if user_id not in follower_ids:
        raise AppError(
            ErrorCode.PARAM_ERROR, "该同事还不是这个客户的跟进人，请先添加跟进人", 422
        )
    customer.owner_id = user_id
    if customer.pool_status == "public":
        customer.pool_status = "private"
    await session.flush()


async def ensure_default_is_follower(
    session: AsyncSession,
    customer: Customer,
    *,
    source: str = "manual",
    created_by: int | None = None,
) -> None:
    """保证默认负责人在有效跟进人里（历史数据/旧入口兜底）。"""
    if customer.owner_id is None:
        return
    follower_ids = await active_follower_ids(session, customer.id)
    if customer.owner_id not in follower_ids:
        await add_followers(
            session,
            customer.id,
            [customer.owner_id],
            source=source,
            created_by=created_by,
        )
