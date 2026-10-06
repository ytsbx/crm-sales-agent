"""请求幂等：同一个请求键重复提交只生效一次，并返回**同一份**结果。

## 为什么需要它（第八批 8.15 / 第七批 7.9 / 第八批 8.9 的共同底座）

「服务端已经提交成功、客户端却因为弱网/超时没收到响应」时，用户会再点一次。
没有稳定请求键的实现只能按每次请求创建 —— 那一次点击就变成两条客户/两条报价，
而当事人根本不知道自己造了重复数据；事后清理还要人工比对。

已有的正确做法在合同生成里（`request_key` 列 + 唯一约束）。这里把它抽成
**一张通用表 + 一个助手**，而不是每个模块各自加一列：
- 一张表只需要一次迁移，新增模块不用再动表结构；
- "同键不同内容要报冲突""并发同键只许一个成功"这些边界只写一遍，
  各写一份必然有一份漏掉，而漏掉的那一份就是重复数据。

## 语义（三条，都要满足）

1. **同键同内容**：第二次直接返回第一次存下的结果，`replayed=True`；
2. **同键不同内容**：报冲突（40902），不静默按新内容再创建一次 ——
   那等于把"重复提交"变成"悄悄改了内容"；
3. **同键并发**：数据库唯一约束只让一个请求拿到"处理中"的占位，
   另一个明确报"正在处理中，请稍后重试"，而不是两个都建。

请求键**按（用户, 动作）分区**：不同用户、不同接口用同一个字符串互不影响，
前端的 uuid 不需要全局唯一。

## 失败就释放

业务校验失败时把占位删掉：用户改完表单会**带着同一个键**重试
（前端一个表单一个键），这时内容必然不同 —— 若不释放，第二次提交会撞
"同键不同内容"，把正常改错重填误判成冲突。只有**成功**的结果才值得记忆。
"""

import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, Index, String, UniqueConstraint, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, IdMixin, JSONType
from app.core.errors import AppError, ErrorCode

#: 键长上限：前端 uuid(36) 或"表单名+时间戳"都够；超长直接拒绝，
#: 免得有人拿它塞请求体进库。
MAX_KEY_LENGTH = 128
#: 动作名长度（例如 "customer:create"）
MAX_ACTION_LENGTH = 64


class RequestKey(Base, IdMixin):
    """一次请求的幂等记录。见模块说明。"""

    __tablename__ = "request_keys"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "action", "request_key", name="uq_request_key_scope"
        ),
        Index("ix_request_keys_created", "created_at"),
    )

    user_id: Mapped[int] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(MAX_ACTION_LENGTH))
    request_key: Mapped[str] = mapped_column(String(MAX_KEY_LENGTH))
    #: 请求内容的指纹：用来区分"同一次提交重发"与"同一把键换了内容"
    request_hash: Mapped[str] = mapped_column(String(64))
    #: in_flight = 占位中；done = 已完成、可回放
    status: Mapped[str] = mapped_column(String(16), default="in_flight")
    #: 成功后的响应体（原样回放，保证"同键返回同一份结果"）
    result_payload: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    #: 成功后指向的业务对象，便于排查"这个键建了哪条数据"
    result_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


def payload_hash(payload) -> str:
    """请求内容指纹：**排序键**后序列化，避免字段顺序不同被当成不同内容。

    只取业务字段（调用方自己传 dict）：不要把 request_key 本身算进去。
    """
    canonical = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class Reservation:
    """占位结果。`replay_payload` 非空表示"这次不用干活，直接回放"。"""

    def __init__(
        self,
        *,
        row: RequestKey | None,
        replay_payload: dict | None = None,
        replay_id: int | None = None,
    ) -> None:
        self.row = row
        self.replay_payload = replay_payload
        self.replay_id = replay_id

    @property
    def should_replay(self) -> bool:
        return self.replay_payload is not None


