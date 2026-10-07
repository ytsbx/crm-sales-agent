"""目标的三种销售额口径 + 老客净额 + 两种新客口径（文档 §六 :121 / 场景17）。

文档要求"**系统分别保存目标值、计算口径、实际值、差额与数据来源**"，并且
"新客户要区分新建档与首次有效成交；老客增长要固定比较客户集合、周期和净额；
销售额分别显示签单、发货、回款"。

三个口径各自的"什么时候算进哪个月"是这张表的全部内容：

| 口径 | 归月依据 | 说明 |
|---|---|---|
| 签单 | 订单创建月 | 与业绩榜（签单归属）一致 |
| 发货 | **首批实际发货日**所在月 | 批次表存的是数量不是金额，所以按"订单有没有在当月发出第一批货"整单归月——避免把一单拆成几份再摊价格 |
| 回款 | 确认回款日 | 只算财务已确认的 |

**老客净额的口径**（业务已拍板）：客户集合在**期初固定**——把"该年 1 月 1 日之前
已有非取消订单的客户"锁成老客池，本期这些客户的订单净额就是老客增长；
本期新成交的客户**不进老客池**（否则新客一成交就自动变老客，数字虚高）。
池子按年度固定，月度横向可比。

**两种新客口径**：`created` = 客户档案在该月新建；`first_deal` = 该客户首笔非取消
订单落在该月。两者常不一样——建档后三个月才成交的客户，前者算 1 月、后者算 4 月。

金额一律按**签单归属**（`sales_owner_id`）归属：钱算签单人，与业绩榜同一口径
（`CRM完整实现方案.md:61`「交接后保留历史业绩归属」）。
⚠️ 应收/账龄页是**另一个口径**（责任口径＝当前负责人），两页的数本来就不该相等；
但同一页内的计划/实绩/差额必须同源（§4.1.3 的原缺陷就是混用）。
"""

from datetime import UTC, datetime

from sqlalchemy import distinct, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.data_scope import scoped_owner_ids
# 业务时间基准（第九批 §9.10）：归年 / 归月一律走这里，不再依赖宿主机时区
# 或数据库会话时区 —— 换了环境同一个报表会变，那是审计意义上的缺陷。
from app.core.timebase import business_month, business_year, year_bounds
from app.core.deps import CurrentUser
from app.modules.analytics.model import BasisSnapshot
from app.modules.customer.model import Customer
from app.modules.order.model import (
    OrderShipmentBatch,
    OrderShipmentBatchItem,
    SalesOrder,
    SalesOrderItem,
)
from app.modules.payment.model import PaymentRecord

#: 口径说明与数据来源——文档要求随实际值一起保存/返回，不能只存在于开发脑子里
SOURCE_NOTE = "CRM 业务单据实时聚合（不读聚水潭、不做二次录入）"
#: 归属政策（`CRM完整实现方案.md:61`：交接后**保留历史业绩归属**）。
#: 业绩口径 = 签单归属（`sales_owner_id`）：钱算签单人。
#: 应收/账龄页是责任口径（当前负责人）——两页的数不该相等，但**同一页内**
#: 计划/实绩/差额必须同源，否则会出现"原负责人负未回款"（§4.1.3）。
#:
#: ⚠️ 下面这些字符串会**原样显示在页面上**（`AnalyticsPage` 直接渲染 `basis_note`），
#: 所以不许写 markdown 星号——前端是纯文本，`**实际发货批次**` 会把星号一起露出来。
#: 要强调就用「」或书名号。加新口径说明时照这个来。
ATTRIBUTION_NOTE = (
    "归属口径：本页为「业绩口径」，按订单「签单归属」（`sales_orders.sales_owner_id`）"
    "——文档 :61「交接后保留历史业绩归属」，钱算签单人。"
    "应收/账龄页用的是责任口径（当前负责人），两页的数不该相等"
)
BASIS_NOTE = {
    "signed": "签单口径：订单创建月，非取消订单的订单金额",
    "shipped": (
        "发货口径：按「实际发货批次」分摊——每批金额 = Σ(该批实发数量 × 订单行单价)，"
        "落在该批 `actual_ship_date` 所在月。不再按首批把整单金额归到一个月（§4.1.4）"
    ),
    "received": (
        "回款口径：按「财务确认时间」（`payment_records.confirmed_at`）归月，"
        "只计已确认的回款；已确认但没记确认时间的不计入"
        "（请到回款管理补填——不拿收款日顶替，否则同一列里混进两个口径）"
    ),
    "repeat": "老客口径：年初固定客户集合（1 月 1 日前已有非取消订单）在本期的订单净额；本期新客不进老客池",
    "new_by_created": "新客口径一（过程指标）：客户档案在本月新建",
    "new_by_first_deal": "新客口径二（考核口径）：该客户首笔非取消订单落在本月",
}


