"""外部系统映射与集成日志。

`external_mappings` 让「每接一个系统就往业务表加一堆字段」这件事不再发生：
ERP/MES 的订单号、物料号统一记在这里，业务表只保留自己的主键。
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, IdMixin, JSONType


class ExternalMapping(Base, IdMixin):
    __tablename__ = "external_mappings"
    __table_args__ = (
        Index("ix_external_mappings_lookup", "system_type", "business_type", "internal_id"),
        Index("ix_external_mappings_external", "system_type", "external_id"),
    )

    system_type: Mapped[str] = mapped_column(String(32))  # ERP / MES / WECOM / LOGISTICS
    business_type: Mapped[str] = mapped_column(String(32))  # order / sku / customer
    internal_id: Mapped[int] = mapped_column(BigInteger)
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    external_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class IntegrationLog(Base, IdMixin):
    __tablename__ = "integration_logs"

    integration_type: Mapped[str] = mapped_column(String(32))
    #: 厂商/适配器名（如「聚水潭」），**只用于显示**。
    #: 与 integration_type 分开是刻意的：类型是稳定的口径（查询按它），
    #: 厂商名会随接入的 ERP 变。此前只有 integration_type，写入存的是厂商名、
    #: 查询却按 '%ERP%' 匹配 —— 两边靠约定对齐，结果聚水潭的日志一条都查不到。
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    direction: Mapped[str] = mapped_column(String(16))  # outbound / inbound
    business_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    business_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    request_data: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    response_data: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="success")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
