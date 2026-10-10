"""Auth：登录 / 登出 / 当前用户 / 我的权限（对齐 03-API §2）。"""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.config import settings
from app.core.database import get_db
from app.core.deps import CurrentUser, bearer_scheme, client_ip, get_current_user
from app.core.errors import AppError, ErrorCode
from app.core import rate_limit
from app.core.response import ok
from app.core.security import (
    create_access_token,
    decode_access_token_allow_expired,
    verify_password,
)
from app.modules.auth.schema import LoginRequest, WeComSsoCallback
from app.modules.auth.session import (
    REASON_LOGOUT,
    SID_CLAIM,
    load_active_session,
    open_session,
    revoke_session,
)
from app.modules.user.model import Department, User

router = APIRouter(prefix="/auth", tags=["Auth"])

logger = logging.getLogger(__name__)


@router.post("/login")
async def login(
    payload: LoginRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    # 防爆破：同一（用户名+IP）在窗口内连续失败到上限直接拒绝，
    # 不再执行 bcrypt 校验（既防猜密码，也防拿登录接口耗 CPU）
    throttle_key = f"{payload.username.lower()}|{client_ip(request) or 'unknown'}"
    locked = rate_limit.remaining_lock_seconds(
        throttle_key,
        max_attempts=settings.login_max_attempts,
        window_minutes=settings.login_lockout_window_minutes,
    )
    if locked:
        raise AppError(
            ErrorCode.RATE_LIMITED,
            f"登录失败次数过多，请 {max(1, locked // 60 + 1)} 分钟后再试",
            429,
        )

    # 登录必须与「改密码 / 重置密码」互斥到**同一把账号锁**上（第十批 10.12 复审）。
    #
    # 只在改密码时"作废该账号已有会话"是挡不住的：一次登录可能**正在途中** ——
    # 密码已经校验通过、会话还没写进去。那一刻作废，扫不到这条还没诞生的会话，
    # 它随后照样落库、照样能访问接口（复审复现的正是这一条）。
    # 所以两件事要抢同一把锁：抢到的先跑完，另一个才轮到。
    #
    # ⚠️ 锁必须**在读密码之前**拿，并且 `populate_existing` 让这次读覆盖内存里的旧值：
    # 否则校验用的是加锁前读到的旧哈希，"改密码先完成"那个顺序照样让旧密码进来。
    # 这把锁一直持有到本函数末尾那次 `commit` —— 中间不提交。
    stmt = (
        select(User)
        .where(User.username == payload.username)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    user = (await session.execute(stmt)).scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.password_hash):
        locked_after = rate_limit.register_failure(
            throttle_key,
            max_attempts=settings.login_max_attempts,
            window_minutes=settings.login_lockout_window_minutes,
        )
        if locked_after:
            raise AppError(
                ErrorCode.RATE_LIMITED,
                f"登录失败次数过多，账号已临时锁定，请 {max(1, locked_after // 60 + 1)} 分钟后再试",
                429,
            )
        raise AppError(ErrorCode.PARAM_ERROR, "用户名或密码错误")
    if user.status != "active":
        raise AppError(ErrorCode.FORBIDDEN, "账号已停用", 403)

    rate_limit.reset(throttle_key)

    # 每次登录开一个**服务端会话**，令牌里带上它的标识（第十批 10.12）。
    # 与审计同一次提交：不会出现"审计有登录、会话表却没有"的半成品。
    login_session = await open_session(session, user.id)
    token = create_access_token(
        user.id, {"name": user.name}, session_id=login_session.sid
    )
    await write_audit(
        session,
        operator_id=user.id,
        action="login",
        business_type="user",
        business_id=user.id,
        ip=client_ip(request),
    )
    await session.commit()

    # ⚠️ 先把要返回给前端的标量**取出来**（C5-05，2026-10-10 修）。
    #
    # 为什么必须在这里取：下面那段附加扫描失败时会 `session.rollback()`，
    # 而回滚会让 ORM 实例的**所有属性过期**。过期之后同步读 `user.name`
    # 会触发一次懒加载 —— 在同步上下文里就是
    # `MissingGreenlet: greenlet_spawn has not been called`。
    # 实测症状：月结扫描一抛异常 → 登录代码回滚 → `_user_brief(user)` 炸 → **登录返回 500**。
    # 一个"附加提醒"把"能不能登录"打挂了，与那段 try 注释里写的取舍正好相反。
    # 取值放在 commit 之后（`user.id` 在 commit 时也会过期，只是同一个 session 里
    # 属性还留着上次加载的值；显式取出来才是确定的）。
    user_brief = _user_brief(user)

    # 月结协议到期提醒（口径已确认 2026-10-05）：**扫描挂在登录上**。
    # 原本它只挂在每日自动任务里，而 `SCHEDULER_ENABLED` 按约定一直关着（多实例安全），
    # 于是这条提醒永远不触发、需求等于没做。登录时只扫**这个人自己名下**的协议，
    # 顺带建待办 + 站内通知（去重靠 source_key，同一协议同一到期周期只会建一次）。
    # 两处刻意的取舍：
    # ① 放在 commit **之后**、且整段包在 try 里 —— 提醒是附加动作，
    #    它出任何问题都不能影响"人能不能登录"；
    # ② 异常只记日志不回抛（登录已经成功提交了）。
    try:
        from app.modules.contract import service as contract_service

        if await contract_service.notify_expiring_monthly(session, owner_id=user.id):
            await session.commit()
    except Exception:
        await session.rollback()
        logger.exception("登录时的月结到期扫描失败（不影响登录）")

    # 用上面取好的标量，**不再碰 user 对象**：它可能已被回滚整片过期。
    return ok({"access_token": token, "token_type": "Bearer", "user": user_brief})


@router.post("/refresh")
async def refresh(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_db),
):
    """用即将过期（或刚过期）的 token 换一个新 token（03-API §2）。

    只在宽限期内有效（`settings.refresh_grace_minutes`），过期太久必须重新登录 ——
    否则一个泄漏的旧 token 等于永久通行证。

    用户必须仍然 active：停用后即使拿着有效 token 也不能续期，
    否则"停用账号"会被一个后台页面无限续命。

    会话必须仍然有效（第十批 10.12）：**与普通鉴权用同一个判断**
    （`load_active_session`）。登出过、或改过密码的令牌，
    既不能访问接口，也不能拿来换新令牌 —— 否则"失效"只挡得住一半的路。

    续期**不新建会话**：一次登录及其后续刷新属于同一会话，`sid` 原样带过去。
    """
    if credentials is None or not credentials.credentials:
        raise AppError(ErrorCode.UNAUTHORIZED, "未登录", 401)

    payload = decode_access_token_allow_expired(
        credentials.credentials, grace_minutes=settings.refresh_grace_minutes
    )
    try:
        user_id = int(payload.get("sub", ""))
    except (TypeError, ValueError) as exc:
        raise AppError(ErrorCode.UNAUTHORIZED, "登录状态无效", 401) from exc

    user = await session.get(User, user_id)
    if user is None or user.status != "active":
        raise AppError(ErrorCode.UNAUTHORIZED, "账号不存在或已停用", 401)

    login_session = await load_active_session(session, payload.get(SID_CLAIM), user_id)
    login_session.last_refresh_at = datetime.now(UTC)

    token = create_access_token(
        user.id, {"name": user.name}, session_id=login_session.sid
    )
    await write_audit(
        session,
        operator_id=user.id,
        action="refresh",
        business_type="auth",
        business_id=user.id,
        after={"username": user.username},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(
        {"access_token": token, "token_type": "Bearer", "user": _user_brief(user)},
        "登录已续期",
    )


@router.post("/sso/wecom/callback")
async def wecom_sso_callback(payload: WeComSsoCallback, request: Request):
    """企微网页授权登录回调（03-API §2）：**通道关闭，一律不签发令牌**。

    ## 为什么关掉

    企微 SSO 里唯一可信的身份来源是**服务端**：后端拿一次性授权 code +
    corpsecret 调 `getuserinfo` 换 userid，或校验企业桥接的服务端签名 / 受众 /
    有效期 / 一次性断言。此前本接口信任请求体里自报的 `wecom_userid`，只拿它查一次
    本地用户就发 token、`state` 也只记进审计不校验 —— 任何知道同事企微工号的人都能
    换到该同事的 token，而且会留下一条 action='sso_login' 的**成功**审计，事后与真实
    登录无法区分。错误授权不能作为取舍接受，所以在企业凭据与服务端换取链路到位前，
    这里直接关闭这条通道（账号密码登录不受影响）。

    ## 关闭期间的行为

    明确报错，不静默失败、也不返回"登录成功但用户是空的"：拒绝理由按
    "凭据未配置 / 服务端换取能力未配置"区分，看到报错的人能直接判断缺什么、找谁配。
    接通后本接口只接受企微 OAuth 的入参（一次性 code + 一次性 state），
    且 state 必须一次性消费（防重放）后才允许签发。
    """
    if not settings.wecom_contact_ready:
        reason = "企微应用凭据未配置（缺 WECOM_CORP_ID / WECOM_CONTACT_SECRET）"
    else:
        # 凭据齐全也只够"调得动企微接口"：本服务里并没有拿 code 换 userid 的实现，
        # 也没有企业桥接的可验签密钥。此时若放行，等于又回到"自报身份即认证"。
        reason = (
            "服务端用授权 code 换取 userid（或校验企业桥接签名/受众/有效期/一次性断言）"
            "的能力未配置"
        )
    detail = (
        f"企微 SSO 未接通，本接口不签发令牌：{reason}。"
        "登录身份必须由服务端从企微取得，请求体里自报的 wecom_userid 不是凭证，一律不接受。"
        "请改用账号密码登录，或在服务端换取链路接通后重试。"
    )
    if not (payload.code and payload.state):
        # 入参形状单独提示：顺手带上 code 的调用方通常是按老前端（只报 userid）写的，
        # 让他一眼看出"不是我不认这个 code，而是整条链路还没接"。
        detail += "（本次请求未携带企微一次性 code/state）"
    logger.warning("企微 SSO 登录被拒绝（%s）：ip=%s", reason, client_ip(request))
    raise AppError(ErrorCode.EXTERNAL_ERROR, detail, 503)


@router.post("/logout")
async def logout(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """登出：**作废本次登录的服务端会话**（第十批 10.12）。

    此前这里只写一条审计 —— JWT 无状态、服务端没有可吊销的东西，
    所谓"登出"其实只是前端把 token 丢掉：那份凭据在过期前（默认 12 小时）
    照样能访问接口、照样能续期。泄漏出去就是一把收不回的钥匙。

    现在把**这一行会话**改成 revoked：同一个 token 再来（普通接口或
    `/auth/refresh`）一律 401，且**重启、多进程部署后依然如此** ——
    判断只看数据库那一行。

    只作废本次登录，不碰这个人的其它会话：手机退登不该把电脑上的
    正在用的那一个也踢掉。

    审计照旧写（会话时长、`session_revoked` 是否真的改动一并记下）。
    """
    revoked = await revoke_session(session, user.sid, reason=REASON_LOGOUT)
    await write_audit(
        session,
        operator_id=user.id,
        action="logout",
        business_type="auth",
        business_id=user.id,
        after={"username": user.username, "session_revoked": revoked},
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