def _bucket(out: dict[str, float], month, amount) -> None:
    if month is None:
        return
    key = f"{int(month):02d}"
    out[key] = out.get(key, 0.0) + float(amount or 0)


#: 口径版本：快照与结果都带它，方便回答"这一版是怎么算的"。
#: `bases.3`（2026-10-06，R07）：首次成交**连日期与来源订单一起冻**，
#: 不再拿当前订单实时重算日期。bases.2 及更早冻的快照没有那一段，
#: 读出来要如实说"日期未知"（见 `_detail_of_snapshot`）。
BASIS_VERSION = "2026-10-06.bases.3"


async def _build_basis(
    session: AsyncSession, year: int
) -> tuple[list[int], dict[int, str], dict[int, dict]]:
    """算出某年的两份基准：年初前已成交的客户、该年首次成交的客户→月份（+日期）。

    这两份就是"老客池"与"首次成交"口径的**全部依据**。它们一旦落库就不再从
    可变的订单状态重算，历史指标才不会漂移（§4.1.5）。

    第三个返回值是 R07 补的：**首次成交的具体日期与来源订单**
    （`{客户id: {"at": ISO, "order_id": 订单id}}`）。此前只冻了"哪个月"，
    明细上的日期靠事后实时重算 —— 原首单被取消后重算会跳到下一张单，
    于是同一行里出现"归属一月、首次成交日期显示三月"的自相矛盾。
    """
    year_start, next_start = year_bounds(year)
    veterans = (
        await session.execute(
            select(distinct(SalesOrder.customer_id)).where(
                SalesOrder.status != "cancelled",
                SalesOrder.customer_id.is_not(None),
                SalesOrder.created_at < year_start,
            )
        )
    ).scalars().all()
    # 每客户最早的那张非取消订单。**连订单 id 一起取** —— 以后要能回答
    # "这个客户的首单到底是哪一张"。`DISTINCT ON (customer_id)` 配
    # `ORDER BY customer_id, created_at, id` ＝ 每组取最早的一条。
    first_deal_rows = (
        await session.execute(
            select(SalesOrder.customer_id, SalesOrder.created_at, SalesOrder.id)
            .where(SalesOrder.status != "cancelled", SalesOrder.customer_id.is_not(None))
            .order_by(SalesOrder.customer_id, SalesOrder.created_at, SalesOrder.id)
            .distinct(SalesOrder.customer_id)
        )
    ).all()
    first_deal_month: dict[int, str] = {}
    first_deal_detail: dict[int, dict] = {}
    for customer_id, at, order_id in first_deal_rows:
        if at is None or not (year_start <= at < next_start):
            continue
        cid = int(customer_id)
        first_deal_month[cid] = f"{at.year}-{at.month:02d}"
        first_deal_detail[cid] = {"at": at.isoformat(), "order_id": int(order_id)}
    return [int(x) for x in veterans], first_deal_month, first_deal_detail


def _detail_of_snapshot(snapshot: BasisSnapshot) -> dict[int, dict]:
    """从快照里取"首次成交的具体日期 + 来源订单"。

    **旧版冻的快照没有这一段**（`first_deal_detail` 是 NULL）—— 那就返回空 dict，
    调用方据此显示"日期未知"。**不要**借机去实时重算：重算出来的日期与已经冻住的
    归属月份可能对不上，那正是 R07 要修的病。
    """
    raw = getattr(snapshot, "first_deal_detail", None) or {}
    out: dict[int, dict] = {}
    for key, value in raw.items():
        if not isinstance(value, dict) or not value.get("at"):
            continue
        try:
            out[int(key)] = value
        except (TypeError, ValueError):
            continue
    return out


