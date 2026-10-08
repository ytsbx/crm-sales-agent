"""用户 / 部门 / 角色 的读写逻辑（03-API §3 / §4 / §5）。

写操作集中在 service 里，路由只负责鉴权与审计。几条关键约束都在这里兜底：
- 用户名唯一；角色 code 唯一；同一父部门下部门名唯一；
- 部门成环检测（不能把自己或自己的祖先设成父部门）；
- 停用/删除前检查引用（有下级部门、有在岗用户、角色还有人用）；
- 不允许停用自己、不允许删掉自己最后的 admin 角色，避免把管理员锁在门外。
"""

from datetime import UTC, datetime

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.core.security import hash_password
from app.modules.auth.session import REASON_PASSWORD_CHANGE, revoke_user_sessions
from app.modules.user.model import (
    Department,
    Permission,
    Role,
    User,
    ROLE_ACTIVE,
    role_permissions,
    user_roles,
)


# ------------------------------------------------------------------ 登录态查询
# 注意：下面三个函数被 core/deps.py 用来构造 CurrentUser，是登录链路的一部分，
# 改动前务必确认 auth / 所有 require_permission 仍然可用。

# 角色状态里「生效」的那一个：`ROLE_ACTIVE`（定义在 `user/model.py`，此处 import）。
# 停用的角色**不参与**权限、角色特例与数据范围的计算（第十批 10.10）——
# 此前这三处都不看状态，于是停用一个角色等于没停：权限接口照旧把它返回、
# 客户列表照样进得去、配了「全部」范围的角色停了也仍然全量可见。


