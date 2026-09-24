"""ORM 基类与通用字段。

约定：
- 主键统一 BIGINT 自增（与 03-API 文档里的 1001 这类示例编号一致）；
- 关键业务表统一带 created_at / updated_at；
- 需要软删除的表自己声明 deleted_at，不在基类里强加。
"""

from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, JSON, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# 可移植的 JSON 列：PostgreSQL 下用 JSONB（可建 GIN 索引），其它库退回普通 JSON。
JSONType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class IdMixin:
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)


class TimestampMixin:
    """创建与更新时间。

    注意：这里同时给「Python 侧默认值」和「数据库侧默认值」。
    只用数据库默认值（server_default）时，SQLAlchemy 在 flush 之后会把该字段标记为过期，
    下一次读取会触发同步 IO —— 在异步会话里就是 MissingGreenlet 报错。
    加上 Python 侧默认值后，ORM 自己知道值，不再回查。
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )
