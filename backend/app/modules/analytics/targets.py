"""目标 vs 实际（领导模块⑧）。

实际值的两个来源（都按数据范围过滤）：
- 销售额 = 非取消订单的 total_amount，按负责人 × 月聚合；
- 新客户 = 客户档案按 created_at 的负责人 × 月计数。

将来聚水潭接入后，"实际销售额"可切换为出库金额——目标表结构不动。
"""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.data_scope import scoped_owner_ids
from app.core.deps import CurrentUser
from app.core.errors import AppError, ErrorCode
from app.modules.analytics.model import SalesTarget
from app.modules.customer.model import Customer
from app.modules.order.model import SalesOrder


def _validate_period(period: str) -> None:
    try:
        datetime.strptime(period, "%Y-%m")
    except ValueError as exc:
        raise AppError(ErrorCode.PARAM_ERROR, "period 必须是 YYYY-MM 格式", 422) from exc


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
                "new_customer_target": t.new_customer_target,
                "sales_target": float(t.sales_target or 0),
                "new_customer_actual": new,
                "sales_actual": round(sales, 2),
                "remark": t.remark,
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
                "new_customer_actual": new_customer_actual.get((month, owner_id), 0),
                "sales_actual": round(sales, 2),
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
                "new_customer_actual": new,
                "sales_actual": 0.0,
                "remark": None,
            }
        )
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

    rows.sort(key=lambda r: (r["period"], r["user_id"] or 0))

    user_names: dict[int | None, str] = {None: "全公司"}
    if user_ids:
        from app.modules.user.model import User

        name_rows = await session.execute(select(User.id, User.name).where(User.id.in_(user_ids)))
        user_names.update({uid: name for uid, name in name_rows.all()})
    for r in rows:
        r["user_name"] = "全公司" if r["user_id"] is None else user_names.get(r["user_id"], f"#{r['user_id']}")
    return {"year": year, "rows": rows}


async def upsert_target(
    session: AsyncSession,
    *,
    user: CurrentUser,
    period: str,
    user_id: int | None,
    new_customer_target: int,
    sales_target: float,
    remark: str | None = None,
) -> SalesTarget:
    _validate_period(period)
    stmt = select(SalesTarget).where(
        SalesTarget.period == period,
        SalesTarget.deleted_at.is_(None),
    )
    stmt = stmt.where(
        SalesTarget.user_id == user_id if user_id is not None else SalesTarget.user_id.is_(None)
    )
    row = (await session.execute(stmt)).scalars().first()
    if row is None:
        row = SalesTarget(
            period=period,
            user_id=user_id,
            created_by=user.id,
            created_at=datetime.now(UTC),
        )
        session.add(row)
    row.new_customer_target = new_customer_target
    row.sales_target = sales_target
    row.remark = remark
    await session.flush()
    await session.refresh(row)
    return row