async def get_user_roles(session: AsyncSession, user_id: int) -> list[Role]:
    """**运行时生效**的角色（登录链路与权限判定都走它）。

    与 `roles_of_user` / `roles_of_users` 的区别很要紧，两边的口径是刻意分开的：
    那两个是**管理视图**用的，不过滤状态 —— 用户管理页要显示「这个人挂着哪些角色、
    哪几个已经停用」。这里必须只给启用中的角色，否则停用角色会顺着
    `core/deps.py` 一路渗进权限特例与数据范围。

    改这里之前先确认 auth / 所有 require_permission 仍然可用（见本段的说明）。
    """
    stmt = (
        select(Role)
        .join(user_roles, user_roles.c.role_id == Role.id)
        .where(user_roles.c.user_id == user_id, Role.status == ROLE_ACTIVE)
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_user_permission_codes(session: AsyncSession, user_id: int) -> set[str]:
    """该用户**当前实际拥有**的权限码。

    注意这里要显式 join 到 Role：过滤条件落在 `Role.status` 上，
    不 join 就没法判角色是不是停用了（原来只 join 了中间表）。
    """
    stmt = (
        select(Permission.code)
        .join(role_permissions, role_permissions.c.permission_id == Permission.id)
        .join(Role, Role.id == role_permissions.c.role_id)
        .join(user_roles, user_roles.c.role_id == Role.id)
        .where(user_roles.c.user_id == user_id, Role.status == ROLE_ACTIVE)
    )
    return {code for code in (await session.execute(stmt)).scalars().all()}


def resolve_data_scope(roles: list[Role]) -> str:
    """多个角色时取范围最大者。**只算启用中的角色。**

    这里再过滤一次是防御性的：调用方可能传进来的是管理视图那份（含停用角色）的
    列表。多挡一道，比指望每个调用点都记得先走 `get_user_roles` 更可靠。
    """
    order = ["self", "department", "department_and_sub", "all"]
    best = "self"
    for role in roles:
        if (getattr(role, "status", ROLE_ACTIVE) or ROLE_ACTIVE) != ROLE_ACTIVE:
            continue
        scope = role.data_scope or "self"
        if scope in order and order.index(scope) > order.index(best):
            best = scope
    return best


def serialize_user(user: User, *, department_name: str | None = None, roles: list[dict] | None = None) -> dict:
    """用户对外表示。**永不返回 password_hash。**"""
    return {
        "id": user.id,
        "name": user.name,
        "username": user.username,
        "mobile": user.mobile,
        "email": user.email,
        "department_id": user.department_id,
        "department": department_name,
        "wecom_userid": user.wecom_userid,
        "status": user.status,
        "roles": roles or [],
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


def serialize_department(dept: Department) -> dict:
    return {
        "id": dept.id,
        "name": dept.name,
        "parent_id": dept.parent_id,
        "wecom_department_id": dept.wecom_department_id,
        "status": dept.status,
    }


def serialize_role(role: Role, *, permission_codes: list[str] | None = None) -> dict:
    return {
        "id": role.id,
        "code": role.code,
        "name": role.name,
        "description": role.description,
        "data_scope": role.data_scope,
        "status": role.status,
        "permission_codes": permission_codes,
    }


# ------------------------------------------------------------------ 用户

async def get_user_or_404(session: AsyncSession, user_id: int) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise AppError(ErrorCode.NOT_FOUND, "用户不存在", 404)
    return user


async def find_user_by_username(session: AsyncSession, username: str) -> User | None:
    return (
        await session.execute(select(User).where(User.username == username.strip()))
    ).scalar_one_or_none()


async def department_names(session: AsyncSession, ids: list[int]) -> dict[int, str]:
    ids = [i for i in ids if i]
    if not ids:
        return {}
    rows = (
        await session.execute(select(Department.id, Department.name).where(Department.id.in_(ids)))
    ).all()
    return {did: name for did, name in rows}


async def roles_of_users(session: AsyncSession, user_ids: list[int]) -> dict[int, list[dict]]:
    """批量取用户的角色，避免列表 N+1。"""
    if not user_ids:
        return {}
    rows = (
        await session.execute(
            select(user_roles.c.user_id, Role)
            .join(Role, Role.id == user_roles.c.role_id)
            .where(user_roles.c.user_id.in_(user_ids))
            .order_by(Role.id.asc())
        )
    ).all()
    result: dict[int, list[dict]] = {}
    for user_id, role in rows:
        result.setdefault(user_id, []).append(
            {
                "id": role.id,
                "code": role.code,
                "name": role.name,
                "data_scope": role.data_scope,
                # 管理视图要能看出"这个角色已经停用、所以不生效"（第十批 10.10）
                "status": role.status,
            }
        )
    return result


async def _validate_department(session: AsyncSession, department_id: int | None) -> None:
    if department_id is None:
        return
    if await session.get(Department, department_id) is None:
        raise AppError(ErrorCode.NOT_FOUND, f"部门 id={department_id} 不存在", 404)


async def _validate_roles(session: AsyncSession, role_ids: list[int]) -> list[Role]:
    if not role_ids:
        return []
    rows = (
        await session.execute(select(Role).where(Role.id.in_(role_ids)))
    ).scalars().all()
    missing = set(role_ids) - {r.id for r in rows}
    if missing:
        raise AppError(ErrorCode.NOT_FOUND, f"角色不存在：{sorted(missing)}", 404)
    return list(rows)


async def create_user(session: AsyncSession, data: dict) -> User:
    username = (data.get("username") or "").strip()
    if not username:
        raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "登录名不能为空")
    if await find_user_by_username(session, username) is not None:
        raise AppError(ErrorCode.DUPLICATE, f"登录名「{username}」已被占用", 409)
    await _validate_department(session, data.get("department_id"))
    roles = await _validate_roles(session, data.get("role_ids") or [])

    user = User(
        name=data["name"].strip(),
        username=username,
        password_hash=hash_password(data["password"]),
        mobile=data.get("mobile"),
        email=data.get("email"),
        department_id=data.get("department_id"),
        wecom_userid=data.get("wecom_userid"),
        status="active",
    )
    session.add(user)
    await session.flush()

    for role in roles:
        await session.execute(user_roles.insert().values(user_id=user.id, role_id=role.id))
    await session.flush()
    return user


async def update_user(session: AsyncSession, user: User, data: dict) -> User:
    if "department_id" in data:
        await _validate_department(session, data["department_id"])
    for field in ("name", "mobile", "email", "department_id", "wecom_userid"):
        if field in data:
            value = data[field]
            if field == "name" and value is not None:
                value = value.strip()
                if not value:
                    raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "姓名不能为空")
            setattr(user, field, value)
    if data.get("password"):
        user.password_hash = hash_password(data["password"])
        # 改（或重置）密码 = 该账号的**全部旧凭据一次性作废**（第十批 10.12）。
        #
        # 与写哈希放在同一个事务里，由路由那一次 commit 一起落库：
        # 要么"新密码生效 + 旧会话全废"，要么两件都没发生。中途绝不能出现
        # "密码已经换了、别人的旧令牌还能进去"的窗口 —— 那正是重置密码
        # 想解决的场景（账号疑似泄漏时改密码，就是要把旧钥匙全部作废）。
        #
        # 包含操作者自己：管理员改自己的密码，当前这次登录也会失效，
        # 下一个请求就要重新登录。这是刻意的 —— "全部旧会话失效"没有例外。
        await revoke_user_sessions(session, user.id, reason=REASON_PASSWORD_CHANGE)
    await session.flush()
    return user


async def set_user_status(
    session: AsyncSession, user: User, *, status: str, operator_id: int | None
) -> User:
    if status == "disabled":
        # 不允许停用自己：否则当前会话下一次请求就会 401，像"把自己踢出系统"
        if operator_id is not None and user.id == operator_id:
            raise AppError(ErrorCode.PARAM_ERROR, "不能停用自己的账号", 422)
        await _assert_not_last_admin(session, user, reason="停用")
    user.status = status
    await session.flush()
    return user


async def set_user_roles(session: AsyncSession, user: User, role_ids: list[int]) -> list[Role]:
    roles = await _validate_roles(session, role_ids)
    # 摘掉 admin 角色前要确认系统里还有别的管理员
    current = {r.code for r in await roles_of_user(session, user.id)}
    new_codes = {r.code for r in roles}
    if "admin" in current and "admin" not in new_codes:
        await _assert_not_last_admin(session, user, reason="移除管理员角色")

    await session.execute(user_roles.delete().where(user_roles.c.user_id == user.id))
    for role in roles:
        await session.execute(user_roles.insert().values(user_id=user.id, role_id=role.id))
    await session.flush()
    return roles


async def roles_of_user(session: AsyncSession, user_id: int) -> list[Role]:
    return list(
        (
            await session.execute(
                select(Role).join(user_roles, user_roles.c.role_id == Role.id).where(
                    user_roles.c.user_id == user_id
                )
            )
        ).scalars().all()
    )


async def permission_codes_of_role(session: AsyncSession, role_id: int) -> list[str]:
    return list(
        (
            await session.execute(
                select(Permission.code)
                .join(role_permissions, role_permissions.c.permission_id == Permission.id)
                .where(role_permissions.c.role_id == role_id)
                .order_by(Permission.code.asc())
            )
        ).scalars().all()
    )


#: 「最后一位有效管理员」保护的**全局事务锁**键（第十批 10.11）。
#:
#: 为什么要一把与具体用户无关的锁：互相停用是两个**不同的用户**，
#: 各自锁自己那一行，谁也挡不住谁 —— 两边都数到「对方还在岗」，
#: 于是一起放行，最后 0 个管理员。行锁在这里结构上就不够用。
#:
#: 用 `pg_advisory_xact_lock` 而不是自建锁表：它**随事务提交/回滚自动释放**，
#: 漏了手工解锁也不会把系统永久锁死，正好是「事务级保护」要的语义。
#: 这个数字没有业务含义，只要全项目一致且固定即可。
_ADMIN_GUARD_LOCK_KEY = 7301001


async def _lock_admin_guard(session: AsyncSession) -> None:
    """取得「有效管理员人数」保护锁，并持有到本事务结束。"""
    await session.execute(select(func.pg_advisory_xact_lock(_ADMIN_GUARD_LOCK_KEY)))


