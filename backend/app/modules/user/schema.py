"""用户 / 部门 / 角色入参（03-API §3 / §4 / §5）。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from app.core.security import BCRYPT_MAX_PASSWORD_BYTES, password_too_long_bytes
from app.core.patch_schema import PatchModel


# 数据范围枚举，与 roles.data_scope 的取值一致（见 02-ER §3 的补充说明）
DATA_SCOPES = ("self", "department", "department_and_sub", "all")

#: 供 Pydantic 做枚举校验用（写错值直接 400，不等到查询时才行为诡异）
DataScope = Literal["self", "department", "department_and_sub", "all"]


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    username: str
    password: str = Field(min_length=6)
    """密码至少 6 位；存库前会 bcrypt 哈希，接口永不回显。"""

    @field_validator("password")
    @classmethod
    def _password_within_bcrypt(cls, value: str) -> str:
        """上限按**字节**算，不是字符数（C5-06，2026-10-10 修）。

        bcrypt 硬限制 72 字节：25 个中文只有 25 个字符，却是 75 字节 —— 按字符数
        校验永远拦不住它。从前这里只写 `min_length=6`，于是 73 个 ASCII 或 25 个
        中文这种"看起来合法"的入参会让 `bcrypt.hashpw` 抛 `ValueError` 冒成 500。
        """
        size = password_too_long_bytes(value)
        if size is not None:
            raise ValueError(
                f"密码过长（{size} 字节，最多 {BCRYPT_MAX_PASSWORD_BYTES} 字节；"
                f"一个中文按 3 字节算）"
            )
        return value
    mobile: str | None = None
    email: str | None = None
    department_id: int | None = None
    wecom_userid: str | None = None
    role_ids: list[int] = []
    """创建时可直接授权角色。"""


class UserUpdate(PatchModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    mobile: str | None = None
    email: str | None = None
    department_id: int | None = None
    wecom_userid: str | None = None
    password: str | None = Field(default=None, min_length=6)
    """传了才重置密码；不传保持原密码。"""

    @field_validator("password")
    @classmethod
    def _password_within_bcrypt(cls, value: str | None) -> str | None:
        """重置密码与创建同一口径（C5-06）：都是喂给 bcrypt，上限自然一样。"""
        if value is None:
            return None
        size = password_too_long_bytes(value)
        if size is not None:
            raise ValueError(
                f"密码过长（{size} 字节，最多 {BCRYPT_MAX_PASSWORD_BYTES} 字节；"
                f"一个中文按 3 字节算）"
            )
        return value


class UserRolesUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    role_ids: list[int]


class DepartmentCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    parent_id: int | None = None
    wecom_department_id: str | None = None


class DepartmentUpdate(PatchModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    parent_id: int | None = None
    wecom_department_id: str | None = None
    status: str | None = None


class RoleCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    code: str
    name: str
    description: str | None = None
    data_scope: str = "self"
    permission_codes: list[str] = []
    """按权限码授权，比传 id 直观（权限码是稳定的，id 会随重建变化）。"""


class RolePermissionUpdate(BaseModel):
    """覆盖角色的权限集合（03-API §5 PUT /roles/{id}/permissions）。

    按权限码而不是 id：权限码稳定，重建库后 id 会变，权限码不会。
    """

    model_config = ConfigDict(extra="ignore")

    permission_codes: list[str]


class RoleDataScopeUpdate(BaseModel):
    """设置角色的数据范围（03-API §5 PUT /roles/{id}/data-scope）。"""

    model_config = ConfigDict(extra="ignore")

    data_scope: DataScope


class RoleUpdate(PatchModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    description: str | None = None
    data_scope: str | None = None
    status: str | None = None
    permission_codes: list[str] | None = None
    """传了才覆盖权限集合；不传保持原权限。"""
