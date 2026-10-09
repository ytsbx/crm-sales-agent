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

from sqlalchemy import Select, select, literal
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.modules.user.model import Department, User


class ScopeUser(Protocol):
    """只需这两个属性，避免 core 反向依赖 deps（会造成 import 环）。"""

    id: int
    department_id: int | None
    data_scope: str


def department_subtree_stmt(department_id: int | None) -> Select:
    """某个部门及其**全部下级**的 id（递归 CTE）。

    ⚠️ 递归那半句必须**装进 CTE 的定义里**（`subtree = subtree.union_all(...)`）。
    原先写成 `select(subtree.c.id).union_all(...)`：CTE 定义里就只剩"起点部门"
    这一条，递归那半句落到了外面那条 `UNION ALL` 上 —— 而外层只执行一次，
    于是整棵树**只往下展开一层**，孙部门一律漏掉（第十批 10.9 修）。

    这个错特别难自己冒出来：只用两层部门（甲部 → 子部）去测，"本部门及下级"
    恰好就是这两个，看起来完全正确；**三层以上**才露破绽。所以套件里的部门树
    必须是三层以上，两层等于没测。
    """
    # ⚠️ 递归深度上限 = **防环兜底**（审查 C3-06 的下半条）。
    #
    # 写入侧已经加了「部门树」事务锁，新环不会再产生；但**历史数据里可能已经有环**
    # （比如锁上线之前造的），那时这条递归会一直转下去。实测过后果：
    # 加 3 秒语句超时后直接超时，受影响部门的人查客户**请求挂死**。
    # 所以查询侧也要能自己止住 —— 两道防线各管一半，缺一不可。
    #
    # 取 64 层：正常部门树远达不到，而真出现环时最多转 64 圈就停。
    _MAX_DEPTH = 64
    base = select(Department.id, literal(0).label("depth")).where(
        Department.id == department_id
    )
    subtree = base.cte("dept_subtree", recursive=True)
    subtree = subtree.union_all(
        select(Department.id, subtree.c.depth + 1)
        .join(subtree, Department.parent_id == subtree.c.id)
        .where(subtree.c.depth < _MAX_DEPTH)
    )
    return select(subtree.c.id)


def department_subtree_ids_stmt(user: ScopeUser) -> Select:
    """用户所在部门及其所有下级部门的 id（递归 CTE）。"""
    return department_subtree_stmt(user.department_id)


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


async def department_member_ids(session: AsyncSession, department_id: int) -> list[int]:
    """某部门（**含全部下级**）的成员 id。

    为什么需要它：团队目标的计划、实绩、差额必须用**同一个成员集合**。
    原来团队目标行（`user_id` 为空）的实际值走的是"可见范围合计"，
    于是部门目标配上公司数字——达成率永远好看，也永远看不出部门自己做得怎么样
    （第三批 §4.1.1）。与 `scoped_owner_ids` 共用同一套递归口径
    （`department_subtree_stmt`），不在这里另写一份部门树展开 —— 这里原先确实
    又写了一份，于是修 10.9 时两处都得改，漏一处就前功尽弃。
    """
    rows = await session.execute(
        select(User.id).where(
            User.department_id.in_(department_subtree_stmt(department_id))
        )
    )
    return list(rows.scalars().all())


async def ensure_in_scope(
    session: AsyncSession,
    user: ScopeUser,
    *,
    owner_id: int | None,
    label: str,
    allow_unowned: bool = False,
) -> None:
    """校验某条记录是否在当前用户的数据范围内，不在就抛 40302。

    ## 为什么必须有这个函数

    各模块的**列表**接口一直按数据范围过滤（`scoped_owner_ids`），
    但**按 id 直查**的详情与写接口大多只做了 `get_x_or_404`（只判断存在）。
    实测确认（张三访问王五的数据）以下接口可以绕过数据范围：
    商机详情/改/删、报价详情、报价版本详情、订单详情/改、
    样品详情/改、任务改、线索详情/改 —— 改个 id 就能看和改别人的数据。
    "列表看不到"和"拿不到"必须一致，否则前端隐藏毫无意义（05-TECH §24）。

    `allow_unowned`：客户与线索允许无负责人（公海 / 线索池），
    它们对所有有查看权限的人可见 —— 这正是公海的意义。
    其余模块（商机/订单/样品/任务）保持 False。
    """
    owner_ids = await scoped_owner_ids(session, user)
    if owner_ids is None:
        # `all`（管理员/财务）能看到全量数据，**无归属也看得见**。
        # 这里必须排在下面"无归属默认拒绝"之前：口径要挡的是"没有范围的人猜到
        # 编号就能看含价格的对客文件"，而 `all` 本来就是全量可见，再拒一次只会把
        # 管理员挡在公海回收、客户合并后遗留的合同/单据之外（历史 bug）。
        return

    if owner_id is None:
        # 无负责人的记录要**分两类**，不能一律放行：
        # - `allow_unowned=True`：客户/线索是正常业务状态（公海 / 线索池），
        #   它们对所有有查看权限的人可见——这正是公海的意义；
        # - 其余（商机/报价/订单/样品/任务/对客单据）：无归属属于历史脏数据，
        #   放行等于"含价格的对客文件，猜到编号就能下"。**默认拒绝**，
        #   要求先把归属补齐（口径已确认：下单/签合同前必须先认领）。
        # 注：这个参数此前虽然写在签名里，函数体却不读它——调用方传了也没用，
        # 等于所有模块都按"放行"走（2026-09-30 修正）。
        if allow_unowned:
            return
        raise AppError(
            ErrorCode.DATA_SCOPE_DENIED,
            f"该{label}没有负责人，无法判定可见范围，已拒绝访问；请先补齐归属",
            403,
        )

    if int(owner_id) not in owner_ids:
        raise AppError(
            ErrorCode.DATA_SCOPE_DENIED, f"该{label}不在你的数据范围内", 403
        )


def scope_guard(
    session: AsyncSession, user: ScopeUser, label: str, *, allow_unowned: bool = False
):
    """给列表之外的单条查询用：返回一个 `await ensure(owner_id)` 小函数。

    用法：
        ensure = scope_guard(session, user, "商机")
        opportunity = await svc.get_opportunity_or_404(session, oid)
        await ensure(opportunity.owner_id)

    比每次手写一遍 `owner_ids is None / in / raise` 更不容易漏，
    也让"这个接口做了范围校验"在代码里一眼可见。

    `allow_unowned`：客户/线索这类允许无负责人的对象要显式传 True，
    否则用 scope_guard 包客户/线索会把公海一起关掉（与 ensure_in_scope 同一口径）。
    """

    async def ensure(owner_id: int | None) -> None:
        await ensure_in_scope(
            session, user, owner_id=owner_id, label=label, allow_unowned=allow_unowned
        )

    return ensure
