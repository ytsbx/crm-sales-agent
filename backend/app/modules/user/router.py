"""系统设置：用户 / 部门 / 角色（对齐 03-API §3 / §4 / §5，当前为只读）。

这一版只提供查询，用于设置页展示；增删改和角色授权留到 Phase 5 的运营能力阶段。
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import CurrentUser, require_permission
from app.core.response import ok, page_data, paginate
from app.modules.user.model import Department, Role, User

router = APIRouter(tags=["System"])


@router.get("/users")
async def list_users(
    keyword: str | None = None,
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    # 有分配客户权限的人也需要选人，所以这里放开给 customer:assign
    _: CurrentUser = Depends(require_permission("user:manage", "customer:assign")),
    session: AsyncSession = Depends(get_db),
):
    stmt = select(User)
    if keyword:
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(User.name.ilike(like) | User.username.ilike(like))
    if status:
        stmt = stmt.where(User.status == status)
    rows, total = await paginate(session, stmt.order_by(User.id.asc()), page, page_size)

    dept_map = {
        d.id: d.name for d in (await session.execute(select(Department))).scalars().all()
    }
    items = [
        {
            "id": user.id,
            "name": user.name,
            "username": user.username,
            "mobile": user.mobile,
            "email": user.email,
            "department": dept_map.get(user.department_id) if user.department_id else None,
            "status": user.status,
            "created_at": user.created_at,
        }
        for user in rows
    ]
    return ok(page_data(items, total, page, page_size))


@router.get("/departments")
async def list_departments(
    _: CurrentUser = Depends(require_permission("user:manage")),
    session: AsyncSession = Depends(get_db),
):
    rows = (await session.execute(select(Department).order_by(Department.id.asc()))).scalars().all()
    return ok(
        [
            {
                "id": d.id,
                "name": d.name,
                "parent_id": d.parent_id,
                "status": d.status,
            }
            for d in rows
        ]
    )


@router.get("/roles")
async def list_roles(
    _: CurrentUser = Depends(require_permission("user:manage")),
    session: AsyncSession = Depends(get_db),
):
    rows = (await session.execute(select(Role).order_by(Role.id.asc()))).scalars().all()
    return ok(
        [
            {
                "id": role.id,
                "code": role.code,
                "name": role.name,
                "description": role.description,
                "data_scope": role.data_scope,
                "status": role.status,
            }
            for role in rows
        ]
    )