def _deal_at_of(entry: dict | None) -> datetime | None:
    """把快照里存的那条 `{"at": ISO 时间…}` 还原成 `datetime`；没有就返回 None。"""
    if not entry:
        return None
    raw = entry.get("at")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None


async def new_customer_rows(
    session: AsyncSession, year: int
) -> list[tuple[str, int | None, int, str, datetime | None]]:
    """本年「首次有效成交」的新客：`(期间, 负责人, 客户id, 客户名, 首成交时间)`。

    **汇总与明细共用这一份，不许各写一段** —— 返工单第 2 条的根因就是
    目标页的汇总按「建档月」数、下钻明细按「首次成交」列：两个口径，
    于是页面上写"新客 3 个"、点开明细只看到 1 个。
    集合只算一次、两边消费同一份数据，"对不上"从源头上就不可能发生。

    首成交时间是为了明细里能显示"这笔是哪天成的" —— 用客户建档案那天会让人困惑
    （3 月建的档、9 月才成第一单，却在 9 月的明细里看到 3 月的日期）。

    但它取的是**与月份一起冻结的那一份**（R07，2026-10-06 修）：此前这里每次现查
    "该客户当前最早的非取消订单"，而归属月份读的却是快照 —— 两边**来源不同**，
    原首单被取消后就会打架（月份还冻在一月，日期却跳到三月）。
    现在日期与来源订单跟月份一起冻；**老快照没冻日期就如实返回 None**
    （界面显示"未知"），不拿实时重算的数字顶上。
    """
    from app.modules.customer.model import Customer

    _veterans, first_deal_month, first_deal_detail, _meta = await basis_for(session, year)
    if not first_deal_month:
        return []
    ids = [int(cid) for cid in first_deal_month]
    # 与明细同一套筛法：未删除的客户档案。**只按 id 取，不按建档时间取** ——
    # 那样又把口径拉回"建档月"了。
    rows = (
        await session.execute(
            select(Customer.id, Customer.name, Customer.owner_id).where(
                Customer.deleted_at.is_(None), Customer.id.in_(ids)
            )
        )
    ).all()
    return [
        (
            first_deal_month[int(cid)],
            owner_id,
            int(cid),
            name,
            # 老快照（没冻日期）这里是 None —— 界面如实显示"未知"。
            # 绝不回头查当前订单补一个日期：那正是 R07 的自相矛盾来源。
            _deal_at_of(first_deal_detail.get(int(cid))),
        )
        for cid, name, owner_id in rows
    ]


async def _freeze_basis(
    year: int, *, replace: bool = False, operator_id: int | None = None
) -> BasisSnapshot:
    """把某年的基准算好并落库。

    为什么用**自己的会话**：这是 GET 里的惰性写入，而请求会话（`get_db`）不提交，
    写在请求会话里会随请求结束被回滚。并发两个请求同时冻结同一年时靠主键冲突兜底：
    谁先落谁赢，后到的重读。
    """
    from app.core.database import SessionLocal

    async with SessionLocal() as own:
        veteran_ids, first_deal_month, first_deal_detail = await _build_basis(own, year)
        month_payload = {str(k): v for k, v in first_deal_month.items()}
        # 日期与来源订单跟月份**在同一个事务里一起落**（R07）：分开写就有机会
        # 一半成功一半失败，留下"有月份没日期"的残档。
        detail_payload = {str(k): v for k, v in first_deal_detail.items()}
        row = await own.get(BasisSnapshot, year)
        if row is None:
            row = BasisSnapshot(
                year=year,
                veteran_customer_ids=veteran_ids,
                first_deal_month=month_payload,
                first_deal_detail=detail_payload,
                metric_basis_version=BASIS_VERSION,
                computed_at=datetime.now(UTC),
                computed_by=operator_id,
            )
            own.add(row)
        elif not replace:
            # 别人已经冻好了：直接用，别覆盖（冻结的意义就是不被后来的重算改写）
            return row
        else:
            row.veteran_customer_ids = veteran_ids
            row.first_deal_month = month_payload
            row.first_deal_detail = detail_payload
            row.metric_basis_version = BASIS_VERSION
            row.computed_at = datetime.now(UTC)
            row.computed_by = operator_id
        try:
            await own.commit()
        except IntegrityError:
            await own.rollback()
            existing = await own.get(BasisSnapshot, year)
            if existing is None:
                raise
            return existing
        await own.refresh(row)
        return row


