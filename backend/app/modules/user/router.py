"""用户 / 部门 / 角色接口（03-API §3 / §4 / §5）。

共 20 个路径，其中读接口 5 个、写接口 15 个。全部要求 `user:manage`
（`GET /users` 例外：分配客户时也要能选人，所以对 `customer:assign` 放开）。

注册顺序：`/departments/tree` 必须排在 `/departments/{dept_id}` 之前，
否则会被动态路由抢先匹配（项目里这个坑踩过三次了）。
"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok, page_data, paginate
from app.modules.user import service as svc
from app.modules.user.model import User
from app.modules.user.schema import (
    DepartmentCreate,
    DepartmentUpdate,
    RoleCreate,
    RoleDataScopeUpdate,
    RolePermissionUpdate,
    RoleUpdate,
    UserCreate,
    UserRolesUpdate,
    UserUpdate,
)

#: 数据范围的中文名，给界面直接显示（与 02-ER §3 的四个取值一一对应）
DATA_SCOPE_LABELS = {
    "self": "仅本人",
    "department": "本部门",
    "department_and_sub": "本部门及下级",
    "all": "全部",
}

router = APIRouter(tags=["System"])


# ================================================================== 用户

@router.get("/users")
async def list_users(
    keyword: str | None = None,
    status: str | None = None,
    department_id: int | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    # 有分配客户权限的人也需要选人，所以这里放开给 customer:assign
    _: CurrentUser = Depends(require_permission("user:manage", "customer:assign")),
    session: AsyncSession = Depends(get_db),
):
    stmt = svc.base_user_query()
    if keyword:
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(User.name.ilike(like) | User.username.ilike(like))
    if status:
        stmt = stmt.where(User.status == status)
    if department_id:
        stmt = stmt.where(User.department_id == department_id)

    rows, total = await paginate(session, stmt.order_by(User.id.asc()), page, page_size)
    dept_map = await svc.department_names(session, [u.department_id for u in rows])
    role_map = await svc.roles_of_users(session, [u.id for u in rows])
    items = [
        svc.serialize_user(
            user,
            department_name=dept_map.get(user.department_id) if user.department_id else None,
            roles=role_map.get(user.id, []),
        )
        for user in rows
    ]
    return ok(page_data(items, total, page, page_size))


@router.post("/users")
async def create_user(
    payload: UserCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("user:manage")),
    session: AsyncSession = Depends(get_db),
):
    created = await svc.create_user(session, payload.model_dump())
    dept_map = await svc.department_names(session, [created.department_id])
    roles = await svc.roles_of_user(session, created.id)
    after = svc.serialize_user(
        created,
        department_name=dept_map.get(created.department_id) if created.department_id else None,
        roles=[{"id": r.id, "code": r.code, "name": r.name, "data_scope": r.data_scope, "status": r.status} for r in roles],
    )
    await write_audit(
        session,
        operator_id=user.id,
        action="create",
        business_type="user",
        business_id=created.id,
        after=after,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(after, "用户已创建")


@router.get("/users/{user_id}")
async def get_user(
    user_id: int,
    _: CurrentUser = Depends(require_permission("user:manage")),
    session: AsyncSession = Depends(get_db),
):
    target = await svc.get_user_or_404(session, user_id)
    dept_map = await svc.department_names(session, [target.department_id])
    roles = await svc.roles_of_user(session, target.id)
    return ok(
        svc.serialize_user(
            target,
            department_name=dept_map.get(target.department_id) if target.department_id else None,
            roles=[{"id": r.id, "code": r.code, "name": r.name, "data_scope": r.data_scope, "status": r.status} for r in roles],
        )
    )


@router.patch("/users/{user_id}")
async def update_user(
    user_id: int,
    payload: UserUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("user:manage")),
    session: AsyncSession = Depends(get_db),
):
    target = await svc.get_user_or_404(session, user_id)
    before = svc.serialize_user(target)
    data = payload.model_dump(exclude_unset=True)
    await svc.update_user(session, target, data)

    after = svc.serialize_user(target)
    # 密码变更不写进审计内容（只记"改了密码"这一事实）
    if data.get("password"):
        after = {**after, "password_changed": True}
    await write_audit(
        session,
        operator_id=user.id,
        action="update",
        business_type="user",
        business_id=target.id,
        before=before,
        after=after,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(after, "已保存")


@router.post("/users/{user_id}/enable")
async def enable_user(
    user_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("user:manage")),
    session: AsyncSession = Depends(get_db),
):
    target = await svc.get_user_or_404(session, user_id)
    before_status = target.status
    await svc.set_user_status(session, target, status="active", operator_id=user.id)
    await write_audit(
        session,
        operator_id=user.id,
        action="enable",
        business_type="user",
        business_id=target.id,
        before={"status": before_status},
        after={"status": "active"},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_user(target), "账号已启用")


@router.post("/users/{user_id}/disable")
async def disable_user(
    user_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("user:manage")),
    session: AsyncSession = Depends(get_db),
):
    target = await svc.get_user_or_404(session, user_id)
    before_status = target.status
    await svc.set_user_status(session, target, status="disabled", operator_id=user.id)
    await write_audit(
        session,
        operator_id=user.id,
        action="disable",
        business_type="user",
        business_id=target.id,
        before={"status": before_status},
        after={"status": "disabled"},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_user(target), "账号已停用")


@router.get("/users/{user_id}/roles")
async def get_user_roles(
    user_id: int,
    _: CurrentUser = Depends(require_permission("user:manage")),
    session: AsyncSession = Depends(get_db),
):
    await svc.get_user_or_404(session, user_id)
    roles = await svc.roles_of_user(session, user_id)
    return ok(
        [
            {"id": r.id, "code": r.code, "name": r.name, "data_scope": r.data_scope, "status": r.status}
            for r in roles
        ]
    )


@router.put("/users/{user_id}/roles")
async def set_user_roles(
    user_id: int,
    payload: UserRolesUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("user:manage")),
    session: AsyncSession = Depends(get_db),
):
    target = await svc.get_user_or_404(session, user_id)
    before = [r.code for r in await svc.roles_of_user(session, target.id)]
    roles = await svc.set_user_roles(session, target, payload.role_ids)
    after = [r.code for r in roles]
    await write_audit(
        session,
        operator_id=user.id,
        action="set_roles",
        business_type="user",
        business_id=target.id,
        before={"roles": before},
        after={"roles": after},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(
        [
            {"id": r.id, "code": r.code, "name": r.name, "data_scope": r.data_scope, "status": r.status}
            for r in roles
        ],
        "角色已更新",
    )


@router.get("/users/{user_id}/data-scope")
async def get_user_data_scope(
    user_id: int,
    _: CurrentUser = Depends(require_permission("user:manage")),
    session: AsyncSession = Depends(get_db),
):
    """该用户实际生效的数据范围（多角色取最大）。"""
    target = await svc.get_user_or_404(session, user_id)
    roles = await svc.roles_of_user(session, user_id)
    dept_map = await svc.department_names(session, [target.department_id])
    return ok(
        {
            "user_id": target.id,
            "department_id": target.department_id,
            "department": dept_map.get(target.department_id) if target.department_id else None,
            "data_scope": svc.resolve_data_scope(roles),
            "roles": [
                {"code": r.code, "name": r.name, "data_scope": r.data_scope, "status": r.status} for r in roles
            ],
        }
    )


# ================================================================== 部门
# 注意顺序：/departments/tree 必须在 /departments/{dept_id} 之前

@router.get("/departments/tree")
async def department_tree(
    _: CurrentUser = Depends(require_permission("user:manage")),
    session: AsyncSession = Depends(get_db),
):
    return ok(await svc.department_tree(session))


@router.get("/departments")
async def list_departments(
    _: CurrentUser = Depends(require_permission("user:manage")),
    session: AsyncSession = Depends(get_db),
):
    rows = await svc.list_departments(session)
    return ok([svc.serialize_department(d) for d in rows])


@router.post("/departments")
async def create_department(
    payload: DepartmentCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("user:manage")),
    session: AsyncSession = Depends(get_db),
):
    dept = await svc.create_department(session, payload.model_dump())
    after = svc.serialize_department(dept)
    await write_audit(
        session,
        operator_id=user.id,
        action="create",
        business_type="department",
        business_id=dept.id,
        after=after,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(after, "部门已创建")


@router.get("/departments/{dept_id}")
async def get_department(
    dept_id: int,
    _: CurrentUser = Depends(require_permission("user:manage")),
    session: AsyncSession = Depends(get_db),
):
    dept = await svc.get_department_or_404(session, dept_id)
    usage = await svc.department_usage(session, dept.id)
    return ok({**svc.serialize_department(dept), **usage})


@router.patch("/departments/{dept_id}")
async def update_department(
    dept_id: int,
    payload: DepartmentUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("user:manage")),
    session: AsyncSession = Depends(get_db),
):
    dept = await svc.get_department_or_404(session, dept_id)
    before = svc.serialize_department(dept)
    await svc.update_department(session, dept, payload.model_dump(exclude_unset=True))
    after = svc.serialize_department(dept)
    await write_audit(
        session,
        operator_id=user.id,
        action="update",
        business_type="department",
        business_id=dept.id,
        before=before,
        after=after,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(after, "已保存")


@router.delete("/departments/{dept_id}")
async def delete_department(
    dept_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("user:manage")),
    session: AsyncSession = Depends(get_db),
):
    dept = await svc.get_department_or_404(session, dept_id)
    before = svc.serialize_department(dept)
    await svc.delete_department(session, dept)
    await write_audit(
        session,
        operator_id=user.id,
        action="delete",
        business_type="department",
        business_id=dept_id,
        before=before,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(None, "部门已删除")


@router.get("/departments/{dept_id}/users")
async def department_users(
    dept_id: int,
    _: CurrentUser = Depends(require_permission("user:manage")),
    session: AsyncSession = Depends(get_db),
):
    await svc.get_department_or_404(session, dept_id)
    rows = await svc.users_of_department(session, dept_id)
    role_map = await svc.roles_of_users(session, [u.id for u in rows])
    return ok(
        [
            svc.serialize_user(u, roles=role_map.get(u.id, []))
            for u in rows
        ]
    )


# ================================================================== 角色与权限

@router.get("/roles")
async def list_roles(
    _: CurrentUser = Depends(require_permission("user:manage")),
    session: AsyncSession = Depends(get_db),
):
    rows = await svc.list_roles(session)
    items = []
    for role in rows:
        codes = await svc.permission_codes_of_role(session, role.id)
        items.append(svc.serialize_role(role, permission_codes=codes))
    return ok(items)


@router.post("/roles")
async def create_role(
    payload: RoleCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("user:manage")),
    session: AsyncSession = Depends(get_db),
):
    role = await svc.create_role(session, payload.model_dump())
    codes = await svc.permission_codes_of_role(session, role.id)
    after = svc.serialize_role(role, permission_codes=codes)
    await write_audit(
        session,
        operator_id=user.id,
        action="create",
        business_type="role",
        business_id=role.id,
        after=after,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(after, "角色已创建")


@router.get("/roles/{role_id}")
async def get_role(
    role_id: int,
    _: CurrentUser = Depends(require_permission("user:manage")),
    session: AsyncSession = Depends(get_db),
):
    role = await svc.get_role_or_404(session, role_id)
    codes = await svc.permission_codes_of_role(session, role.id)
    usage = await svc.role_usage(session, role.id)
    return ok({**svc.serialize_role(role, permission_codes=codes), **usage})


@router.patch("/roles/{role_id}")
async def update_role(
    role_id: int,
    payload: RoleUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("user:manage")),
    session: AsyncSession = Depends(get_db),
):
    role = await svc.get_role_or_404(session, role_id)
    before_codes = await svc.permission_codes_of_role(session, role.id)
    before = svc.serialize_role(role, permission_codes=before_codes)

    await svc.update_role(session, role, payload.model_dump(exclude_unset=True))

    after_codes = await svc.permission_codes_of_role(session, role.id)
    after = svc.serialize_role(role, permission_codes=after_codes)
    await write_audit(
        session,
        operator_id=user.id,
        action="update",
        business_type="role",
        business_id=role.id,
        before=before,
        after=after,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(after, "已保存")


@router.delete("/roles/{role_id}")
async def delete_role(
    role_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("user:manage")),
    session: AsyncSession = Depends(get_db),
):
    role = await svc.get_role_or_404(session, role_id)
    before = svc.serialize_role(role)
    await svc.delete_role(session, role)
    await write_audit(
        session,
        operator_id=user.id,
        action="delete",
        business_type="role",
        business_id=role_id,
        before=before,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(None, "角色已删除")


@router.get("/permissions")
async def list_permissions(
    _: CurrentUser = Depends(require_permission("user:manage")),
    session: AsyncSession = Depends(get_db),
):
    rows = await svc.list_permissions(session)
    return ok(
        [
            {
                "id": p.id,
                "code": p.code,
                "name": p.name,
                "resource": p.resource,
                "action": p.action,
            }
            for p in rows
        ]
    )


# ================================================================== 角色权限与数据范围
# 03-API §5 把这四项单独列出（而不是只靠 PATCH /roles/{id}）。
# 分开的意义：授权与调数据范围是两个不同的人在管，也是两次独立的审计事件。

@router.get("/roles/{role_id}/permissions")
async def get_role_permissions(
    role_id: int,
    _: CurrentUser = Depends(require_permission("user:manage")),
    session: AsyncSession = Depends(get_db),
):
    """该角色的权限码列表。"""
    role = await svc.get_role_or_404(session, role_id)
    codes = await svc.permission_codes_of_role(session, role.id)
    return ok({"role_id": role.id, "code": role.code, "permission_codes": codes})


@router.put("/roles/{role_id}/permissions")
async def set_role_permissions(
    role_id: int,
    payload: RolePermissionUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("user:manage")),
    session: AsyncSession = Depends(get_db),
):
    """覆盖角色的权限集合（03-API §5）。

    传空数组等于收回全部权限 —— 这是合法操作（也是"先清空再逐个加"
    的常见做法），不做拦截。
    """
    role = await svc.get_role_or_404(session, role_id)
    before = await svc.permission_codes_of_role(session, role.id)
    await svc.set_role_permissions(session, role, payload.permission_codes)
    await session.flush()
    after = await svc.permission_codes_of_role(session, role.id)
    await write_audit(
        session,
        operator_id=user.id,
        action="set_role_permissions",
        business_type="role",
        business_id=role.id,
        before={"permission_codes": before},
        after={"permission_codes": after},
        ip=client_ip(request),
    )
    await session.commit()
    return ok({"role_id": role.id, "permission_codes": after}, "权限已更新")


@router.get("/roles/{role_id}/data-scope")
async def get_role_data_scope(
    role_id: int,
    _: CurrentUser = Depends(require_permission("user:manage")),
    session: AsyncSession = Depends(get_db),
):
    """该角色的数据范围，以及实际在用这个角色的人数。

    带上 `user_count` 是因为改数据范围会影响所有人 ——
    界面要能先告诉管理员"这一改要动 N 个人"。
    """
    role = await svc.get_role_or_404(session, role_id)
    usage = await svc.role_usage(session, role.id)
    return ok(
        {
            "role_id": role.id,
            "code": role.code,
            "name": role.name,
            "data_scope": role.data_scope,
            "data_scope_label": DATA_SCOPE_LABELS.get(role.data_scope, role.data_scope),
            **usage,
        }
    )


@router.put("/roles/{role_id}/data-scope")
async def set_role_data_scope(
    role_id: int,
    payload: RoleDataScopeUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("user:manage")),
    session: AsyncSession = Depends(get_db),
):
    """改角色的数据范围（03-API §5）。

    只改 `data_scope` 一列，不动权限集合 —— 这正是这两个接口分开的意义。
    改完主动把结果里的 user_count 带回去，让界面能提示影响面。
    """
    role = await svc.get_role_or_404(session, role_id)
    before_scope = role.data_scope
    role.data_scope = payload.data_scope
    await session.flush()
    usage = await svc.role_usage(session, role.id)
    await write_audit(
        session,
        operator_id=user.id,
        action="set_role_data_scope",
        business_type="role",
        business_id=role.id,
        before={"data_scope": before_scope},
        after={"data_scope": role.data_scope, **usage},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(
        {
            "role_id": role.id,
            "data_scope": role.data_scope,
            "data_scope_label": DATA_SCOPE_LABELS.get(role.data_scope, role.data_scope),
            **usage,
        },
        f"数据范围已改为「{DATA_SCOPE_LABELS.get(role.data_scope, role.data_scope)}」",
    )


__all__ = ["router", "AppError", "ErrorCode", "Query"]
