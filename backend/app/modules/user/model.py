"""组织与权限：部门 / 用户 / 角色 / 权限（对齐 02-ER §3）。

补充说明：`roles.data_scope` 是 06-需求澄清清单第 1-7 条指出的缺口，
本实现把数据范围挂在角色上（self / department / department_and_sub / all）。
"""

from sqlalchemy import Boolean, false, BigInteger, ForeignKey, String, Table, Column
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, IdMixin, TimestampMixin

#: 角色状态里「生效」的那一个（第十批 10.10）。
#:
#: 放在 model 层而不是 service 层，是为了让所有需要「按有效角色取数」的模块
#: （通知、审批、用户等）都能从同一个地方 import，不必各自写一遍字面量，
#: 也不会因为 import 权限服务而绕出循环依赖。
#: 语义：`status != "active"` 的角色 —— 仍挂在人身上、但**不参与**权限、
#: 角色特例与数据范围的计算。
ROLE_ACTIVE = "active"


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
    initial_password_pending: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    mobile: Mapped[str | None] = mapped_column(String(32), nullable=True)
    email: Mapped[str | None] = mapped_column(String(128), nullable=True)
    department_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("departments.id"), nullable=True
    )
    wecom_userid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: 钉钉 userid（企业内部应用的成员编号）。有值 = 这个人可以用钉钉登录。
    #:
    #: 为什么与 wecom_userid 并列而不是另建映射表（2026-10-14）：绑定关系
    #: **只需要「一个人 ↔ 一个编号」**这一件事，没有企微那种"外部联系人待归一"
    #: 的中间态，所以不必为它开一张表。多余的中间表只会多一处要维护的一致性。
    #:
    #: 长度给 128：钉钉 userid 官方口径"最长 64 字符"，但手机号格式的 userid
    #: 会带上企业后缀，实际见过更长的（与 wecom_users.wecom_userid 取同一长度）。
    dingtalk_userid: Mapped[str | None] = mapped_column(String(128), nullable=True)
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
