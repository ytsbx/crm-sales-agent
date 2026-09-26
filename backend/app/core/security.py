"""密码哈希与 JWT。

密码用 bcrypt；登录态用 JWT（与桌面「知识库」项目保持同一套做法，便于运维一致）。
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt

from app.core.config import settings
from app.core.errors import ErrorCode, AppError


def hash_password(raw: str) -> str:
    return bcrypt.hashpw(raw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(raw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(raw.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        # 库里存的不是合法 bcrypt 串（例如手工插的明文），一律视为验证失败。
        return False


def create_access_token(subject: str | int, extra: dict[str, Any] | None = None) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.access_token_expire_minutes)).timestamp()),
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
