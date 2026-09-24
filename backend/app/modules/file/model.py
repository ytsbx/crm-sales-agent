"""文件与业务附件。

分层与文档一致：
- `files` 是文件本身（存储位置 + 元信息）；
- `business_files` 是"这个文件挂在哪个业务对象上"，一个文件可以被多个对象引用。

当前存储后端是本地磁盘，但字段（storage_provider + object_key）按对象存储设计，
换成 MinIO/OSS 时只需实现一个新的存储适配器，业务表不用动。
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, IdMixin


class FileRecord(Base, IdMixin):
    __tablename__ = "files"

    storage_provider: Mapped[str] = mapped_column(String(16), default="local")
    object_key: Mapped[str] = mapped_column(String(255))
    file_name: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    uploaded_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(), server_default=func.now(),
        nullable=False,
    )


class BusinessFile(Base, IdMixin):
    __tablename__ = "business_files"
    __table_args__ = (
        Index("ix_business_files_target", "business_type", "business_id"),
    )

    business_type: Mapped[str] = mapped_column(String(32))
    business_id: Mapped[int] = mapped_column(BigInteger)
    file_id: Mapped[int] = mapped_column(BigInteger)
    category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(), server_default=func.now(),
        nullable=False,
    )