async def basis_for(
    session: AsyncSession, year: int, *, refreeze: bool = False, operator_id: int | None = None
) -> tuple[list[int], dict[int, str], dict[int, dict], dict]:
    """取一年的基准，并说明它是否已冻结（§4.1.5）。

    返回 `(老客池, 客户→归属月, 客户→首次成交日期与来源订单, 元信息)`。
    第三项可能不完整：**旧版冻的快照没有日期**（R07 之前只冻了月份），
    此时它是空 dict，调用方要如实显示"未知"，不要回头实时重算。

    边界刻意保守：**当年不冻结**——数据还在产生，冻了会冻在半路上；
    **过去年份**第一次被读取时冻结一次，之后一直用快照。管理员要改历史口径时
    走 `refreeze=True`（带审计），而不是让每次读取都悄悄重算。
    """
    current_year = datetime.now(UTC).year
    if year >= current_year:
        veteran_ids, first_deal_month, first_deal_detail = await _build_basis(session, year)
        return veteran_ids, first_deal_month, first_deal_detail, {
            "frozen": False,
            "frozen_at": None,
            "version": BASIS_VERSION,
            "note": "当年数据仍在产生，不冻结；跨年后首次读取时自动冻结",
        }
    snapshot = None if refreeze else await session.get(BasisSnapshot, year)
    if snapshot is None:
        snapshot = await _freeze_basis(year, replace=refreeze, operator_id=operator_id)
    return (
        [int(x) for x in (snapshot.veteran_customer_ids or [])],
        {int(k): v for k, v in (snapshot.first_deal_month or {}).items()},
        _detail_of_snapshot(snapshot),
        {
            "frozen": True,
            "frozen_at": snapshot.computed_at.isoformat() if snapshot.computed_at else None,
            "version": snapshot.metric_basis_version,
            "note": "历史年份口径已冻结：之后的订单变更不会改写这一年已出的数",
        },
    )


