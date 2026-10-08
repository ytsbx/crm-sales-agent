"""FastAPI 依赖：当前用户、权限校验、数据范围。"""

from collections.abc import Awaitable, Callable

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.errors import AppError, ErrorCode
from app.core.security import decode_access_token
from app.modules.auth.session import SID_CLAIM, load_active_session
from app.modules.user.model import User
from app.modules.user.service import get_user_permission_codes, get_user_roles, resolve_data_scope

bearer_scheme = HTTPBearer(auto_error=False)


class CurrentUser:
    """请求上下文中的当前用户，附带权限与数据范围。

    `sid` 是这次请求所用令牌对应的登录会话标识（第十批 10.12），
    登出接口靠它定位"要作废哪一次登录"。
    **默认 None**：本项目里还有几处"内部构造的查看者"（通知、审批、企微同步
    等拿某个用户当视角去判断可见性），它们不来自请求、也没有会话，
    不该被迫编一个 sid 出来。
    """

    def __init__(
        self,
        user: User,
        permissions: set[str],
        roles: list[str],
        data_scope: str,
        *,
        sid: str | None = None,
    ):
        self.id = user.id
        self.name = user.name
        self.username = user.username
        self.department_id = user.department_id
        self.permissions = permissions
        self.roles = roles
        self.data_scope = data_scope
        self.sid = sid

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

    # 凭据还必须对应一个**服务端仍然有效**的登录会话（第十批 10.12）。
    # 少了这一步，登出与改密码都只影响前端本地状态：泄漏出去的旧令牌
    # 在过期前依旧畅通 —— 这正是无状态 JWT 的经典缺口。
    sid = payload.get(SID_CLAIM)
    await load_active_session(session, sid, user_id)

    roles = await get_user_roles(session, user_id)
    permissions = await get_user_permission_codes(session, user_id)
    return CurrentUser(
        user=user,
        permissions=permissions,
        roles=[r.code for r in roles],
        data_scope=resolve_data_scope(roles),
        sid=sid,
    )


def require_permission(*codes: str) -> Callable[..., Awaitable[CurrentUser]]:
    """要求当前用户拥有其中任意一个权限。"""

    async def _dep(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        # 管理员角色默认放行，避免新建权限时把管理员自己锁在门外
        if "admin" in user.roles or any(user.has(code) for code in codes):
            return user
        # 文案带上缺的权限码：前端列表页会把它显示在空态里，
        # "无操作权限：需要 opportunity:view" 远比一句干巴巴的"无操作权限"能定位问题
        raise AppError(
            ErrorCode.FORBIDDEN,
            f"无操作权限：需要 {' / '.join(codes)}",
            403,
        )

    return _dep


def ensure_permission(user: CurrentUser, code: str) -> None:
    """在函数体里校验一个权限。

    为什么需要它：`require_permission` 是写在路由依赖里的，一个接口只能声明一组
    "**任一**满足"的权限码。但客户 360 概览这类接口要**逐板块**用不同的模块权限判断
    （订单板块要 `order:view`、跟进板块要 `followup:view`……），写不进依赖里，
    只能在函数体内判。文案与 `require_permission` 保持一致，前端拿到的提示同源。

    管理员（admin 角色）一律放行，理由同 `require_permission`：新建权限码时
    不会把管理员自己锁在门外。
    """
    if "admin" in user.roles or user.has(code):
        return
    raise AppError(ErrorCode.FORBIDDEN, f"无操作权限：需要 {code}", 403)


def client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None
