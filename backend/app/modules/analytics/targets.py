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
# 业务时间基准（第九批 §9.10）：SQL 侧归年/归月显式指定业务时区
from app.core.timebase import business_month, business_year
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
#: .3：回款归月依据由「到账日」改为「财务确认时间」（返工单第 2 条）。
#: .4：新客**考核**口径由「建档月」改为「首次有效成交月」，建档数降为过程指标另列；
#:     冻结快照的读取与补行补上数据范围判断（返工单 P1-1 / P1-2）。
METRIC_BASIS_VERSION = "2026-10-06.targets.4"

#: 每个指标的数据来源（随结果返回——业务要能回答"这个数是怎么来的"）
METRIC_SOURCES = {
    "sales_target": "sales_targets.sales_target（手工设定）",
    "assess_actual": (
        "payment_records.received_amount，status=confirmed，"
        "按 confirmed_at（财务确认时间）归月；缺确认时间的记录不计入"
    ),
    "sales_actual": "sales_orders.total_amount，status != cancelled，按 created_at 归月（展示口径，不进差额）",
    "shipped_actual": "order_shipment_batches × batch_items.shipped_qty × order_items.unit_price，按各批 actual_ship_date 归月（展示口径，不进差额）",
    "new_customer_actual": (
        "该客户**首笔非取消订单**落在本月 → 计 1 个（考核口径）。"
        "客户集合取自月度基准快照（analytics/target_bases.py），"
        "与年度统计、下钻明细、冻结快照同源"
    ),
    "new_customer_created_actual": (
        "customers.created_at 建档月 × owner_id 计数（**过程指标**，"
        "只展示、不进差额与达成率）"
    ),
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


def _filter_frozen_by_scope(
    frozen: dict[str, dict[tuple[str, str], object]],
    *,
    allowed_users: set[int] | None,
    allowed_depts: set[int],
) -> dict[str, dict[tuple[str, str], object]]:
    """把快照裁到操作者能看的作用域（返工单：冻结实绩绕过数据范围，P1）。

    **为什么要在"读取"时就裁，而不是在展示时过滤**：快照按
    (期间, 作用域, 指标) 存，一次读回**整年所有作用域**，它有三个消费点 ——
    补零行、覆盖实时值、查人员名称。任何一处漏判都是越权，而且漏出去的是
    "某个人的签单额与回款额"这种最敏感的东西。所以在门口裁一次，
    后面三处消费的都是裁过的数据；补零行那处再判一次是最后一道（纵深防御，
    安全判断重复一遍是划算的，漏一遍的代价是业绩泄露）。

    `allowed_users is None` = 全公司范围，不裁。
    公司汇总行（作用域键为 `company`，即 `user_id is None`）保留：
    那是既定的"全公司目标人人可见"规则，它只有一个汇总值，
    不带任何个人的数，不会借它把谁的业绩漏出去。
    """
    if allowed_users is None:
        return frozen

    from app.modules.analytics.target_actuals import parse_scope_key

    out: dict[str, dict[tuple[str, str], object]] = {}
    for period, snap in frozen.items():
        kept: dict[tuple[str, str], object] = {}
        for (scope_key, metric), value in snap.items():
            user_id, dept_id = parse_scope_key(scope_key)
            if dept_id is not None:
                if dept_id in allowed_depts:
                    kept[(scope_key, metric)] = value
            elif user_id is None or user_id in allowed_users:
                kept[(scope_key, metric)] = value
        # 空 dict 也要写进去：`actual_frozen` 靠"期间在不在 frozen 里"判断，
        # 少一个键会让已结账的那一期被标成"实时值"。
        out[period] = kept
    return out


async def targets_with_actuals(
    session: AsyncSession, user: CurrentUser, year: int, *, ignore_snapshot: bool = False
) -> dict:
    prefix = f"{year}-%"
    owner_ids = await _visible_owner_ids(session, user)
    admin_view = owner_ids is None
    # 团队目标所属部门的可见范围，同时也是**快照裁剪**的依据（见下面补行那段）。
    # 提前算一次、两处共用：各算各的迟早漂移，而这里的漂移就是越权。
    visible_depts = await _visible_department_ids(session, user)

    # 这一年的实绩快照（第三批 §4.1.5 后半）：按"期间"分好组的已结账数字。
    # 逐期决定用存档还是实时——同一年里可能几个月结了账、几个月还没结。
    #
    # `ignore_snapshot=True` 是给**结账/重算**用的：那两件事要的恰恰是"现在的数"，
    # 若读到上一次的存档，重算就变成"把上次的值再抄一遍"，等于没算。
    from app.modules.analytics import target_actuals

    frozen = {} if ignore_snapshot else await target_actuals.frozen_map(session, year)
    # 进门先裁到本例账号能看的作用域（见函数说明）：后面补零行、覆盖实时值、
    # 查人员名称三处都只消费裁过的数据，不会再出现"漏判一处就漏业绩"。
    frozen = _filter_frozen_by_scope(
        frozen,
        allowed_users=None if admin_view else set(owner_ids or []),
        allowed_depts=set(visible_depts or []),
    )

    # ---- 目标行（按范围过滤：非 all 分三层各按各的归属判）----
    target_stmt = select(SalesTarget).where(
        SalesTarget.period.like(prefix), SalesTarget.deleted_at.is_(None)
    )
    if not admin_view:
        from sqlalchemy import and_, or_

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
    order_year = business_year(SalesOrder.created_at)
    order_month = business_month(SalesOrder.created_at)
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
    customer_year = business_year(Customer.created_at)
    customer_month = business_month(Customer.created_at)
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

    # ---- 新客：**两条口径，考核只认其中一条**（返工单：新客口径，P1）----
    #
    #  · `new_customer_actual`（考核口径）= **首次有效成交**落在本月的客户数。
    #    与 `annual_bases.new_by_first_deal`、下钻明细、冻结快照全部同源。
    #  · `new_customer_created_actual`（过程指标）= 客户档案在本月**新建**的数量。
    #    它衡量的是"跑了多少新客"，跟"成了多少"是两件事，所以**另列一栏**，
    #    不参与差额与达成率。
    #
    # 老实现把后者当成了考核实绩：9 月只新建一个客户、一单没成，目标页照样显示
    # "新客实绩 1"。同一页里汇总按建档、明细按首成交，两个数字还会互相打脸。
    new_customer_actual: dict[tuple[str, int | None], int] = {}
    #: 建档口径（过程指标）。**保留 **——审查方明确说它可以另列，不是要删掉。
    new_customer_created: dict[tuple[str, int | None], int] = {}
    in_scope = None if owner_ids is None else set(owner_ids)
    for m, owner_id, n in (await session.execute(customer_stmt)).all():
        key = (f"{year}-{int(m):02d}", owner_id)
        new_customer_created[key] = new_customer_created.get(key, 0) + int(n)
    for period, owner_id, _cid, _name, _at in await target_bases.new_customer_rows(
        session, year
    ):
        if in_scope is not None and (owner_id is None or owner_id not in in_scope):
            continue
        key = (period, owner_id)
        new_customer_actual[key] = new_customer_actual.get(key, 0) + 1

    # ---- 回款（**考核主口径**，已确认 2026-10-05）----
    # 归月依据 = **财务确认时间**（`confirmed_at`），不是到账日（返工单第 2 条）：
    # 销售登记的是"客户什么时候打的钱"，**财务确认才是这笔钱算数的时点**。
    # 跨月确认时（1 月底到账、2 月初才确认）老实现把它归进 1 月，与"确认回款"这个名字不符。
    # `extract` 按**数据库会话时区**取年月——本库是 Asia/Shanghai（北京时间），
    # 与全项目其它归月（订单创建月、发货月）同一套口径。
    #
    # 归属跟签单归属同一列（业绩口径）：同一行的三个口径必须同源，
    # 不能一个按签单人、一个按现负责人（§4.1.3）。
    received_year = business_year(PaymentRecord.confirmed_at)
    received_month = business_month(PaymentRecord.confirmed_at)
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
            # 缺确认时间的记录**归不了月**：先不算，由下面那个计数提示业务去补录。
            # 不能拿 received_date 顶替——那会让同一列里混进两个口径，
            # 以后没人分得清哪笔是按什么算的（返工单第 2 条的原始缺陷）。
            PaymentRecord.confirmed_at.is_not(None),
            SalesOrder.status != "cancelled",
            received_year == year,
        )
        .group_by(received_month, sales_owner)
    )
    # 「已确认、却没记确认时间」的回款笔数：按**到账日的年份**判断它大概属于哪一年
    # （没有确认时间，只能拿这个判），只用于提示补录，不进任何金额。
    missing_confirmed_stmt = (
        select(func.count())
        .select_from(PaymentRecord)
        .join(SalesOrder, SalesOrder.id == PaymentRecord.order_id)
        .where(
            PaymentRecord.status == "confirmed",
            PaymentRecord.confirmed_at.is_(None),
            SalesOrder.status != "cancelled",
            func.extract("year", PaymentRecord.received_date) == year,
        )
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
        missing_confirmed_stmt = missing_confirmed_stmt.where(
            sales_owner.in_(owner_ids or [0])
        )

    received_actual: dict[tuple[str, int | None], float] = {}
    for m, owner_id, total in (await session.execute(received_stmt)).all():
        key = (f"{year}-{int(m):02d}", owner_id)
        received_actual[key] = received_actual.get(key, 0.0) + float(total or 0)

    # 本年度「已确认但缺确认时间」的笔数（只提示，不进金额）
    missing_confirmed_count = int(
        (await session.execute(missing_confirmed_stmt)).scalar_one() or 0
    )

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

    def _sort_key(key: tuple[str, int | None]) -> tuple[str, int]:
        """按「月，人」排序；无归属人（None）排在本月最后一个。"""
        month, owner = key
        return (month, owner if owner is not None else -1)


    # ---- 组装行：目标行 + 有实际但没设目标的（月，负责人）补零行 ----
    def actual_for(
        month: str, target_user_id: int | None, department_id: int | None = None
    ) -> tuple[float, float, float, int, int]:
        """某个 (月, 作用域) 的实际值：签单 / 回款（考核）/ 发货 / 新客 / 新建档。

        三个销售口径都算出来是为了"都能显示"，但**只有考核口径进差额**（§4.3）：
        已确认考核主口径是**确认回款**。
        新客那一项**考核看首次成交**（`new_customer_actual`），
        新建档数量只作过程指标另列（`new_customer_created_actual`）——
        两者都要返回，但只有前者参与差额与达成率。

        这里**只看实时值**；已结账期间的存档值在下面的统一后处理里覆盖
        （那边一处收口，三个地方各写一遍必然漂移——第一版就漏了"补零行"那条路）。
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
                int(pick(new_customer_created, members)),
            )
        return (
            pick(sales_actual, None),
            pick(received_actual, None),
            pick(shipped_actual, None),
            int(pick(new_customer_actual, None)),
            int(pick(new_customer_created, None)),
        )

    rows: list[dict] = []
    seen: set[tuple[str, int | None]] = set()
    user_ids: set[int] = set()
    for t in targets:
        signed, received, shipped, new, new_created = actual_for(
            t.period, t.user_id, t.department_id
        )
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
                # 过程指标：本月**新建档**的客户数（不参与差额与达成率）。
                # 与考核值合看才有意义："建档 8 个、成交 1 个"说明前端转化有问题。
                "new_customer_created_actual": new_created,
                # 考核口径 = **确认回款**（已确认 2026-10-05）；差额与达成率都基于它，
                # 另外两个口径只展示、不混进差额（§4.3）
                "assess_basis": ASSESS_BASIS,
                "assess_basis_label": ASSESS_BASIS_LABEL,
                "assess_actual": round(received, 2),
                "sales_actual": round(signed, 2),
                "shipped_actual": round(shipped, 2),
                "received_actual": round(received, 2),
                # 这一期的数字是存档值还是实时算的（§4.1.5）。
                # 界面上要说清楚：存档值不会因为后来的退货变小，
                # 实时值会——两者对不上时，先看这个标志再怀疑数据。
                "actual_frozen": t.period in frozen,
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

    # ---- 组装行：目标行 + 有实际值但没设目标的（月，负责人）补零行 ----
    #
    # ⚠️ 补零行的键必须来自**所有**实际值来源，不能只看签单。
    # 1 月签单、2 月才回款是常态：只按 `sales_actual` 的键生成行的话，
    # 2 月根本不会有这一行 —— 那笔回款在报表上凭空消失，而它明明计进了
    # 6 月的合计（返工单第 2 条把归月依据改成"财务确认时间"之后，
    # 签单月与回款月不一致的情形比原来多得多）。
    # 新客那一维排除 `owner_id is None`：无负责人的新客出成一行没有意义。
    actual_keys: set[tuple[str, int | None]] = (
        set(sales_actual) | set(received_actual) | set(shipped_actual)
    )
    actual_keys |= {key for key in new_customer_actual if key[1] is not None}
    # 只有复购、没有签单/新客的 (月, 人) 也要出行，否则设了复购目标的人看不到自己的数
    for repeat_owner, months in repeat_by_owner.items():
        for month_key, value in months.items():
            if value:
                actual_keys.add((f"{year}-{month_key}", repeat_owner))

    # ⚠️ **已结账期间的行不能依赖实时值**（2026-10-06 实测抓到的真 bug）。
    #
    # 上面那几个键都来自**实时**聚合。结账之后客户退货、回款被驳回，
    # 实时值就没了 —— 于是那一行**根本不生成**，后面"用存档覆盖"自然无从发生，
    # 报表上那一期的数字凭空消失。可冻结的全部意义就是"结账之后这张报表不再变"，
    # 这比数字算错更糟：领导查数发现上个月的数不见了。
    #
    # 所以：**该期存档里出现过的（期间, 人）也要出行**。用 "sales" 作锚点——
    # 结账时各指标是一起写的（见 `target_actuals.freeze_period`），取一项即可。
    # 部门作用域由目标行承载，这里不补（补零行只按人，与上面几个来源一致）。
    #
    # ⚠️ **范围判断必须加在这里**（返工单：冻结实绩绕过数据范围，P1）。
    # `frozen_map` 读的是**整年所有作用域**的快照，而上面那几个键都来自
    # 已按范围过滤过的实时聚合 —— 只有这一段没有判断，于是"仅本人"的账号
    # 会看到别人的（期间, 人）行，带着别人冻结的签单额与回款额。
    # 这是我上一轮修"结账后那行消失"时引入的：只顾着把行补回来，忘了补范围。
    #
    # 判据与实时查询**同一套口径**，不因为"这一期结过账"就放宽：
    #  · 部门快照：只在我能看的部门里才用它（`department` 范围只含自己那个部门，
    #    `department_and_sub` 含子树，`self` 是空集）。
    #  · 个人快照：只在我能看的人里才补行；`user_id is None` 是**公司汇总行**，
    #    属于既定的"全公司目标人人可见"规则，保留 —— 它只有一个汇总值，
    #    不带任何个人的数，不会借它把某人的业绩漏出去。
    allowed_users = None if owner_ids is None else set(owner_ids)
    allowed_depts = None if visible_depts is None else set(visible_depts)
    for frozen_period, snap in frozen.items():
        for scope_key, metric in snap:
            if metric != "sales":
                continue
            frozen_user_id, frozen_dept_id = target_actuals.parse_scope_key(scope_key)
            if allowed_users is not None:
                if frozen_dept_id is not None:
                    if frozen_dept_id not in (allowed_depts or set()):
                        continue
                elif frozen_user_id is not None and frozen_user_id not in allowed_users:
                    continue
            if frozen_dept_id is not None:
                continue
            actual_keys.add((frozen_period, frozen_user_id))

    for month, owner_id in sorted(actual_keys, key=_sort_key):
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
                "new_customer_actual": int(new_customer_actual.get((month, owner_id), 0)),
                "new_customer_created_actual": int(
                    new_customer_created.get((month, owner_id), 0)
                ),
                "sales_actual": round(sales_actual.get((month, owner_id), 0.0), 2),
                "repeat_customer_actual": _repeat_actual(month, owner_id),
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
        # ---- 已结账的期间：用存档值覆盖实时算出来的数（第三批 §4.1.5 后半）----
        #
        # **必须放在这里统一做，不能塞进 actual_for**：报表有三种组装行的路径
        # （有目标的、有实绩没设目标的补零行、只有复购的），其中补零行根本不走
        # actual_for —— 第一版就栽在这：设了目标的人结账后数字冻住了，
        # 没设目标的人照样漂移。放一处，三条路都盖到。
        #
        # 覆盖要发生在**差额与达成率之前**（就在下面那个循环里），否则差额还是按实时值算的。
        scope_key = target_actuals.scope_key_of(row.get("user_id"), row.get("department_id"))
        snap = frozen.get(month) or {}
        # 以「签单」这一项在不在作为"这一行冻过"的标记：结账时四项是一起写的
        # （见 target_actuals.freeze_period），拿它当锚点最稳。
        frozen_row = (scope_key, "sales") in snap
        if frozen_row:
            # 逐项取、缺了就回落实时值：结账永远写满四项，但万一历史数据不全，
            # 宁可显示实时值也不要 KeyError 把整张报表打挂。
            def frozen_value(metric: str, live):
                key = (scope_key, metric)
                if key not in snap:
                    return live
                return int(snap[key]) if metric == "new_customer" else round(float(snap[key]), 2)

            row["sales_actual"] = frozen_value("sales", row.get("sales_actual") or 0)
            row["received_actual"] = frozen_value("received", row.get("received_actual") or 0)
            row["shipped_actual"] = frozen_value("shipped", row.get("shipped_actual") or 0)
            row["new_customer_actual"] = frozen_value(
                "new_customer", row.get("new_customer_actual") or 0
            )
            # 复购（老客净额）也在冻结之列（返工单第 4 条）：
            # 同一行里的四个数一起冻，不能只冻三个、留一个实时算
            row["repeat_customer_actual"] = frozen_value(
                "repeat_net", row.get("repeat_customer_actual") or 0
            )
            row["assess_actual"] = row["received_actual"]
        # 这一行的数字是**存档值**还是**实时算的**。界面上必须说清楚：
        # 存档值不会因为后来的退货变小，实时值会——两者对不上时先看这个标志。
        row["actual_frozen"] = frozen_row

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
        # ⚠️ 这段会**原样显示在页面上**，别用 markdown 星号加粗——
        # 前端是纯文本渲染，星号会跟着一起露出来（`**签单归属**`）。
        # 要强调用「」或书名号。
        "attribution_note": (
            "归属口径（业绩口径）：一律按订单「签单归属」（`sales_orders.sales_owner_id`，"
            "为空才回落到 owner_id）——文档 :61「交接后保留历史业绩归属」，钱算签单人。"
            "汇总、差额、下钻明细同一政策：点开明细加起来的数必须等于这一行。"
            "应收/账龄页用的是责任口径（当前负责人），两页的数本来就不该相等"
        ),
        # 「已确认、却没记确认时间」的回款笔数（本年度、本人范围内）：
        # 这些钱**没有进任何金额**，界面上必须提示业务去补录，
        # 否则用户看到"回款比实际少"却不知道少在哪（返工单第 2 条的收尾）。
        "missing_confirmed_at_count": missing_confirmed_count,
        "missing_confirmed_at_note": (
            f"有 {missing_confirmed_count} 笔已确认回款没记确认时间，未计入任何月份，"
            "请到「回款管理」补填确认时间"
            if missing_confirmed_count
            else None
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


# --------------------------------------------------------------- 可追溯明细（§4.3）

#: 可以下钻到业务记录的指标。键与 `targets_with_actuals` 返回的行字段对应。
DRILLDOWN_METRICS = {
    "signed": "签单额（sales_orders）",
    "shipped": "发货额（发货批次 × 行实发数量）",
    "received": "回款额（考核口径，payment_records）",
    "new_customer": "新客（首次成交口径，customers）",
    "repeat_net": "老客净额（期初老客池的订单）",
}

#: 下钻一次最多返回多少条明细（再多的用合计与筛选条件表达，不塞爆响应）
DRILLDOWN_LIMIT = 200


def _fmt_at(at) -> str | None:
    """明细里的业务时间写成人看得懂的格式。

    老实现直接 `isoformat()`，界面会原样出现 `2026-01-05T10:30:00+00:00`
    ——机器格式不说，还是 UTC 表示，看着比北京时间早 8 小时（时间线那次已经吃过这个亏）。
    `date` 类（如发货日）保持 `YYYY-MM-DD` 不变。
    """
    if at is None:
        return None
    if isinstance(at, datetime):
        if at.tzinfo is None:
            at = at.replace(tzinfo=UTC)
        return at.astimezone().strftime("%Y-%m-%d %H:%M")
    if hasattr(at, "isoformat"):
        return at.isoformat()
    return str(at)


def _row(record_type: str, record_id: int, label: str | None, owner_id: int | None,
         amount: float, at) -> dict:
    return {
        "record_type": record_type,
        "id": record_id,
        "label": label,
        "owner_id": owner_id,
        "amount": round(amount, 2),
        "date": _fmt_at(at),
    }


async def drilldown(
    session: AsyncSession,
    user: CurrentUser,
    *,
    period: str,
    metric: str,
    user_id: int | None = None,
    department_id: int | None = None,
    unlimited: bool = False,
    ignore_snapshot: bool = False,
) -> dict:
    """把某个指标的某个 (期间, 作用域) 拆到**具体业务记录**（§4.3）。

    为什么必须有它：文档要求"所有断言应定位到业务记录或批次，而不是只比汇总数字"。
    目标页给出的差额要能一路点回到是哪几张单、哪几个发货批次、哪几笔回款。
    合计与 `targets_with_actuals` / `annual_bases` 用的是**同一套口径与筛选**，
    否则"明细加起来对不上汇总"比没有明细更糟。

    **已结账的期间读存档明细**（返工单第 4 条）：那一期的汇总已经冻住了，
    明细要是还实时算，结账之后发生退货/取消就会比汇总少一笔。
    存档是结账时和汇总**同一次**写下来的，所以永远对得上。
    返回里 `source` 字段说明这一份是 `snapshot`（存档）还是 `live`（实时）。

    `unlimited=True` 给**结账**用：要落档的是完整明细，不能按展示上限截断，
    否则存下来的明细天生不全，将来加总与汇总对不上。

    `ignore_snapshot=True` 是给**结账 / 重算时收集明细**用的：那两件事要的恰恰是
    "按现在数据算出来的明细"，读存档就等于把上次的明细又抄一遍（等于没重算）。
    汇总那边同一个开关叫 `ignore_snapshot`（见 `targets_with_actuals`），名字保持一致。
    """
    period = normalize_period(period)
    if metric not in DRILLDOWN_METRICS:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"未知指标：{metric}；可选：{'、'.join(DRILLDOWN_METRICS)}",
            422,
        )
    if user_id is not None and department_id is not None:
        raise AppError(ErrorCode.PARAM_ERROR, "user_id 与 department_id 不能同时指定", 422)

    # 作用域 → 负责人集合（None = all，不限）
    scope_ids = await _visible_owner_ids(session, user)
    if department_id is not None:
        allowed = await _visible_department_ids(session, user)
        if allowed is not None and department_id not in allowed:
            raise AppError(ErrorCode.DATA_SCOPE_DENIED, "无权查看该部门的指标明细", 403)
        members = await department_member_ids(session, department_id)
        scope_ids = (
            members if scope_ids is None else [x for x in members if x in set(scope_ids)]
        )
        scope_label = f"部门#{department_id}（含下级）"
    elif user_id is not None:
        if scope_ids is not None and user_id not in scope_ids:
            raise AppError(ErrorCode.DATA_SCOPE_DENIED, "无权查看该负责人的指标明细", 403)
        scope_ids = [user_id]
        scope_label = f"负责人#{user_id}"
    else:
        scope_label = "当前数据范围（不限人）" if scope_ids is None else "当前数据范围"

    year, month = int(period[:4]), int(period[5:7])
    items: list[dict] = []

    #: 业绩归属（签单归属），与汇总 `targets_with_actuals` 用的是**同一个表达式**。
    #: 老实现这里直接拿 `SalesOrder.owner_id`（当前负责人）：汇总按签单人分、
    #: 明细按现负责人分，交接过的单子上点开明细永远对不上汇总（返工单第 3 条）。
    #: 注意 `coalesce` 只是"没填签单人时回落"，真正要修的是**两处归属列必须同源**。
    sales_owner = func.coalesce(SalesOrder.sales_owner_id, SalesOrder.owner_id)

    # 已结账的期间读**存档明细**（返工单第 4 条）：汇总已经冻住，
    # 明细要是还实时算，结账之后一张退货单就会让两边差一笔。
    # 判据用"期间结过账"而不是"存档非空"——某期确实没有回款时，
    # 空明细才是正确答案，回落到实时算会把结账之后的新回款补进来。
    #
    # ⚠️ 但**结账/重算时来收集明细**必须走实时（`ignore_snapshot=True`）：
    # 那时 archive 里躺的正是上一次的明细，读它等于把旧明细原样抄回去，
    # "重算"变成什么都没干（实测：改过存档明细之后重算，明细纹丝不动）。
    from app.modules.analytics import target_actuals

    snapshot_frozen = (
        False if ignore_snapshot else await target_actuals.is_frozen(session, period)
    )
    if snapshot_frozen:
        allowed = None if scope_ids is None else set(scope_ids)
        for item in await target_actuals.frozen_items(session, period, metric):
            if allowed is not None and item.get("owner_id") not in allowed:
                continue
            items.append(item)
    elif metric == "signed":
        stmt = select(
            SalesOrder.id, SalesOrder.order_no, sales_owner,
            SalesOrder.total_amount, SalesOrder.created_at,
        ).where(
            SalesOrder.status != "cancelled",
            business_year(SalesOrder.created_at) == year,
            business_month(SalesOrder.created_at) == month,
        )
        if scope_ids is not None:
            stmt = stmt.where(sales_owner.in_(scope_ids or [0]))
        for record_id, order_no, owner, amount, at in (await session.execute(stmt)).all():
            items.append(_row("order", record_id, order_no, owner, float(amount or 0), at))

    elif metric == "shipped":
        stmt = (
            select(
                OrderShipmentBatch.id,
                SalesOrder.order_no,
                OrderShipmentBatch.batch_no,
                sales_owner,
                func.coalesce(
                    func.sum(OrderShipmentBatchItem.shipped_qty * SalesOrderItem.unit_price), 0
                ),
                OrderShipmentBatch.actual_ship_date,
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
                func.extract("month", OrderShipmentBatch.actual_ship_date) == month,
                SalesOrder.status != "cancelled",
            )
            .group_by(
                OrderShipmentBatch.id,
                SalesOrder.order_no,
                OrderShipmentBatch.batch_no,
                sales_owner,
                OrderShipmentBatch.actual_ship_date,
            )
        )
        if scope_ids is not None:
            stmt = stmt.where(sales_owner.in_(scope_ids or [0]))
        for batch_id, order_no, batch_no, owner, amount, ship_date in (
            await session.execute(stmt)
        ).all():
            items.append(
                _row("shipment_batch", batch_id, f"{order_no} 第{batch_no}批", owner,
                     float(amount or 0), ship_date)
            )

    elif metric == "received":
        # 归月依据 = **财务确认时间**，与汇总同一个表达式（返工单第 2 条）。
        # 明细里显示的时间也换成确认时间：这一行是"按什么归月就显示什么"，
        # 否则明细显示到账日、汇总按确认日算，用户会以为有一笔错月了。
        stmt = (
            select(
                PaymentRecord.id, SalesOrder.order_no, sales_owner,
                PaymentRecord.received_amount, PaymentRecord.confirmed_at,
            )
            .join(SalesOrder, SalesOrder.id == PaymentRecord.order_id)
            .where(
                PaymentRecord.status == "confirmed",
                # 与汇总严格一致：缺确认时间的既不算进汇总，也不列进明细
                PaymentRecord.confirmed_at.is_not(None),
                SalesOrder.status != "cancelled",
                business_year(PaymentRecord.confirmed_at) == year,
                business_month(PaymentRecord.confirmed_at) == month,
            )
        )
        if scope_ids is not None:
            stmt = stmt.where(sales_owner.in_(scope_ids or [0]))
        for pay_id, order_no, owner, amount, at in (await session.execute(stmt)).all():
            items.append(
                _row("payment", pay_id, order_no, owner, float(amount or 0), at)
            )

    elif metric == "new_customer":
        # **首次有效成交**口径（考核口径）——与汇总、年度统计、冻结快照同源。
        #
        # 这里刻意改成调用汇总用的那一份取数（`target_bases.new_customer_rows`），
        # 而不是自己再写一段 SQL：返工单第 2 条的根因就是"汇总一段、明细一段"，
        # 两段迟早会漂。共用一份之后，"汇总几个、点开就是几个"是结构保证，
        # 不靠人记得同步改两处。
        month_key = f"{year}-{month:02d}"
        scope_set = None if scope_ids is None else set(scope_ids)
        for period, owner_id, cid, name, deal_at in await target_bases.new_customer_rows(
            session, year
        ):
            if period != month_key:
                continue
            if scope_set is not None and (owner_id is None or owner_id not in scope_set):
                continue
            # 显示**首成交时间**：显示建档日会让用户困惑（3 月建档案、9 月才成首单）
            items.append(_row("customer", cid, name, owner_id, 0.0, deal_at))

    else:  # repeat_net
        veterans, _first_deal, _first_deal_detail, _meta = await target_bases.basis_for(
            session, year
        )
        stmt = select(
            SalesOrder.id, SalesOrder.order_no, sales_owner,
            SalesOrder.total_amount, SalesOrder.created_at,
        ).where(
            SalesOrder.status != "cancelled",
            SalesOrder.customer_id.in_(veterans or [0]),
            business_year(SalesOrder.created_at) == year,
            business_month(SalesOrder.created_at) == month,
        )
        if scope_ids is not None:
            stmt = stmt.where(sales_owner.in_(scope_ids or [0]))
        for record_id, order_no, owner, amount, at in (await session.execute(stmt)).all():
            items.append(_row("order", record_id, order_no, owner, float(amount or 0), at))

    total = sum(item["amount"] for item in items)
    items.sort(key=lambda x: (x["date"] or "", -x["amount"]))
    cap = None if unlimited else DRILLDOWN_LIMIT
    return {
        "period": period,
        "metric": metric,
        "metric_label": DRILLDOWN_METRICS[metric],
        "scope": scope_label,
        "scope_user_ids": scope_ids,
        "count": len(items),
        "total": round(total, 2),
        "items": items if cap is None else items[:cap],
        "truncated": cap is not None and len(items) > cap,
        # 这一份是**存档**（结账那一刻抄的，不会因为后来退货变小）
        # 还是**实时算**的。两者对不上时，先看这个标志再怀疑数据。
        "source": "snapshot" if snapshot_frozen else "live",
        "actual_frozen": snapshot_frozen,
        # 口径元数据随明细一起给（§4.3：实际值 + 来源 + 口径版本 + 计算时间）
        "metric_basis_version": METRIC_BASIS_VERSION,
        "sources": METRIC_SOURCES.get(
            {"new_customer": "new_customer_actual", "repeat_net": "repeat_customer_actual"}.get(
                metric, f"{metric}_actual"
            ),
            METRIC_SOURCES.get("assess_actual"),
        ),
        "computed_at": datetime.now(UTC).isoformat(),
    }
