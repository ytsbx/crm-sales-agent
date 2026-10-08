"""登录会话的读写：**唯一一处**「这份凭据还算不算数」的判断（第十批 10.12）。

## 为什么收敛到一个模块、一个函数

10.12 的要求里有一条是「普通鉴权和刷新接口使用同一失效判断」。两处各写一套
是这类需求最典型的坏结局：今天改了一处、明天改另一处，两边慢慢长出差异，
最后变成"某个接口还能用一把已经作废的令牌"。

所以这里只暴露一个判断入口 `load_active_session`，`core/deps.py`
（普通鉴权）与 `auth/router.py`（续期）都调它，谁都不许自己查表。

## 失效结果为什么重启/多进程后依然有效

判据**只在数据库里** —— 没有内存缓存、没有进程内集合。
吊销写库即生效，另一个进程下一个请求就会读到。
"""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.modules.auth.model import SESSION_ACTIVE, SESSION_REVOKED, LoginSession

#: 令牌里承载会话标识的 claim 名。
SID_CLAIM = "sid"

#: 吊销原因：用户主动登出。
REASON_LOGOUT = "logout"
#: 吊销原因：管理员改了 / 重置了这个账号的密码。
REASON_PASSWORD_CHANGE = "password_change"


async def open_session(session: AsyncSession, user_id: int) -> LoginSession:
    """登录成功时开一个会话，返回它（令牌里要带上它的 `sid`）。

    调用方负责提交事务：这样"登录成功"和"会话存在"是同一次提交，
    不会出现"审计里有一条登录、会话表里却没有"的半成品状态。
    """
    row = LoginSession(
        sid=uuid4().hex,
        user_id=user_id,
        status=SESSION_ACTIVE,
        last_refresh_at=datetime.now(UTC),
    )
    session.add(row)
    await session.flush()
    return row


async def load_active_session(
    session: AsyncSession, sid: str | None, user_id: int
) -> LoginSession:
    """取出**仍然有效**的会话；取不到就 401。

    这里是全项目唯一的凭据失效判断，`get_current_user` 与 `/auth/refresh` 共用。

    三种情况都按"凭据已失效"拒绝：

    - **令牌里没有 sid**：它无法被吊销，等于给"登出/改密后旧凭据失效"留一个后门。
      本机制上线前签发的旧令牌会因此失效，用户重新登一次即可 ——
      一次性成本，换的是「不存在不可吊销的凭据」。
    - **查不到这一行**：伪造的、或所属用户已被删除（外键级联带走）。
    - **行是 `revoked`**：登出过、或改过密码。

    另外也核一下 `user_id`：正常签不出"别人的 sid"，但这一步是白给的，
    留着可以挡住"拿 A 的会话号配 B 的 subject"这类构造。
    """
    if not sid:
        raise AppError(ErrorCode.UNAUTHORIZED, "登录状态无效，请重新登录", 401)

    row = (
        await session.execute(select(LoginSession).where(LoginSession.sid == sid))
    ).scalar_one_or_none()
    if row is None or row.user_id != user_id or row.status != SESSION_ACTIVE:
        raise AppError(ErrorCode.UNAUTHORIZED, "登录状态已失效，请重新登录", 401)
    return row


async def revoke_session(session: AsyncSession, sid: str | None, *, reason: str) -> bool:
    """吊销**一个**会话（登出）。返回是否真的改动了。

    只作废本会话，不碰这个人的其它登录：手机上退登，不该把电脑上
    正在用的那一个也踢掉。

    没找到、或本来就是吊销态 → 返回 False 而不是报错：登出是幂等动作，
    重复调用不该给使用者一个报错。
    """
    if not sid:
        return False

    row = (
        await session.execute(select(LoginSession).where(LoginSession.sid == sid))
    ).scalar_one_or_none()
    if row is None or row.status != SESSION_ACTIVE:
        return False

    row.status = SESSION_REVOKED
    row.revoked_reason = reason
    row.revoked_at = datetime.now(UTC)
    await session.flush()
    return True


async def revoke_user_sessions(session: AsyncSession, user_id: int, *, reason: str) -> int:
    """吊销某人的**全部**有效会话（改密码 / 重置密码），返回作废条数。

    调用方必须把它和"写新密码哈希"放在**同一个事务**里：要么
    「新密码生效 + 旧会话全废」一起发生，要么两件都不发生。
    否则会留下一个很别扭的中间态 —— 密码已经换了，别人的旧令牌还能进去。

    用一条 UPDATE 而不是"先查出来再逐条改"：判据（`status = active`）
    跟着写语句一起下去，并发下不会被同一条会话的另一次吊销挤成重复动作。
    """
    now = datetime.now(UTC)
    result = await session.execute(
        update(LoginSession)
        .where(LoginSession.user_id == user_id, LoginSession.status == SESSION_ACTIVE)
        .values(status=SESSION_REVOKED, revoked_reason=reason, revoked_at=now)
        .execution_options(synchronize_session=False)
    )
    await session.flush()
    return result.rowcount or 0
