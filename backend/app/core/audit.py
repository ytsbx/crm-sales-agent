"""审计日志：高风险动作必须留痕（05-TECH §25）。"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, DateTime, String, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, IdMixin, JSONType


class AuditLog(Base, IdMixin):
    __tablename__ = "audit_logs"

    operator_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="WEB")  # WEB/API/AGENT/INTEGRATION/SYSTEM
    business_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    business_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    action: Mapped[str] = mapped_column(String(64))
    before_data: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    after_data: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


def json_safe(value: Any) -> Any:
    """把审计数据转成能进 JSONB 的形状。

    踩过的坑：报价单的 valid_until 是 date 类型，直接塞进 JSON 列会在
    flush 时抛 "Object of type date is not JSON serializable"，
    而且报错出现在 commit 阶段，看调用栈很难一眼定位。
    """
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


async def write_audit(
    session: AsyncSession,
    *,
    operator_id: int | None,
    action: str,
    business_type: str | None = None,
    business_id: int | None = None,
    before: Any = None,
    after: Any = None,
    source: str = "WEB",
    ip: str | None = None,
) -> None:
    """写一条审计记录。不 commit，由调用方的事务统一提交。"""
    session.add(
        AuditLog(
            operator_id=operator_id,
            source=source,
            business_type=business_type,
            business_id=business_id,
            action=action,
            before_data=json_safe(before),
            after_data=json_safe(after),
            ip=ip,
        )
    )
