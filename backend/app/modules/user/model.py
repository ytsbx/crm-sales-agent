"""组织与权限：部门 / 用户 / 角色 / 权限（对齐 02-ER §3）。

补充说明：`roles.data_scope` 是 06-需求澄清清单第 1-7 条指出的缺口，
本实现把数据范围挂在角色上（self / department / department_and_sub / all）。
"""

from sqlalchemy import BigInteger, ForeignKey, String, Table, Column
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, IdMixin, TimestampMixin


class Department(Base, IdMixin, TimestampMixin):
    __tablename__ = "departments"

    parent_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    name: Mapped[str] = mapped_column(String(128))
    wecom_department_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active")


class User(Base, IdMixin, TimestampMixin):
    __tablename__ = "users"

    name: Mapped[str] = mapped_column(String(64))
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    mobile: Mapped[str | None] = mapped_column(String(32), nullable=True)
    email: Mapped[str | None] = mapped_column(String(128), nullable=True)
    department_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("departments.id"), nullable=True
    )
    wecom_userid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active")


class Role(Base, IdMixin, TimestampMixin):
    __tablename__ = "roles"

    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 数据范围：self / department / department_and_sub / all
    data_scope: Mapped[str] = mapped_column(String(32), default="self")
    status: Mapped[str] = mapped_column(String(32), default="active")


class Permission(Base, IdMixin):
    __tablename__ = "permissions"

    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(64))
    resource: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action: Mapped[str | None] = mapped_column(String(32), nullable=True)


user_roles = Table(
    "user_roles",
    Base.metadata,
    Column("user_id", BigInteger, ForeignKey("users.id"), primary_key=True),
    Column("role_id", BigInteger, ForeignKey("roles.id"), primary_key=True),
)

role_permissions = Table(
    "role_permissions",
    Base.metadata,
    Column("role_id", BigInteger, ForeignKey("roles.id"), primary_key=True),
    Column("permission_id", BigInteger, ForeignKey("permissions.id"), primary_key=True),
)

# 兼容 02-ER 文档中的命名，供 ORM 关系查询使用
UserRole = user_roles
RolePermission = role_permissions
