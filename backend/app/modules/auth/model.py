"""登录会话：让「登出 / 改密码后旧凭据立刻失效」成为可能（第十批 10.12）。

## 为什么需要一张表

JWT 是**无状态**的：签名对、没过期，服务端就一路放行。于是此前：

- `POST /auth/logout` 只写一条审计，真正"登出"靠前端把 token 丢掉；
- 改（或重置）密码只换 `password_hash`，**已经发出去的 token 一张都没作废**。

也就等于：任何一次泄漏（浏览器缓存、日志、截图里的调试面板）在 token 过期前
（默认 12 小时）都是有效通行证，服务端**没有任何可吊销的东西**。

这张表把"一次登录"变成一个**服务端可见、可作废**的对象：
每一行 = 一次登录，令牌里带 `sid` 指向它。
"这份凭据还算不算数"只有一个判据 —— 这一行是不是 `active`（见 `session.py`）。

## 为什么用随机串当会话标识、而不是自增 id

自增 id 可猜、可枚举。虽然光有 sid 没有签名也拿不到什么，但没有理由把
别人的会话序号暴露在令牌里；随机串顺带让"两个会话碰巧同号"不可能发生。

## 与审计的关系

`audit_logs` 记的是"谁在什么时候做了什么"（含 login / logout / refresh），
是流水账；本表记的是"这次登录**现在还作不作数**"，是状态。
两者刻意不合并：流水账要留档、不能随会话作废而消失。
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, IdMixin, TimestampMixin

#: 会话仍然有效：令牌可以用它访问接口、也可以拿它续期。
SESSION_ACTIVE = "active"
#: 会话已被吊销（登出 / 改密码 / 重置密码）。终态：不再恢复。
SESSION_REVOKED = "revoked"


class LoginSession(Base, IdMixin, TimestampMixin):
    __tablename__ = "login_sessions"

    # 唯一约束与索引都在这里**显式声明名字**，而不是在列上写 unique=True / index=True：
    # 后者的名字由 SQLAlchemy 自己拼（`ix_login_sessions_user_id`），
    # 与迁移里手写的可读名字对不上，`alembic check` 会一直把这张新表报成"有漂移"
    # —— 真出现结构改动时反而看不出哪一条是真的。
    __table_args__ = (
        UniqueConstraint("sid", name="uq_login_sessions_sid"),
        Index("ix_login_sessions_user", "user_id"),
    )

    #: 令牌里的 `sid`。（随机 32 位十六进制串）
    sid: Mapped[str] = mapped_column(String(64))
    #: 外键 `ondelete="CASCADE"`：删用户时把他的会话一并带走，
    #: 不留"指向已删用户的会话"这种要定期清扫的孤儿行。
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(32), default=SESSION_ACTIVE)
    #: 为什么被吊销：logout / password_change。
    #: 排查"人怎么突然掉线了"时要看它 —— 只记一个"已失效"等于没说。
    revoked_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: 「最近一次凭它换到新凭据」的时间：登录那一刻写入，之后每次续期更新。
    #:
    #: 刻意**没有**做成"每次请求都更新"：那等于给每个读请求加一条写，
    #: 而它只用于排查与验证"续期属于同一会话"，不需要那么准。
    last_refresh_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
