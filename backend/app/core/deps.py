"""FastAPI 依赖：当前用户、权限校验、数据范围。"""

from collections.abc import Awaitable, Callable

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.errors import AppError, ErrorCode
from app.core.security import decode_access_token
from app.modules.user.model import User
from app.modules.user.service import get_user_permission_codes, get_user_roles, resolve_data_scope

bearer_scheme = HTTPBearer(auto_error=False)


class CurrentUser:
    """请求上下文中的当前用户，附带权限与数据范围。"""

    def __init__(self, user: User, permissions: set[str], roles: list[str], data_scope: str):
        self.id = user.id
        self.name = user.name
        self.username = user.username
        self.department_id = user.department_id
        self.permissions = permissions
        self.roles = roles
        self.data_scope = data_scope

    def has(self, code: str) -> bool:
        return code in self.permissions


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_db),
) -> CurrentUser:
    if credentials is None or not credentials.credentials:
        raise AppError(ErrorCode.UNAUTHORIZED, "未登录", 401)

    payload = decode_access_token(credentials.credentials)
    try:
        user_id = int(payload.get("sub", ""))
    except (TypeError, ValueError) as exc:
        raise AppError(ErrorCode.UNAUTHORIZED, "登录状态无效", 401) from exc

    user = await session.get(User, user_id)
    if user is None or user.status != "active":
        raise AppError(ErrorCode.UNAUTHORIZED, "账号不存在或已停用", 401)

    roles = await get_user_roles(session, user_id)
    permissions = await get_user_permission_codes(session, user_id)
    return CurrentUser(
        user=user,
        permissions=permissions,
        roles=[r.code for r in roles],
        data_scope=resolve_data_scope(roles),
    )


def require_permission(*codes: str) -> Callable[..., Awaitable[CurrentUser]]:
    """要求当前用户拥有其中任意一个权限。"""

    async def _dep(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        # 管理员角色默认放行，避免新建权限时把管理员自己锁在门外
        if "admin" in user.roles or any(user.has(code) for code in codes):
            return user
        raise AppError(ErrorCode.FORBIDDEN, "无操作权限", 403)

    return _dep


def client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None