async def reserve(
    session: AsyncSession,
    *,
    user_id: int,
    action: str,
    request_key: str,
    payload,
    result_type: str | None = None,
) -> Reservation:
    """占位或回放。

    - 命中已完成且内容一致 → 回放；
    - 命中但内容不同 → 40902 冲突；
    - 命中且仍在处理中 → 40901 冲突（"正在处理中"）；
    - 未命中 → 插入占位；插入撞唯一约束（并发）→ 重新判定上面三种。
    """
    key = (request_key or "").strip()
    if not key:
        raise AppError(ErrorCode.PARAM_ERROR, "请求键不能为空", 422)
    if len(key) > MAX_KEY_LENGTH:
        raise AppError(
            ErrorCode.PARAM_ERROR, f"请求键长度不能超过 {MAX_KEY_LENGTH}", 422
        )
    if len(action) > MAX_ACTION_LENGTH:
        raise AppError(ErrorCode.PARAM_ERROR, "请求动作名过长", 422)

    digest = payload_hash(payload)
    stmt = select(RequestKey).where(
        RequestKey.user_id == user_id,
        RequestKey.action == action,
        RequestKey.request_key == key,
    )
    existing = (await session.execute(stmt)).scalars().first()
    if existing is not None:
        return _judge(existing, digest)

    row = RequestKey(
        user_id=user_id,
        action=action,
        request_key=key,
        request_hash=digest,
        status="in_flight",
        result_type=result_type,
        created_at=datetime.now(UTC),
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError:
        # 并发：另一个请求刚插进去。回到 savepoint 之后重新读一次再判定。
        await session.rollback()
        existing = (await session.execute(stmt)).scalars().first()
        if existing is None:  # pragma: no cover - 只可能在极端时序下出现
            raise AppError(
                ErrorCode.VERSION_CONFLICT, "同一请求正在处理中，请稍后重试", 409
            ) from None
        return _judge(existing, digest)
    return Reservation(row=row)


def _judge(existing: RequestKey, digest: str) -> Reservation:
    if existing.request_hash != digest:
        raise AppError(
            ErrorCode.VERSION_CONFLICT,
            "这次提交的请求键与之前那次的内容不一致：同一把键只能对应同一份内容。"
            "如果是改完表单重新提交，请刷新页面重新打开表单后再提交",
            409,
        )
    if existing.status == "done":
        return Reservation(
            row=existing,
            replay_payload=existing.result_payload,
            replay_id=existing.result_id,
        )
    raise AppError(
        ErrorCode.DUPLICATE,
        "这次提交正在处理中（或上一次没有正常结束），请稍后重试，不要重复点击",
        409,
    )


async def complete(
    session: AsyncSession,
    reservation: Reservation,
    *,
    result_payload: dict,
    result_id: int | None = None,
) -> None:
    """标记完成并记住响应体，之后同键请求直接回放这份响应。

    **必须在这里做 JSON 安全化**，而不是指望每个调用方自己包：序列化出来的
    业务响应里天然带 `datetime` / `date` / `Decimal`，直接塞进 JSON 列会在
    `commit` 阶段抛 `Object of type datetime is not JSON serializable`
    —— 报错点在提交，看调用栈很难定位；而且"创建成功却回滚"最糟：
    用户以为没建、重试又拿不到回放。放在底座上，所有调用方一次受保护。
    """
    from app.core.audit import json_safe

    if reservation.row is None:  # pragma: no cover - 防御性
        return
    reservation.row.status = "done"
    reservation.row.result_payload = json_safe(result_payload)
    reservation.row.result_id = result_id
    await session.flush()


async def release(session: AsyncSession, reservation: Reservation) -> None:
    """业务失败时释放占位。

    占位行可能已经被上层 `rollback()` 连带回滚掉了，所以这里要判存在性；
    删不掉也不能让"释放失败"盖住真正的业务错误。
    """
    if reservation.row is None:  # pragma: no cover - 防御性
        return
    try:
        await session.delete(await session.get(RequestKey, reservation.row.id))
        await session.flush()
    except Exception:  # noqa: BLE001 —— 释放是补偿动作，不能顶掉原始错误
        await session.rollback()


def request_key_from(request, payload_key: str | None = None) -> str | None:
    """从请求里取幂等键：优先 body 字段，其次 `X-Request-Key` 头。

    两种入口都留：前端表单习惯放 body；通用 HTTP 客户端（脚本、移动端重试）
    放头更自然。body 里传了空白串时**继续看头**，不把"填了个空"当成"明确不给"。
    **都不给就是 None**，调用方按"不做幂等"处理并在响应里说明 ——
    不假装有幂等（那会让人以为重试安全）。
    """
    body_key = (payload_key or "").strip()
    if body_key:
        return body_key
    header = request.headers.get("x-request-key") if request is not None else None
    return (header or "").strip() or None