async def annual_bases(session: AsyncSession, user: CurrentUser, year: int) -> dict:
    """按年返回六个口径的月度序列（键是 `01`~`12`）。"""
    owner_ids = await scoped_owner_ids(session, user)
    scope = (lambda stmt, col: stmt) if owner_ids is None else (
        lambda stmt, col: stmt.where(col.in_(owner_ids or [0]))
    )
    sales_owner = func.coalesce(SalesOrder.sales_owner_id, SalesOrder.owner_id)
    # 基准：老客池 + 首次成交。**过去年份读快照、当年实时算**（§4.1.5）
    veteran_ids, first_deal_month, _first_deal_detail, basis_meta = await basis_for(
        session, year
    )

    # ---- 签单：订单创建月 ----
    signed: dict[str, float] = {}
    rows = await session.execute(
        scope(
            select(
                business_month(SalesOrder.created_at),
                func.coalesce(func.sum(SalesOrder.total_amount), 0),
            )
            .where(
                SalesOrder.status != "cancelled",
                business_year(SalesOrder.created_at) == year,
            )
            .group_by(business_month(SalesOrder.created_at)),
            sales_owner,
        )
    )
    for month, amount in rows.all():
        _bucket(signed, month, amount)

    # ---- 发货：按**实际发货批次 × 行实发数量**分摊到各批次所在月（§4.1.4）----
    # 原来按"订单首批实际发货日"把**整单金额**归到一个月：10 月发 10 件、11 月发 90 件，
    # 整单都算进 10 月——分批发货直接错期。现在每批各算各的：
    # 批次金额 = Σ(该批 `shipped_qty` × 订单行 `unit_price`)，落在该批发货日所在月。
    shipped: dict[str, float] = {}
    batch_value = (
        select(
            OrderShipmentBatch.order_id.label("order_id"),
            OrderShipmentBatch.actual_ship_date.label("ship_date"),
            func.coalesce(
                func.sum(OrderShipmentBatchItem.shipped_qty * SalesOrderItem.unit_price), 0
            ).label("amount"),
        )
        .select_from(OrderShipmentBatch)
        .join(
            OrderShipmentBatchItem,
            OrderShipmentBatchItem.batch_id == OrderShipmentBatch.id,
        )
        .join(SalesOrderItem, SalesOrderItem.id == OrderShipmentBatchItem.order_item_id)
        .where(
            OrderShipmentBatch.status == "shipped",
            OrderShipmentBatch.actual_ship_date.is_not(None),
        )
        .group_by(OrderShipmentBatch.order_id, OrderShipmentBatch.actual_ship_date)
        .subquery()
    )
    rows = await session.execute(
        scope(
            select(
                func.extract("month", batch_value.c.ship_date),
                func.coalesce(func.sum(batch_value.c.amount), 0),
            )
            .select_from(batch_value)
            .join(SalesOrder, SalesOrder.id == batch_value.c.order_id)
            .where(
                SalesOrder.status != "cancelled",
                func.extract("year", batch_value.c.ship_date) == year,
            )
            .group_by(func.extract("month", batch_value.c.ship_date)),
            sales_owner,
        )
    )
    for month, amount in rows.all():
        _bucket(shipped, month, amount)

    # ---- 回款：**财务确认时间**归月（返工单第 2 条，与 targets.py 同一口径）----
    received: dict[str, float] = {}
    rows = await session.execute(
        scope(
            select(
                business_month(PaymentRecord.confirmed_at),
                func.coalesce(func.sum(PaymentRecord.received_amount), 0),
            )
            .select_from(PaymentRecord)
            .join(SalesOrder, SalesOrder.id == PaymentRecord.order_id)
            .where(
                PaymentRecord.status == "confirmed",
                # 缺确认时间的归不了月，不进这一列（与目标页一致：先不算、由那边提示补录）
                PaymentRecord.confirmed_at.is_not(None),
                # 年份边界也用 extract，与下面 group_by 的月份**同一套时区口径**：
                # 一个用带时区的时间戳比较、一个按会话时区分月，跨年边界会差 8 小时
                business_year(PaymentRecord.confirmed_at) == year,
            )
            .group_by(business_month(PaymentRecord.confirmed_at)),
            sales_owner,
        )
    )
    for month, amount in rows.all():
        _bucket(received, month, amount)

    # ---- 老客净额：期初固定客户集合 ----
    # 期初 = 该年 1 月 1 日之前已有非取消订单的客户。这份池子来自**基准**
    # （过去年份已冻结，§4.1.5），不再每次从订单状态现算——否则事后取消一张往年
    # 订单，客户就从整年老客池里消失，去年的数今年再看就变了。
    repeat_net: dict[str, float] = {}
    rows = await session.execute(
        scope(
            select(
                business_month(SalesOrder.created_at),
                func.coalesce(func.sum(SalesOrder.total_amount), 0),
            ).where(
                SalesOrder.status != "cancelled",
                SalesOrder.customer_id.in_(veteran_ids or [0]),
                business_year(SalesOrder.created_at) == year,
            ).group_by(business_month(SalesOrder.created_at)),
            sales_owner,
        )
    )
    for month, amount in rows.all():
        _bucket(repeat_net, month, amount)

    # ---- 两种新客口径 ----
    new_by_created: dict[str, float] = {}
    rows = await session.execute(
        scope(
            select(
                business_month(Customer.created_at), func.count()
            )
            .where(
                Customer.deleted_at.is_(None),
                business_year(Customer.created_at) == year,
            )
            .group_by(business_month(Customer.created_at)),
            Customer.owner_id,
        )
    )
    for month, count in rows.all():
        _bucket(new_by_created, month, count)

    # 首次成交用**基准里的那份**（§4.1.5）：过去年份已冻结，当年实时。
    # 不再从 orders 现算 `min(created_at)`——那样事后取消人家的首单，
    # 首次成交月会往后跳，历史指标被就地改写。
    new_by_first_deal: dict[str, float] = {}
    deals = {int(cid): month for cid, month in first_deal_month.items()}
    if deals:
        # 数据范围仍按客户**当前负责人**过滤（口径不变，只是基准不再现算）
        owners = dict(
            (
                await session.execute(
                    select(Customer.id, Customer.owner_id).where(
                        Customer.deleted_at.is_(None), Customer.id.in_(list(deals))
                    )
                )
            ).all()
        )
        for customer_id, month in deals.items():
            owner = owners.get(customer_id)
            if owner is None:
                continue
            if owner_ids is not None and owner not in owner_ids:
                continue
            _bucket(new_by_first_deal, int(month[5:]), 1)

    months = [f"{m:02d}" for m in range(1, 13)]
    return {
        "year": year,
        "months": months,
        "signed": [{"month": m, "value": round(signed.get(m, 0.0), 2)} for m in months],
        "shipped": [{"month": m, "value": round(shipped.get(m, 0.0), 2)} for m in months],
        "received": [{"month": m, "value": round(received.get(m, 0.0), 2)} for m in months],
        "repeat_net": [{"month": m, "value": round(repeat_net.get(m, 0.0), 2)} for m in months],
        "new_by_created": [{"month": m, "value": int(new_by_created.get(m, 0))} for m in months],
        "new_by_first_deal": [
            {"month": m, "value": int(new_by_first_deal.get(m, 0))} for m in months
        ],
        "basis_note": BASIS_NOTE,
        "attribution_note": ATTRIBUTION_NOTE,
        "source_note": SOURCE_NOTE,
        # §4.1.5：基准（老客池 / 首次成交）是否已冻结、什么时候冻的、按哪一版口径
        "basis_frozen": basis_meta["frozen"],
        "basis_frozen_at": basis_meta["frozen_at"],
        "basis_version": basis_meta["version"],
        "basis_note_freeze": basis_meta["note"],
    }


