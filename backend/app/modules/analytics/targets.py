"""目标 vs 实际（领导模块⑧）。

实际值的三个来源（都按数据范围过滤）：
- 销售额 = 非取消订单的 total_amount，按负责人 × 月聚合；
- 新客户 = 客户档案按 created_at 的负责人 × 月计数；
- 复购（老客净额）= 期初固定老客池在本月的订单净额，口径定义在 `target_bases.py`，
  这里只调用不再重写（否则同一口径会出现两个数）。

将来聚水潭接入后，"实际销售额"可切换为出库金额——目标表结构不动。
"""

import re
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.data_scope import scoped_owner_ids
from app.core.deps import CurrentUser
from app.core.errors import AppError, ErrorCode
from app.modules.analytics import target_bases
from app.modules.analytics.model import SalesTarget
from app.modules.customer.model import Customer
from app.modules.order.model import SalesOrder

#: 期间的标准形态。库层有同名 CHECK 约束，两边保持一字不差。
PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def normalize_period(period: str) -> str:
    """把期间统一成 `YYYY-MM`（第三批 §4.1.6）。

    原来只过 `datetime.strptime(p, '%Y-%m')`，而它是**宽容**的：`2026-1` 照样通过
    并存进库。实际值那边按 `f"{year}-{int(m):02d}"` 生成 `2026-01`，两边永远对不上
    ——那一行目标就变成"设了但达成为 0"，而且 `2026-1` 与 `2026-01` 能同时存在。
    这里先补零、再严格校验；补不出年月的一律拒绝（不去猜它想表达什么）。
    """
    raw = (period or "").strip()
    matched = re.fullmatch(r"(\d{4})-(\d{1,2})", raw)
    if matched is None:
        raise AppError(ErrorCode.PARAM_ERROR, "period 必须是 YYYY-MM 格式", 422)
    month = int(matched.group(2))
    if not 1 <= month <= 12:
        raise AppError(ErrorCode.PARAM_ERROR, "period 的月份必须在 01-12 之间", 422)
    normalized = f"{matched.group(1)}-{month:02d}"
    if not PERIOD_RE.match(normalized):  # 兜底自证
        raise AppError(ErrorCode.PARAM_ERROR, "period 必须是 YYYY-MM 格式", 422)
    return normalized


