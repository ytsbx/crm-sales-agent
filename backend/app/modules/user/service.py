"""用户 / 角色 / 权限查询。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.user.model import Permission, Role, role_permissions, user_roles


async def get_user_roles(session: AsyncSession, user_id: int) -> list[Role]:
    stmt = select(Role).join(user_roles, user_roles.c.role_id == Role.id).where(
        user_roles.c.user_id == user_id
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_user_permission_codes(session: AsyncSession, user_id: int) -> set[str]:
    stmt = (
        select(Permission.code)
        .join(role_permissions, role_permissions.c.permission_id == Permission.id)
        .join(user_roles, user_roles.c.role_id == role_permissions.c.role_id)
        .where(user_roles.c.user_id == user_id)
    )
    return {code for code in (await session.execute(stmt)).scalars().all()}


def resolve_data_scope(roles: list[Role]) -> str:
    """多个角色时取范围最大者。"""
    order = ["self", "department", "department_and_sub", "all"]
    best = "self"
    for role in roles:
        scope = role.data_scope or "self"
        if scope in order and order.index(scope) > order.index(best):
            best = scope
    return best
