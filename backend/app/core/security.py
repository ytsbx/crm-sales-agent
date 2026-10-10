"""密码哈希与 JWT。

密码用 bcrypt；登录态用 JWT（与桌面「知识库」项目保持同一套做法，便于运维一致）。
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
from uuid import uuid4

import jwt

from app.core.config import settings
from app.core.errors import ErrorCode, AppError


#: bcrypt 的硬限制：**72 字节**（不是 72 个字符）。
#:
#: ⚠️ 这个上限**不随 bcrypt 版本变**，但行为变了：bcrypt < 4 会**静默截断**到 72 字节，
#: bcrypt >= 4（本环境 5.0.0）直接抛 `ValueError`。实测：
#:   72 个 ASCII  → 通过
#:   73 个 ASCII  → ValueError
#:   25 个中文（75 字节）→ ValueError（**字符数只有 25**，所以按"字符数"校验是错的）
#: 从前入参只写了 `min_length=6`，于是"合法入参"能把创建/重置用户打成 500（C5-06）。
#: **不静默截断**：截断会让"密码前 72 字节相同的两个不同密码"变成同一个密码，
#: 那是安全问题，不是便利问题 —— 宁可明确报参数错。
BCRYPT_MAX_PASSWORD_BYTES = 72


def password_too_long_bytes(raw: str) -> int | None:
    """超长时返回实际字节数，否则 None。给入参校验用。"""
    size = len(raw.encode("utf-8"))
    return size if size > BCRYPT_MAX_PASSWORD_BYTES else None


def hash_password(raw: str) -> str:
    """哈希密码。

    入参层已经拦了超长（`user/schema.py` 的校验器），这里再兜一道：
    **别的地方漏校验时也给出人话错误，而不是让 bcrypt 的 ValueError 冒成 500**。
    """
    size = password_too_long_bytes(raw)
    if size is not None:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"密码过长（{size} 字节，最多 {BCRYPT_MAX_PASSWORD_BYTES} 字节；"
            f"一个中文按 3 字节算）",
            422,
        )
    return bcrypt.hashpw(raw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(raw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(raw.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        # 库里存的不是合法 bcrypt 串（例如手工插的明文），一律视为验证失败。
        return False


def create_access_token(
    subject: str | int,
    extra: dict[str, Any] | None = None,
    *,
    session_id: str,
) -> str:
    """签发访问令牌。

    `session_id` 是**必填**的：令牌必须挂在一个服务端可吊销的登录会话上
    （第十批 10.12）。把它做成关键字必填，是为了让"忘了带会话标识"这件事
    在调用处就报错，而不是签出一张天生无法吊销的通行证 ——
    鉴权侧会因为缺 `sid` 直接 401，那种失败要等到使用者登录不上才会被发现。
    """
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.access_token_expire_minutes)).timestamp()),
        # 随机 jti：没有它，同一秒内签出的 token 完全相同，
        # /auth/refresh 会"续期了个寂寞"（新 token == 旧 token）
        "jti": uuid4().hex,
        # 登录会话标识：鉴权与续期都拿它去查"这次登录还作不作数"
        "sid": session_id,
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError as exc:
        raise AppError(ErrorCode.TOKEN_EXPIRED, "登录已过期，请重新登录", 401) from exc
    except jwt.PyJWTError as exc:
        raise AppError(ErrorCode.UNAUTHORIZED, "登录状态无效", 401) from exc


def decode_access_token_allow_expired(token: str, *, grace_minutes: int) -> dict[str, Any]:
    """续期专用：允许已过期但在宽限期内的 token。

    为什么需要单独的入口：`/auth/refresh` 的价值就在于"过期了还能救一下"。
    如果复用 `decode_access_token`，一过期就 401，那这个接口只能给
    还有效的 token 续期 —— 前端正常操作时根本用不到它。

    签名错误、算法不符仍然一律拒绝；只对"仅仅过期"放宽。
    """
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError:
        # 过期了，但签名是好的 —— 取出 exp 判断是否在宽限期内
        try:
            payload = jwt.decode(
                token,
                settings.jwt_secret,
                algorithms=[settings.jwt_algorithm],
                options={"verify_exp": False},
            )
        except jwt.PyJWTError as exc:
            raise AppError(ErrorCode.UNAUTHORIZED, "登录状态无效", 401) from exc
        expired_at = datetime.fromtimestamp(int(payload.get("exp", 0)), tz=UTC)
        if datetime.now(UTC) - expired_at > timedelta(minutes=grace_minutes):
            raise AppError(
                ErrorCode.TOKEN_EXPIRED,
                f"登录已过期超过 {grace_minutes} 分钟，请重新登录",
                401,
            )
        return payload
    except jwt.PyJWTError as exc:
        raise AppError(ErrorCode.UNAUTHORIZED, "登录状态无效", 401) from exc
