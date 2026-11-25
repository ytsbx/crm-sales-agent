"""审计日志：高风险动作必须留痕（05-TECH §25）。"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, DateTime, Index, String, desc, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, IdMixin, JSONType


class AuditLog(Base, IdMixin):
    __tablename__ = "audit_logs"
    #: 按「业务类型 + 对象 id」反查用（回收站要看"这条是谁删的"）。
    #: `id DESC` 是配合「取最新一条」的写法 —— 「删掉 → 恢复 → 再删」之后
    #: 要显示**本次**那个人，不能翻出第一次的旧留痕。建法见迁移 `a7c1e5b9d3f2`。
    #: 这张表是全项目长得最快的一张，没有它每次打开回收站都是全表扫描。
    __table_args__ = (
        Index("ix_audit_logs_business", "business_type", "business_id", desc("id")),
    )

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


def json_safe(value: Any, *, decimal_as_str: bool | None = True) -> Any:
    """把审计数据转成能进 JSONB 的形状。

    踩过的坑：报价单的 valid_until 是 date 类型，直接塞进 JSON 列会在
    flush 时抛 "Object of type date is not JSON serializable"，
    而且报错出现在 commit 阶段，看调用栈很难一眼定位。

    Decimal 默认按原值存字符串（float→Decimal 专项批1，B12-01）：转 float
    会在 16 位边界差一分，审计留痕就对不上账；字符串可精确复算，与 bizdoc
    快照先例一致。`decimal_as_str=None` 保留 Decimal 原值，供精确渲染器
    exact_json_dumps 使用（Q4 决策2：幂等快照存精确原文，不再经 float）。
    **float 复印模式（decimal_as_str=False）已拆除**（2026-11-24 主人批准）：
    Q4 后全 app/测试零调用方，属没人按的老按钮；再有人传 False 显式报错，
    而不是静默走丢精度的老路。
    """
    if isinstance(value, dict):
        return {key: json_safe(item, decimal_as_str=decimal_as_str) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item, decimal_as_str=decimal_as_str) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        if decimal_as_str is None:
            return value
        if not decimal_as_str:
            raise ValueError(
                "json_safe 的 float 复印模式（decimal_as_str=False）已拆除："
                "转 float 会在 16 位边界差一分，审计留痕必须原文照抄"
            )
        return str(value)
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
