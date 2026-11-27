"""客户跟进人的读写与不变量。

约定：
- 一个客户可以有多名有效跟进人（`customer_followers.deleted_at IS NULL`）；
- 客户**没有**默认负责人；`customers.owner_id` 是历史字段，本模块不再读写；
- 私海客户至少保留一位跟进人：移除到最后一位时拒绝（先指定接手人或放入公海）。
"""

from datetime import UTC, datetime

from sqlalchemy import func, select
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
            .order_by(CustomerFollower.customer_id, CustomerFollower.created_at, CustomerFollower.id)
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


async def active_follower_count(session: AsyncSession, customer_id: int) -> int:
    return int(
        (
            await session.execute(
                select(func.count(CustomerFollower.id)).where(
                    CustomerFollower.customer_id == customer_id,
                    CustomerFollower.deleted_at.is_(None),
                )
            )
        ).scalar_one()
    )


async def is_follower(session: AsyncSession, customer_id: int, user_id: int) -> bool:
    return (
        await session.execute(
            select(CustomerFollower.id)
            .where(
                CustomerFollower.customer_id == customer_id,
                CustomerFollower.user_id == user_id,
                CustomerFollower.deleted_at.is_(None),
            )
            .limit(1)
        )
    ).first() is not None


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
    session: AsyncSession,
    customer: Customer,
    user_id: int,
    *,
    allow_empty: bool = False,
) -> bool:
    """软删一条跟进关系。

    **最后一位跟进人不能移除**——没人跟进的私海客户没人能看到（除管理员），
    也会让"客户数/待办/通知"失去归属。要换人请走转交（先加接手人再移除），
    要不跟了请显式「放入公海」（公海=没有跟进人，由 `clear_followers` 统一清空）。
    """
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
    if not allow_empty and await active_follower_count(session, customer.id) <= 1:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            "这是客户最后一位跟进人。请先转交给接手人，或把客户放入公海",
            422,
        )
    row.deleted_at = datetime.now(UTC)
    await session.flush()
    return True


async def replace_follower(
    session: AsyncSession,
    customer: Customer,
    old_user_id: int | None,
    new_user_id: int,
    *,
    source: str,
    created_by: int | None = None,
    keep_old: bool = False,
) -> dict:
    """用接手人替换一位跟进人（转交/离职交接共用）。

    - 接手人先加入（幂等）；
    - 被替换人仍有效、且不是同一个人、且 `keep_old=False` 时移除；
    - 客户只有被替换人一位时**不移除**（先加后减，客户不会出现零跟进人；
      这类情况应显式传 `keep_old=True` 或由调用方拒绝，见 transfer_customer）。
    """
    added = await add_followers(
        session,
        customer.id,
        [new_user_id],
        source=source,
        created_by=created_by,
    )
    removed = False
    if old_user_id and old_user_id != new_user_id and not keep_old:
        if await is_follower(session, customer.id, old_user_id):
            if await active_follower_count(session, customer.id) > 1:
                removed = await remove_follower(
                    session, customer, old_user_id, allow_empty=True
                )
    if customer.pool_status == "public":
        customer.pool_status = "private"
        await session.flush()
    return {"added": added, "removed": removed}


async def clear_followers(
    session: AsyncSession, customer: Customer, *, keep_user_ids=None
) -> int:
    """清空有效跟进关系（放入公海专用）。

    公海客户 = 没有跟进人：所有有效跟进关系软删，历史保留。
    `keep_user_ids` 仅用于兼容旧口径，正常调用不传。
    """
    keep = {int(uid) for uid in (keep_user_ids or []) if uid}
    rows = (
        await session.execute(
            select(CustomerFollower)
            .where(
                CustomerFollower.customer_id == customer.id,
                CustomerFollower.deleted_at.is_(None),
            )
            .with_for_update()
        )
    ).scalars().all()
    now = datetime.now(UTC)
    cleared = 0
    for row in rows:
        if row.user_id in keep:
            continue
        row.deleted_at = now
        cleared += 1
    if cleared:
        await session.flush()
    return cleared
