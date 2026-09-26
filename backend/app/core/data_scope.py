"""业务数据范围（data scope）的统一实现。

背景：`roles.data_scope` 支持四个值 —— self / department / department_and_sub / all。
但此前 9 个模块各写一份过滤条件，且都把 `department_and_sub` 当成 `department` 处理
（只比 `User.department_id == user.department_id`，从不递归 `departments.parent_id`），
于是配了「本部门及下级」的主管实际只看得到本级部门的数据。

这里统一成一份，`department_and_sub` 走递归 CTE 取整棵子树。
用 CTE 而不是把部门树拆成多次查询，是为了让过滤留在一条 SQL 里，
分页、计数、聚合都能直接复用。PostgreSQL 与 SQLite 都支持 `WITH RECURSIVE`。
"""

from typing import Protocol

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.user.model import Department, User


class ScopeUser(Protocol):
    """只需这两个属性，避免 core 反向依赖 deps（会造成 import 环）。"""

    id: int
    department_id: int | None
    data_scope: str


def department_subtree_ids_stmt(user: ScopeUser) -> Select:
    """用户所在部门及其所有下级部门的 id（递归 CTE）。"""
    base = select(Department.id).where(Department.id == user.department_id)
    subtree = base.cte("dept_subtree", recursive=True)
    return select(subtree.c.id).union_all(
        select(Department.id).join(subtree, Department.parent_id == subtree.c.id)
    )


async def scoped_owner_ids(session: AsyncSession, user: ScopeUser) -> list[int] | None:
    """当前用户能看到的「负责人 id」集合。

    返回 None 表示 `all`（不加过滤）；否则返回可用于 `Model.owner_id.in_(...)`
    的 id 列表。

    为什么是协程：递归子树的成员要先查出来。`CurrentUser` 的 department_id 是从
    ORM 实例上取的普通属性，但如果将来改成关系属性，在异步会话里直接读会触发
    懒加载并报 MissingGreenlet（见 10-交接文档 第八节第 3 条）。先查成 id 列表，
    调用方只做 `in_(ids)`，语义和并发行为都更可预期。

    注意：调用方各自对「无负责人」的处理不同（客户/线索允许 owner_id 为空，
    商机/订单不允许），所以这里只管负责人集合，不碰 NULL 语义。
    """
    scope = user.data_scope
    if scope == "all":
        return None

    if scope == "department":
        if user.department_id is None:
            return [user.id]
        rows = await session.execute(
            select(User.id).where(User.department_id == user.department_id)
        )
        return list(rows.scalars().all())

    if scope == "department_and_sub":
        if user.department_id is None:
            # 没有部门的人谈不上「下级」，退化为只看自己
            return [user.id]
        rows = await session.execute(
            select(User.id).where(User.department_id.in_(department_subtree_ids_stmt(user)))
        )
        return list(rows.scalars().all())

    return [user.id]
