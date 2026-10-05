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

from app.core.data_scope import department_member_ids, scoped_owner_ids
from app.core.deps import CurrentUser
from app.core.errors import AppError, ErrorCode
from app.modules.analytics import target_bases
from app.modules.analytics.model import SalesTarget
from app.modules.customer.model import Customer
from app.modules.order.model import (
    OrderShipmentBatch,
    OrderShipmentBatchItem,
    SalesOrder,
    SalesOrderItem,
)
from app.modules.payment.model import PaymentRecord

#: 期间的标准形态。库层有同名 CHECK 约束，两边保持一字不差。
PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

#: 考核主口径（已确认 2026-10-05）：**确认回款**。
#: 签单与发货照常显示，但**不进差额**——§4.3 明确"不要把未选为考核口径的数字混入差额"。
ASSESS_BASIS = "received"
ASSESS_BASIS_LABEL = "确认回款"

#: 指标定义版本（§4.3 要求每个指标存"指标定义版本"）。
#: 口径一变就改这个字符串：历史报表据此自证是按哪一版算出来的。
METRIC_BASIS_VERSION = "2026-10-05.targets.2"

#: 每个指标的数据来源（随结果返回——业务要能回答"这个数是怎么来的"）
METRIC_SOURCES = {
    "sales_target": "sales_targets.sales_target（手工设定）",
    "assess_actual": "payment_records.received_amount，status=confirmed，按 received_date 归月",
    "sales_actual": "sales_orders.total_amount，status != cancelled，按 created_at 归月（展示口径，不进差额）",
    "shipped_actual": "order_shipment_batches × batch_items.shipped_qty × order_items.unit_price，按各批 actual_ship_date 归月（展示口径，不进差额）",
    "new_customer_actual": "customers.created_at 建档月 × owner_id 计数（过程指标）",
    "repeat_customer_actual": "期初老客池在本月的订单净额（口径见 analytics/target_bases.py）",
}


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


async def _visible_department_ids(session: AsyncSession, user: CurrentUser) -> list[int] | None:
    """当前用户能看到的**团队目标**所属部门；None = 不限（all）。

    为什么团队目标要单独判可见性：它的 `user_id` 是空的，用 `user_id` 判就只剩
    "全部纳入"或"全部排除"两种结果——旧实现选了前者（`or_(user_id == me,
    user_id.is_(None))`），于是**别的部门的团队目标也被带进我的列表**（第三批 §4.1.1）。
    self 范围返回空：团队目标不是"我自己的数据"。
    """
    scope = user.data_scope
    if scope == "all":
        return None
    if user.department_id is None:
        return []
    if scope == "department":
        return [user.department_id]
    if scope == "department_and_sub":
        from app.core.data_scope import department_subtree_ids_stmt

        rows = await session.execute(department_subtree_ids_stmt(user))
        return list(rows.scalars().all())
    return []


