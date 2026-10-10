"""FastAPI 依赖：当前用户、权限校验、数据范围。"""

from collections.abc import Awaitable, Callable
from ipaddress import ip_address, ip_network

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
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
    """取可信的客户端地址，避免任意请求头伪造来源 IP。

    `X-Forwarded-For` 只有在 TCP 对端属于显式配置的可信代理时才会读取；
    直接访问应用端口时，即使请求带了该请求头，也使用真实 TCP 对端地址。
    """
    peer = request.client.host if request.client else None
    if not peer:
        return None

    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded or not _is_trusted_proxy(peer):
        return peer

    chain = [part.strip() for part in forwarded.split(",") if part.strip()]
    if not chain:
        return peer
    # XFF 中每一项都必须是地址；遇到任意非法项就放弃整条头，避免把审计/限流
    # 键建立在代理传来的任意字符串上。
    try:
        [ip_address(value) for value in chain]
    except ValueError:
        return peer

    # 从离应用最近的代理向左走，跳过连续的可信代理；第一个非可信地址就是
    # 客户端。单层代理时等价于取 XFF 最右项。
    for value in reversed(chain):
        if not _is_trusted_proxy(value):
            return value
    return chain[0]


def _is_trusted_proxy(value: str) -> bool:
    try:
        address = ip_address(value)
    except ValueError:
        return False
    for configured in settings.trusted_proxy_ip_list:
        try:
            if address in ip_network(configured, strict=False):
                return True
        except ValueError:
            # 配置错误不能让请求处理变成 500；该项按未信任处理。
            continue
    return False