async def _is_active_admin(session: AsyncSession, user_id: int) -> bool:
    """这个人是不是**有效**管理员：持有启用中的 admin 角色。"""
    count = (
        await session.execute(
            select(func.count())
            .select_from(user_roles)
            .join(Role, Role.id == user_roles.c.role_id)
            .where(
                user_roles.c.user_id == user_id,
                Role.code == "admin",
                Role.status == ROLE_ACTIVE,
            )
        )
    ).scalar_one()
    return bool(count)


async def count_active_admins(
    session: AsyncSession,
    *,
    exclude_user_id: int | None = None,
    exclude_role_id: int | None = None,
) -> int:
    """当前有效管理员人数：**账号在岗 且 持有启用中的 admin 角色**。

    `exclude_*` 用来模拟「这次操作已经生效之后」的样子：
    停用账号 / 摘角色时排掉这个人，停用管理员角色时排掉这个角色。

    两个维度缺一不可 —— 只数角色不看账号在岗，会把停用账号也算成管理员；
    只数账号不看角色状态，会在「角色全被停用」时仍以为还有人
    （第十批 10.10 与 10.11 是同一个根）。
    """
    stmt = (
        select(func.count(func.distinct(User.id)))
        .select_from(User)
        .join(user_roles, user_roles.c.user_id == User.id)
        .join(Role, Role.id == user_roles.c.role_id)
        .where(
            Role.code == "admin",
            Role.status == ROLE_ACTIVE,
            User.status == "active",
        )
    )
    if exclude_user_id is not None:
        stmt = stmt.where(User.id != exclude_user_id)
    if exclude_role_id is not None:
        stmt = stmt.where(Role.id != exclude_role_id)
    return int((await session.execute(stmt)).scalar_one())


async def _assert_not_last_admin(session: AsyncSession, user: User, *, reason: str) -> None:
    """确认这个用户不是系统里最后一个有效管理员。

    否则一次误操作就能把所有人锁在系统外，且没有找回入口。

    **顺序很关键：先取锁、再统计。** 统计放在锁外面等于没保护 ——
    两个并发请求会各自读到「对方还在」，然后一起放行。
    """
    await _lock_admin_guard(session)
    if not await _is_active_admin(session, user.id):
        # 他持有的 admin 角色本身已停用：停用他 / 摘掉这个角色都不会减少有效
        # 管理员数，不该被拦 —— 否则「清理一个已经没用的停用角色」会做不下去
        return
    if await count_active_admins(session, exclude_user_id=user.id) == 0:
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"「{user.name}」是系统里最后一位有效管理员，不能{reason}",
            422,
        )


async def _assert_role_disable_keeps_admin(
    session: AsyncSession, role: Role, *, reason: str
) -> None:
    """停用一个管理员角色前，确认停完还剩有效管理员。

    与「停用一个人」不同：停用 admin 角色会一次带走**所有**持有该角色的人，
    所以判据按角色排除，不能按人排除。
    """
    await _lock_admin_guard(session)
    if await count_active_admins(session, exclude_role_id=role.id) == 0:
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"停用后系统里将没有有效管理员，「{role.name}」不能{reason}",
            422,
        )


# ------------------------------------------------------------------ 部门

async def get_department_or_404(session: AsyncSession, dept_id: int) -> Department:
    dept = await session.get(Department, dept_id)
    if dept is None:
        raise AppError(ErrorCode.NOT_FOUND, "部门不存在", 404)
    return dept


async def list_departments(session: AsyncSession) -> list[Department]:
    # `NULLS FIRST` 的可移植写法：先按"有没有上级"排（顶级在前），再按 id。
    # 直接调 .nullsfirst() 是 PostgreSQL 专有，换库即 500。
    return list(
        (
            await session.execute(
                select(Department).order_by(
                    Department.parent_id.is_not(None).asc(), Department.id.asc()
                )
            )
        ).scalars().all()
    )