def target_snapshot(row: SalesTarget | None) -> dict | None:
    """审计用的目标快照（第三批 §4.1.2 要求"完整审计"：改前改后都要留）。"""
    if row is None:
        return None
    return {
        "id": row.id,
        "period": row.period,
        "user_id": row.user_id,
        "department_id": row.department_id,
        "new_customer_target": row.new_customer_target,
        "sales_target": float(row.sales_target or 0),
        "repeat_customer_target": float(row.repeat_customer_target or 0),
        "remark": row.remark,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def _visible_owner_ids(session: AsyncSession, user: CurrentUser) -> list[int] | None:
    """None = 不限（all 范围）；否则本范围负责人清单。"""
    if user.data_scope == "all":
        return None
    return await scoped_owner_ids(session, user)


async def targets_with_actuals(session: AsyncSession, user: CurrentUser, year: int) -> dict:
    prefix = f"{year}-%"
    owner_ids = await _visible_owner_ids(session, user)
    admin_view = owner_ids is None

    # ---- 目标行（按范围过滤：非 all 只看自己 + 全公司目标）----
    target_stmt = select(SalesTarget).where(
        SalesTarget.period.like(prefix), SalesTarget.deleted_at.is_(None)
    )
    if not admin_view:
        from sqlalchemy import or_

        target_stmt = target_stmt.where(
            or_(SalesTarget.user_id == user.id, SalesTarget.user_id.is_(None))
        )
    targets = list((await session.execute(target_stmt)).scalars().all())

    # ---- 实际：销售额（非取消订单，按负责人 × 月）----
    # 注意不能用 to_char(created_at, 'YYYY-MM')：格式串是绑定参数，
    # SELECT 与 GROUP BY 的参数位不同，PG 无法判定表达式等价会报 GroupingError；
    # extract 的字段名是内联文本，两边渲染完全一致
    order_year = func.extract("year", SalesOrder.created_at)
    order_month = func.extract("month", SalesOrder.created_at)
    # 目标达成按**签单归属**算（文档 :61）：销售离职交接后，老订单的签单额
    # 仍计在原销售的目标达成里，不会因为换人跟进就从他名下消失
    sales_owner = func.coalesce(SalesOrder.sales_owner_id, SalesOrder.owner_id)
    sales_stmt = (
        select(order_month, sales_owner, func.sum(SalesOrder.total_amount))
        .where(
            SalesOrder.status != "cancelled",
            order_year == year,
        )
        .group_by(order_month, sales_owner)
    )
    customer_year = func.extract("year", Customer.created_at)
    customer_month = func.extract("month", Customer.created_at)
    customer_stmt = (
        select(customer_month, Customer.owner_id, func.count())
        .where(
            Customer.deleted_at.is_(None),
            customer_year == year,
        )
        .group_by(customer_month, Customer.owner_id)
    )
    if owner_ids is not None:
        # 销售额看"本范围的人签的单"，所以按签单归属过滤（与上面的分组同一列）；
        # 新客户仍是客户维度，按当前负责人
        sales_stmt = sales_stmt.where(sales_owner.in_(owner_ids or [0]))
        customer_stmt = customer_stmt.where(Customer.owner_id.in_(owner_ids or [0]))

    sales_actual: dict[tuple[str, int | None], float] = {}
    for m, owner_id, total in (await session.execute(sales_stmt)).all():
        key = (f"{year}-{int(m):02d}", owner_id)
        sales_actual[key] = sales_actual.get(key, 0.0) + float(total or 0)

    new_customer_actual: dict[tuple[str, int | None], int] = {}
    for m, owner_id, n in (await session.execute(customer_stmt)).all():
        key = (f"{year}-{int(m):02d}", owner_id)
        new_customer_actual[key] = new_customer_actual.get(key, 0) + int(n)

    # 复购（老客净额）：口径定义在 target_bases 里那一处，这里只按 (月份, 人) 取数。
    # 以前 repeat_customer_target 只存不算——目标页看不到它，等于设了没人管。
    repeat_by_owner = await target_bases.repeat_net_by_owner(session, user, year)

    def _repeat_actual(month: str, owner_id: int | None) -> float:
        """复购实际值。

        `owner_id is None` 的行是**全公司/团队目标**：销售额与新客在那一行把
        范围内所有人加总（见上面的 actual_for），复购也必须加总——
        原先直接取 `repeat_by_owner[None]`（"无签单归属"那一桶），
        于是那一行的复购实际≈0、差额一片负数、达成率 0%，主管每月看到的是错数。
        """
        key = month[5:]
        if owner_id is None:
            return round(sum(bucket.get(key, 0.0) for bucket in repeat_by_owner.values()), 2)
        return round(repeat_by_owner.get(owner_id, {}).get(key, 0.0), 2)

    def _sort_key(kv: tuple[tuple[str, int | None], object]) -> tuple[str, int]:
        month, owner = kv[0]
        return (month, owner if owner is not None else -1)


    # ---- 组装行：目标行 + 有实际但没设目标的（月，负责人）补零行 ----
    def actual_for(month: str, target_user_id: int | None) -> tuple[float, int]:
        if target_user_id is None:
            # 全公司目标：实际 = 可见范围内合计
            sales = sum(v for (m, _o), v in sales_actual.items() if m == month)
            new = sum(v for (m, _o), v in new_customer_actual.items() if m == month)
        else:
            sales = sales_actual.get((month, target_user_id), 0.0)
            new = new_customer_actual.get((month, target_user_id), 0)
        return sales, new

    rows: list[dict] = []
    seen: set[tuple[str, int | None]] = set()
    user_ids: set[int] = set()
    for t in targets:
        sales, new = actual_for(t.period, t.user_id)
        rows.append(
            {
                "target_id": t.id,
                "period": t.period,
                "user_id": t.user_id,
                "department_id": t.department_id,
                "new_customer_target": t.new_customer_target,
                "sales_target": float(t.sales_target or 0),
                "repeat_customer_target": float(t.repeat_customer_target or 0),
                "new_customer_actual": new,
                "sales_actual": round(sales, 2),
                "repeat_customer_actual": _repeat_actual(t.period, t.user_id),
                "remark": t.remark,
                # 乐观并发（第三批 §4.1.2）：界面把这一版的时间戳带回来编辑，
                # 中途被别人改过就能发现，而不是静默覆盖
                "updated_at": t.updated_at.isoformat() if t.updated_at else None,
            }
        )
        seen.add((t.period, t.user_id))
        if t.user_id:
            user_ids.add(t.user_id)

    for (month, owner_id), sales in sorted(sales_actual.items(), key=_sort_key):
        if (month, owner_id) in seen:
            continue
        rows.append(
            {
                "target_id": None,
                "period": month,
                "user_id": owner_id,
                "new_customer_target": 0,
                "sales_target": 0,
                "repeat_customer_target": 0,
                "new_customer_actual": new_customer_actual.get((month, owner_id), 0),
                "sales_actual": round(sales, 2),
                "repeat_customer_actual": _repeat_actual(month, owner_id),
                "remark": None,
            }
        )
        if owner_id:
            user_ids.add(owner_id)
    for (month, owner_id), new in sorted(new_customer_actual.items(), key=_sort_key):
        if (month, owner_id) in seen or owner_id is None:
            continue
        if any(r["period"] == month and r["user_id"] == owner_id for r in rows):
            continue
        rows.append(
            {
                "target_id": None,
                "period": month,
                "user_id": owner_id,
                "new_customer_target": 0,
                "sales_target": 0,
                "repeat_customer_target": 0,
                "new_customer_actual": new,
                "sales_actual": 0.0,
                "repeat_customer_actual": _repeat_actual(month, owner_id),
                "remark": None,
            }
        )
        user_ids.add(owner_id)

    # 只有复购、没有签单/新客的 (月, 人) 也要出行，否则设了复购目标的人看不到自己的数
    for owner_id, months in repeat_by_owner.items():
        for month_key, value in months.items():
            period = f"{year}-{month_key}"
            if (period, owner_id) in seen or not value:
                continue
            if any(r["period"] == period and r["user_id"] == owner_id for r in rows):
                continue
            rows.append(
                {
                    "target_id": None,
                    "period": period,
                    "user_id": owner_id,
                    "new_customer_target": 0,
                    "sales_target": 0,
                    "repeat_customer_target": 0,
                    "new_customer_actual": 0,
                    "sales_actual": 0.0,
                    "repeat_customer_actual": round(value, 2),
                    "remark": None,
                }
            )
            if owner_id:
                user_ids.add(owner_id)

    # ---- 差额与达成率（文档 §六 :121 / 场景17）：每个口径都要能回答"差多少" ----
    # **零基期不给百分比**：分母为 0 时算出来的是错误增长率（文档场景17 明确要求
    # "零基期不产生错误增长率"）。没设目标就是没设，不编一个百分比出来。
    # 这里统一后处理，而不是在三处组装行的地方各写一遍——三处各写必然漂移。
    for row in rows:
        target = float(row.get("sales_target") or 0)
        actual = float(row.get("sales_actual") or 0)
        row["sales_variance"] = round(actual - target, 2)
        row["sales_achievement"] = round(actual / target, 4) if target else None
        row["achievement_note"] = None if target else "未设销售目标，不计算达成率"
        new_target = int(row.get("new_customer_target") or 0)
        new_actual = int(row.get("new_customer_actual") or 0)
        row["new_customer_variance"] = new_actual - new_target
        row["new_customer_achievement"] = (
            round(new_actual / new_target, 4) if new_target else None
        )
        # 复购（老客净额）同样给差额与达成率：文档要求每个口径都能回答"差多少"
        repeat_target = float(row.get("repeat_customer_target") or 0)
        repeat_actual_row = float(row.get("repeat_customer_actual") or 0)
        row["repeat_customer_variance"] = round(repeat_actual_row - repeat_target, 2)
        row["repeat_customer_achievement"] = (
            round(repeat_actual_row / repeat_target, 4) if repeat_target else None
        )

    rows.sort(key=lambda r: (r["period"], r["user_id"] or 0))

    user_names: dict[int | None, str] = {None: "全公司"}
    if user_ids:
        from app.modules.user.model import User

        name_rows = await session.execute(select(User.id, User.name).where(User.id.in_(user_ids)))
        user_names.update({uid: name for uid, name in name_rows.all()})
    # 团队目标的名字要按**部门**查：只认 user_id 的话，部门目标会显示成"全公司"，
    # 主管会以为自己设的是全公司目标（功能对、标签错，最容易误导人）
    dept_ids = {r.get("department_id") for r in rows if r.get("department_id")}
    dept_names: dict[int, str] = {}
    if dept_ids:
        from app.modules.user.model import Department

        name_rows = await session.execute(
            select(Department.id, Department.name).where(Department.id.in_(dept_ids))
        )
        dept_names.update({did: name for did, name in name_rows.all()})
    for r in rows:
        if r.get("department_id"):
            r["user_name"] = dept_names.get(r["department_id"], f"部门#{r['department_id']}")
        elif r["user_id"] is None:
            r["user_name"] = "全公司"
        else:
            r["user_name"] = user_names.get(r["user_id"], f"#{r['user_id']}")
    return {"year": year, "rows": rows}


async def upsert_target(
    session: AsyncSession,
    *,
    user: CurrentUser,
    period: str,
    user_id: int | None,
    new_customer_target: int,
    sales_target: float,
    repeat_customer_target: float = 0,
    department_id: int | None = None,
    remark: str | None = None,
    expected_updated_at: datetime | None = None,
) -> tuple[SalesTarget, bool, dict | None]:
    """新建或更新一条目标，返回 `(行, 是否新建, 改前快照)`。

    第三批 §4.1.2 要求：唯一约束（库层 `uq_sales_targets_scope` 已加）、
    **乐观并发保护**、完整审计。这里做前两件的应用层部分：

    - 期间标准化（§4.1.6），`2026-1` 不再能与 `2026-01` 并存；
    - 个人目标与团队目标**互斥**，同时给就报错——否则这条到底算谁的说不清；
    - 目标值不得为负；
    - `FOR UPDATE` 先锁住那一行：两个人同时"新建"同一作用域时，唯一索引会让
      后到的插入失败，锁能让它读到已经建好的那一行、转成更新；
    - `expected_updated_at` 是**乐观并发**：调用方把它读到的时间戳带回来，
      对不上说明中途被别人改过 → 409，而不是把人家的改动静默覆盖掉。
    """
    period = normalize_period(period)
    if user_id is not None and department_id is not None:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            "个人目标与团队目标不能同时指定：要么给 user_id，要么给 department_id",
            422,
        )
    if (
        int(new_customer_target) < 0
        or float(sales_target or 0) < 0
        or float(repeat_customer_target or 0) < 0
    ):
        raise AppError(ErrorCode.PARAM_ERROR, "目标值不能为负", 422)

    stmt = select(SalesTarget).where(
        SalesTarget.period == period,
        SalesTarget.deleted_at.is_(None),
    )
    stmt = stmt.where(
        SalesTarget.user_id == user_id if user_id is not None else SalesTarget.user_id.is_(None)
    )
    # 团队目标：按部门匹配（与按人匹配互斥，见 model 里的语义说明）
    stmt = stmt.where(
        SalesTarget.department_id == department_id
        if department_id is not None
        else SalesTarget.department_id.is_(None)
    )
    row = (await session.execute(stmt.with_for_update())).scalars().first()
    before = target_snapshot(row)

    if row is not None and expected_updated_at is not None:
        current = row.updated_at or row.created_at
        # 比到秒：HTTP 传回来的时间戳精度可能低于库里的微秒
        if current is not None and current.replace(microsecond=0) != expected_updated_at.replace(
            microsecond=0
        ):
            raise AppError(
                ErrorCode.VERSION_CONFLICT,
                "这条目标刚被别人改过，请刷新后重新编辑（不要覆盖别人的改动）",
                409,
            )

    created = row is None
    if created:
        row = SalesTarget(
            period=period,
            user_id=user_id,
            department_id=department_id,
            created_by=user.id,
            created_at=datetime.now(UTC),
        )
        session.add(row)
    row.new_customer_target = int(new_customer_target)
    row.sales_target = Decimal(str(sales_target or 0))
    row.repeat_customer_target = Decimal(str(repeat_customer_target or 0))
    row.remark = remark
    row.updated_at = datetime.now(UTC)
    try:
        await session.flush()
    except IntegrityError as exc:
        # 唯一索引兜底：并发下两个请求都想新建同一作用域，先到的赢
        await session.rollback()
        raise AppError(
            ErrorCode.VERSION_CONFLICT,
            "该期间该作用域的目标已存在（并发创建），请刷新后重试",
            409,
        ) from exc
    await session.refresh(row)
    return row, created, before