async def targets_with_actuals(session: AsyncSession, user: CurrentUser, year: int) -> dict:
    prefix = f"{year}-%"
    owner_ids = await _visible_owner_ids(session, user)
    admin_view = owner_ids is None

    # ---- 目标行（按范围过滤：非 all 分三层各按各的归属判）----
    target_stmt = select(SalesTarget).where(
        SalesTarget.period.like(prefix), SalesTarget.deleted_at.is_(None)
    )
    if not admin_view:
        from sqlalchemy import and_, or_

        visible_depts = await _visible_department_ids(session, user)
        conditions = [
            # ① 全公司目标：给所有人看（那是公司层面的数字，不是别人的私有数据）
            and_(SalesTarget.user_id.is_(None), SalesTarget.department_id.is_(None)),
            # ② 个人目标：本范围内的人。旧实现只比 `user.id`，
            #    于是主管看不到**同团队同事**的个人目标（§4.1.1 的另一半）
            SalesTarget.user_id.in_(owner_ids or [0]),
        ]
        if visible_depts is not None:
            # ③ 团队目标：本范围内的部门。旧实现靠 `user_id IS NULL` 匹配，
            #    把**别的部门**的团队目标也收了进来
            conditions.append(SalesTarget.department_id.in_(visible_depts or [0]))
        target_stmt = target_stmt.where(or_(*conditions))
    targets = list((await session.execute(target_stmt)).scalars().all())

    # ---- 实际：销售额（非取消订单，按负责人 × 月）----
    # 注意不能用 to_char(created_at, 'YYYY-MM')：格式串是绑定参数，
    # SELECT 与 GROUP BY 的参数位不同，PG 无法判定表达式等价会报 GroupingError；
    # extract 的字段名是内联文本，两边渲染完全一致
    order_year = func.extract("year", SalesOrder.created_at)
    order_month = func.extract("month", SalesOrder.created_at)
    # 目标达成按**签单归属**算（文档 :61「交接后保留历史业绩归属」）：销售离职交接后，
    # 老订单的签单额仍计在原销售的目标达成里，不会因为换人跟进就从他名下消失。
    # ⚠️ 应收/账龄页是**另一个口径**（责任口径＝当前负责人）：两页的数本来就不该相等。
    #    但同一个页面内的「计划/实绩/差额」必须同源（§4.1.3 的原缺陷就是混用）。
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

    # ---- 回款（**考核主口径**，已确认 2026-10-05）----
    # 财务确认日归月，只计已确认的回款。归属跟签单归属同一列（业绩口径）：
    # 同一行的三个口径必须同源，不能一个按签单人、一个按现负责人（§4.1.3）。
    received_year = func.extract("year", PaymentRecord.received_date)
    received_month = func.extract("month", PaymentRecord.received_date)
    received_stmt = (
        select(
            received_month,
            sales_owner,
            func.coalesce(func.sum(PaymentRecord.received_amount), 0),
        )
        .select_from(PaymentRecord)
        .join(SalesOrder, SalesOrder.id == PaymentRecord.order_id)
        .where(
            PaymentRecord.status == "confirmed",
            SalesOrder.status != "cancelled",
            received_year == year,
        )
        .group_by(received_month, sales_owner)
    )
    # ---- 发货（展示口径，不进差额）：按**实际发货批次 × 行实发数量**分摊（§4.1.4）----
    shipped_stmt = (
        select(
            func.extract("month", OrderShipmentBatch.actual_ship_date),
            sales_owner,
            func.coalesce(
                func.sum(OrderShipmentBatchItem.shipped_qty * SalesOrderItem.unit_price), 0
            ),
        )
        .select_from(OrderShipmentBatch)
        .join(
            OrderShipmentBatchItem,
            OrderShipmentBatchItem.batch_id == OrderShipmentBatch.id,
        )
        .join(SalesOrderItem, SalesOrderItem.id == OrderShipmentBatchItem.order_item_id)
        .join(SalesOrder, SalesOrder.id == OrderShipmentBatch.order_id)
        .where(
            OrderShipmentBatch.status == "shipped",
            OrderShipmentBatch.actual_ship_date.is_not(None),
            func.extract("year", OrderShipmentBatch.actual_ship_date) == year,
            SalesOrder.status != "cancelled",
        )
        .group_by(
            func.extract("month", OrderShipmentBatch.actual_ship_date), sales_owner
        )
    )
    if owner_ids is not None:
        received_stmt = received_stmt.where(sales_owner.in_(owner_ids or [0]))
        shipped_stmt = shipped_stmt.where(sales_owner.in_(owner_ids or [0]))

    received_actual: dict[tuple[str, int | None], float] = {}
    for m, owner_id, total in (await session.execute(received_stmt)).all():
        key = (f"{year}-{int(m):02d}", owner_id)
        received_actual[key] = received_actual.get(key, 0.0) + float(total or 0)

    shipped_actual: dict[tuple[str, int | None], float] = {}
    for m, owner_id, total in (await session.execute(shipped_stmt)).all():
        key = (f"{year}-{int(m):02d}", owner_id)
        shipped_actual[key] = shipped_actual.get(key, 0.0) + float(total or 0)

    # 复购（老客净额）：口径定义在 target_bases 里那一处，这里只按 (月份, 人) 取数。
    # 以前 repeat_customer_target 只存不算——目标页看不到它，等于设了没人管。
    repeat_by_owner = await target_bases.repeat_net_by_owner(session, user, year)

    # ---- 团队目标的成员集合（第三批 §4.1.1）----
    # 团队目标行（user_id 为空、department_id 有值）的**计划/实绩/差额必须同源**：
    # 成员集合先查好，下面的实际值只在这个集合里加总。旧实现让 team 行走
    # "可见范围合计"，等于给部门目标配了公司数字。
    dept_members: dict[int, list[int]] = {}
    for t in targets:
        if t.department_id is not None and t.department_id not in dept_members:
            dept_members[t.department_id] = await department_member_ids(
                session, t.department_id
            )

    def _repeat_actual(month: str, owner_id: int | None, department_id: int | None = None) -> float:
        """复购实际值。

        `owner_id is None` 的行是**全公司/团队目标**：销售额与新客在那一行把
        范围内所有人加总（见上面的 actual_for），复购也必须加总——
        原先直接取 `repeat_by_owner[None]`（"无签单归属"那一桶），
        于是那一行的复购实际≈0、差额一片负数、达成率 0%，主管每月看到的是错数。

        团队目标（`department_id` 有值）只在**本部门成员**里加总，
        不能拿全公司数字顶替。
        """
        key = month[5:]
        if department_id is not None:
            members = set(dept_members.get(department_id, []))
            return round(
                sum(v.get(key, 0.0) for oid, v in repeat_by_owner.items() if oid in members), 2
            )
        if owner_id is None:
            return round(sum(bucket.get(key, 0.0) for bucket in repeat_by_owner.values()), 2)
        return round(repeat_by_owner.get(owner_id, {}).get(key, 0.0), 2)

    def _sort_key(kv: tuple[tuple[str, int | None], object]) -> tuple[str, int]:
        month, owner = kv[0]
        return (month, owner if owner is not None else -1)


    # ---- 组装行：目标行 + 有实际但没设目标的（月，负责人）补零行 ----
    def actual_for(
        month: str, target_user_id: int | None, department_id: int | None = None
    ) -> tuple[float, float, float, int]:
        """某个 (月, 作用域) 的四个实际值：签单 / 回款（考核）/ 发货 / 新客。

        三个销售口径都算出来是为了"都能显示"，但**只有考核口径进差额**（§4.3）：
        已确认考核主口径是**确认回款**。
        """
        def pick(source: dict, restrict_to: set[int] | None) -> float:
            if restrict_to is not None:
                return sum(
                    v for (m, o), v in source.items() if m == month and o in restrict_to
                )
            if target_user_id is None:
                return sum(v for (m, _o), v in source.items() if m == month)
            return source.get((month, target_user_id), 0.0)

        if department_id is not None:
            members = set(dept_members.get(department_id, []))
            return (
                pick(sales_actual, members),
                pick(received_actual, members),
                pick(shipped_actual, members),
                int(pick(new_customer_actual, members)),
            )
        return (
            pick(sales_actual, None),
            pick(received_actual, None),
            pick(shipped_actual, None),
            int(pick(new_customer_actual, None)),
        )

    rows: list[dict] = []
    seen: set[tuple[str, int | None]] = set()
    user_ids: set[int] = set()
    for t in targets:
        signed, received, shipped, new = actual_for(t.period, t.user_id, t.department_id)
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
                # 考核口径 = **确认回款**（已确认 2026-10-05）；差额与达成率都基于它，
                # 另外两个口径只展示、不混进差额（§4.3）
                "assess_basis": ASSESS_BASIS,
                "assess_basis_label": ASSESS_BASIS_LABEL,
                "assess_actual": round(received, 2),
                "sales_actual": round(signed, 2),
                "shipped_actual": round(shipped, 2),
                "received_actual": round(received, 2),
                "repeat_customer_actual": _repeat_actual(t.period, t.user_id, t.department_id),
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

    # 补零行（有实际但没设目标的 (月, 人)）也要带齐三个口径与考核字段，
    # 否则前端要为"有目标/没目标"写两套渲染，而且差额字段的语义会不一致。
    for row in rows:
        month, owner_id = row["period"], row.get("user_id")
        row.setdefault("assess_basis", ASSESS_BASIS)
        row.setdefault("assess_basis_label", ASSESS_BASIS_LABEL)
        row.setdefault(
            "received_actual", round(received_actual.get((month, owner_id), 0.0), 2)
        )
        row.setdefault(
            "shipped_actual", round(shipped_actual.get((month, owner_id), 0.0), 2)
        )
        row.setdefault("assess_actual", row["received_actual"])

    # ---- 差额与达成率（文档 §六 :121 / 场景17）：每个口径都要能回答"差多少" ----
    # **零基期不给百分比**：分母为 0 时算出来的是错误增长率（文档场景17 明确要求
    # "零基期不产生错误增长率"）。没设目标就是没设，不编一个百分比出来。
    # 这里统一后处理，而不是在三处组装行的地方各写一遍——三处各写必然漂移。
    for row in rows:
        target = float(row.get("sales_target") or 0)
        # 差额与达成率用**考核口径**（确认回款）。`sales_actual`（签单）与
        # `shipped_actual`（发货）照常展示，但绝不混进差额——§4.3 明确要求。
        actual = float(row.get("assess_actual") or 0)
        row["sales_variance"] = round(actual - target, 2)
        row["sales_achievement"] = round(actual / target, 4) if target else None
        row["achievement_note"] = (
            None if target else "未设销售目标，不计算达成率"
        )
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
    return {
        "year": year,
        "rows": rows,
        # §4.3：目标值、指标定义版本、实际值、差额、数据来源、计算时间都要随结果给出。
        # 目标值/实际值/差额在每一行里；这里给"这一版口径是什么、数从哪来、什么时候算的"。
        "metric_basis_version": METRIC_BASIS_VERSION,
        "assess_basis": ASSESS_BASIS,
        "assess_basis_label": ASSESS_BASIS_LABEL,
        "attribution_note": (
            "归属口径：一律按订单**当前负责人**（决策「交接后归现负责人」）——"
            "计划、实绩、差额同一政策"
        ),
        "sources": METRIC_SOURCES,
        "computed_at": datetime.now(UTC).isoformat(),
    }


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