async def department_tree(session: AsyncSession) -> list[dict]:
    """把平铺的部门列表拼成树。一次查询在内存里拼，避免递归打库。"""
    rows = await list_departments(session)
    nodes = {d.id: {**serialize_department(d), "children": []} for d in rows}
    roots: list[dict] = []
    for dept in rows:
        node = nodes[dept.id]
        parent = nodes.get(dept.parent_id) if dept.parent_id else None
        if parent is None:
            roots.append(node)
        else:
            parent["children"].append(node)
    return roots


async def _assert_no_cycle(session: AsyncSession, dept_id: int, parent_id: int | None) -> None:
    """父部门不能是自己或自己的后代，否则树会成环、递归查询会无限循环。"""
    if parent_id is None:
        return
    if parent_id == dept_id:
        raise AppError(ErrorCode.PARAM_ERROR, "不能把部门设为自己的上级", 422)

    current = parent_id
    seen: set[int] = set()
    while current is not None:
        if current in seen:
            break  # 数据里本来就有环，先止住别再转
        seen.add(current)
        if current == dept_id:
            raise AppError(ErrorCode.PARAM_ERROR, "不能把部门挂到自己的下级部门下", 422)
        parent = await session.get(Department, current)
        current = parent.parent_id if parent else None


async def _assert_sibling_name_free(
    session: AsyncSession, name: str, parent_id: int | None, *, exclude_id: int | None = None
) -> None:
    stmt = select(Department).where(
        Department.name == name.strip(), Department.parent_id.is_(None) if parent_id is None else Department.parent_id == parent_id
    )
    if exclude_id is not None:
        stmt = stmt.where(Department.id != exclude_id)
    if (await session.execute(stmt)).scalar_one_or_none() is not None:
        raise AppError(ErrorCode.DUPLICATE, f"同级下已存在部门「{name}」", 409)


async def create_department(session: AsyncSession, data: dict) -> Department:
    name = (data.get("name") or "").strip()
    if not name:
        raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "部门名称不能为空")
    parent_id = data.get("parent_id")
    if parent_id is not None:
        await get_department_or_404(session, parent_id)
    await _assert_sibling_name_free(session, name, parent_id)

    dept = Department(
        name=name,
        parent_id=parent_id,
        wecom_department_id=data.get("wecom_department_id"),
        status="active",
    )
    session.add(dept)
    await session.flush()
    return dept


async def update_department(session: AsyncSession, dept: Department, data: dict) -> Department:
    new_parent = data.get("parent_id", dept.parent_id)
    if "parent_id" in data and data["parent_id"] is not None:
        await get_department_or_404(session, data["parent_id"])
    await _assert_no_cycle(session, dept.id, new_parent)

    if data.get("name"):
        await _assert_sibling_name_free(
            session, data["name"], new_parent, exclude_id=dept.id
        )

    for field in ("name", "parent_id", "wecom_department_id", "status"):
        if field in data:
            value = data[field]
            if field == "name" and value is not None:
                value = value.strip()
            setattr(dept, field, value)
    await session.flush()
    return dept


async def department_usage(session: AsyncSession, dept_id: int) -> dict:
    """部门被引用的情况：有几个下级、有几个用户。删前要看这两个数。

    字段名用 `child_count` 而不是 `children`：树接口里 `children` 是子节点**数组**，
    同名会让前端拿到的东西"有时是数字有时是数组"。
    """
    children = (
        await session.execute(
            select(func.count()).select_from(Department).where(Department.parent_id == dept_id)
        )
    ).scalar_one()
    users = (
        await session.execute(
            select(func.count()).select_from(User).where(User.department_id == dept_id)
        )
    ).scalar_one()
    return {"child_count": int(children), "users": int(users)}


async def delete_department(session: AsyncSession, dept: Department) -> None:
    usage = await department_usage(session, dept.id)
    if usage["child_count"] > 0:
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"该部门下还有 {usage['child_count']} 个下级部门，请先处理",
            422,
        )
    if usage["users"] > 0:
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"该部门下还有 {usage['users']} 个用户，请先调整他们的部门",
            422,
        )
    await session.delete(dept)


# ------------------------------------------------------------------ 角色与权限

