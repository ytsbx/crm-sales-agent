"""Auth：登录 / 登出 / 当前用户 / 我的权限（对齐 03-API §2）。"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, get_current_user
from app.core.errors import AppError, ErrorCode
from app.core.response import ok
from app.core.security import create_access_token, verify_password
from app.modules.auth.schema import LoginRequest
from app.modules.user.model import Department, User

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/login")
async def login(
    payload: LoginRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    stmt = select(User).where(User.username == payload.username)
    user = (await session.execute(stmt)).scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise AppError(ErrorCode.PARAM_ERROR, "用户名或密码错误")
    if user.status != "active":
        raise AppError(ErrorCode.FORBIDDEN, "账号已停用", 403)

    token = create_access_token(user.id, {"name": user.name})
    await write_audit(
        session,
        operator_id=user.id,
        action="login",
        business_type="user",
        business_id=user.id,
        ip=client_ip(request),
    )
    await session.commit()
    return ok({"access_token": token, "token_type": "Bearer", "user": _user_brief(user)})


@router.post("/logout")
async def logout(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """登出。

    JWT 是无状态的，服务端没有可吊销的会话，所以登出本身只由前端丢弃 token。
    但登录有审计、登出没有会让"会话时长"这类排查缺一半信息，所以这里补一条，
    与 login 对称。
    """
    await write_audit(
        session,
        operator_id=user.id,
        action="logout",
        business_type="auth",
        business_id=user.id,
        after={"username": user.username},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(None, "已登出")


@router.get("/me")
async def me(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    dept_name = None
    if user.department_id:
        dept = await session.get(Department, user.department_id)
        dept_name = dept.name if dept else None
    return ok(
        {
            "id": user.id,
            "name": user.name,
            "username": user.username,
            "department": dept_name,
            "roles": user.roles,
            "data_scope": user.data_scope,
            "permissions": sorted(user.permissions),
        }
    )


@router.get("/permissions")
async def my_permissions(user: CurrentUser = Depends(get_current_user)):
    return ok({"permissions": sorted(user.permissions), "roles": user.roles})


def _user_brief(user: User) -> dict:
    return {"id": user.id, "name": user.name, "username": user.username}