async def repeat_net_by_owner(
    session: AsyncSession, user: CurrentUser, year: int
) -> dict[int | None, dict[str, float]]:
    """老客净额，按「签单归属人」拆开的版本（键：负责人 id → 月份 → 金额）。

    口径与 `annual_bases` 里那一份**完全相同**（期初固定客户集合、按签单归属、
    金额取订单净额），只多拆了"人"这一维——目标页要按人显示复购目标的完成情况，
    而 `annual_bases` 只在调用者范围内按月汇总，拆不出人。

    **口径只此一处**：`targets.py` 直接用这个函数，不要自己再写一遍，
    否则两处迟早会漂移成两个数（这个项目已经吃过"各算各的"的亏）。
    """
    owner_ids = await scoped_owner_ids(session, user)
    sales_owner = func.coalesce(SalesOrder.sales_owner_id, SalesOrder.owner_id)

    # 期初老客池来自**同一份基准**（§4.1.5）：过去年份已冻结。两处各算一遍的话，
    # 冻结只管住一处、另一处照样漂移，等于没冻。
    veteran_ids, _first_deal_month, _first_deal_detail, _meta = await basis_for(session, year)
    stmt = (
        select(
            business_month(SalesOrder.created_at),
            sales_owner,
            func.coalesce(func.sum(SalesOrder.total_amount), 0),
        )
        .where(
            SalesOrder.status != "cancelled",
            SalesOrder.customer_id.in_(veteran_ids or [0]),
            business_year(SalesOrder.created_at) == year,
        )
        .group_by(business_month(SalesOrder.created_at), sales_owner)
    )
    if owner_ids is not None:
        stmt = stmt.where(sales_owner.in_(owner_ids or [0]))

    out: dict[int | None, dict[str, float]] = {}
    for month, owner_id, amount in (await session.execute(stmt)).all():
        if month is None:
            continue
        bucket = out.setdefault(owner_id, {})
        key = f"{int(month):02d}"
        bucket[key] = bucket.get(key, 0.0) + float(amount or 0)
    return out