async def get_role_or_404(session: AsyncSession, role_id: int) -> Role:
    role = await session.get(Role, role_id)
    if role is None:
        raise AppError(ErrorCode.NOT_FOUND, "角色不存在", 404)
    return role


async def list_roles(session: AsyncSession) -> list[Role]:
    return list((await session.execute(select(Role).order_by(Role.id.asc()))).scalars().all())


async def list_permissions(session: AsyncSession) -> list[Permission]:
    return list(
        (
            await session.execute(
                select(Permission).order_by(Permission.resource.asc(), Permission.code.asc())
            )
        ).scalars().all()
    )


async def _resolve_permissions(session: AsyncSession, codes: list[str]) -> list[Permission]:
    if not codes:
        return []
    rows = (
        await session.execute(select(Permission).where(Permission.code.in_(codes)))
    ).scalars().all()
    missing = set(codes) - {p.code for p in rows}
    if missing:
        raise AppError(ErrorCode.NOT_FOUND, f"权限码不存在：{sorted(missing)}", 404)
    return list(rows)


async def set_role_permissions(session: AsyncSession, role: Role, codes: list[str]) -> None:
    permissions = await _resolve_permissions(session, codes)
    await session.execute(
        role_permissions.delete().where(role_permissions.c.role_id == role.id)
    )
    for perm in permissions:
        await session.execute(
            role_permissions.insert().values(role_id=role.id, permission_id=perm.id)
        )
    await session.flush()


async def create_role(session: AsyncSession, data: dict) -> Role:
    code = (data.get("code") or "").strip()
    if not code:
        raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "角色编码不能为空")
    existing = (
        await session.execute(select(Role).where(Role.code == code))
    ).scalar_one_or_none()
    if existing is not None:
        raise AppError(ErrorCode.DUPLICATE, f"角色编码「{code}」已存在", 409)

    role = Role(
        code=code,
        name=(data.get("name") or code).strip(),
        description=data.get("description"),
        data_scope=data.get("data_scope") or "self",
        status="active",
    )
    session.add(role)
    await session.flush()
    await set_role_permissions(session, role, data.get("permission_codes") or [])
    return role


async def update_role(session: AsyncSession, role: Role, data: dict) -> Role:
    # 停用管理员角色 = 一次摘掉一批人的管理员身份，必须先确认停完还剩人（第十批 10.11）。
    # 检查放在 setattr **之前**：被拒绝时一个字段都还没改，事务回滚最干净。
    new_status = data.get("status")
    if (
        role.code == "admin"
        and new_status is not None
        and new_status != ROLE_ACTIVE
        and new_status != role.status
    ):
        await _assert_role_disable_keeps_admin(session, role, reason="停用")
    for field in ("name", "description", "data_scope", "status"):
        if field in data and data[field] is not None:
            setattr(role, field, data[field])
    await session.flush()
    if data.get("permission_codes") is not None:
        await set_role_permissions(session, role, data["permission_codes"])
    return role


async def role_usage(session: AsyncSession, role_id: int) -> dict:
    users = (
        await session.execute(
            select(func.count()).select_from(user_roles).where(user_roles.c.role_id == role_id)
        )
    ).scalar_one()
    return {"users": int(users)}


async def delete_role(session: AsyncSession, role: Role) -> None:
    if role.code == "admin":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "内置管理员角色不能删除", 422)
    usage = await role_usage(session, role.id)
    if usage["users"] > 0:
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"还有 {usage['users']} 个用户在使用该角色，请先调整他们的角色",
            422,
        )
    await session.execute(
        role_permissions.delete().where(role_permissions.c.role_id == role.id)
    )
    await session.delete(role)


async def users_of_department(session: AsyncSession, dept_id: int) -> list[User]:
    return list(
        (
            await session.execute(
                select(User).where(User.department_id == dept_id).order_by(User.id.asc())
            )
        ).scalars().all()
    )


def now() -> datetime:
    return datetime.now(UTC)


def base_user_query() -> Select:
    return select(User)
