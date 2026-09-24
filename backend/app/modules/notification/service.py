"""通知写入。所有函数都不 commit，由调用方的事务统一提交。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.notification.model import Notification
from app.modules.user.model import Permission, Role, role_permissions, user_roles


def notify(
    session: AsyncSession,
    *,
    user_id: int,
    type_: str,
    title: str,
    content: str | None = None,
    business_type: str | None = None,
    business_id: int | None = None,
) -> None:
    session.add(
        Notification(
            user_id=user_id,
            type=type_,
            title=title,
            content=content,
            business_type=business_type,
            business_id=business_id,
        )
    )


async def approver_user_ids(session: AsyncSession, permission_code: str) -> list[int]:
    """找出有某个权限（或管理员角色）的用户，用于审批类通知。"""
    stmt = (
        select(user_roles.c.user_id)
        .join(Role, Role.id == user_roles.c.role_id)
        .outerjoin(role_permissions, role_permissions.c.role_id == Role.id)
        .outerjoin(Permission, Permission.id == role_permissions.c.permission_id)
        .where((Permission.code == permission_code) | (Role.code == "admin"))
        .distinct()
    )
    return [int(uid) for uid in (await session.execute(stmt)).scalars().all()]


async def notify_approvers(
    session: AsyncSession,
    *,
    permission_code: str,
    title: str,
    content: str | None = None,
    business_type: str | None = None,
    business_id: int | None = None,
    exclude_user_id: int | None = None,
) -> int:
    user_ids = await approver_user_ids(session, permission_code)
    sent = 0
    for user_id in user_ids:
        if exclude_user_id and user_id == exclude_user_id:
            continue
        notify(
            session,
            user_id=user_id,
            type_="approval",
            title=title,
            content=content,
            business_type=business_type,
            business_id=business_id,
        )
        sent += 1
    return sent
