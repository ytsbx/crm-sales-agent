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
（文档 :61）。
"""

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.data_scope import scoped_owner_ids
from app.core.deps import CurrentUser
from app.modules.customer.model import Customer
from app.modules.order.model import OrderShipmentBatch, SalesOrder
from app.modules.payment.model import PaymentRecord

#: 口径说明与数据来源——文档要求随实际值一起保存/返回，不能只存在于开发脑子里
SOURCE_NOTE = "CRM 业务单据实时聚合（不读聚水潭、不做二次录入）"
BASIS_NOTE = {
    "signed": "签单口径：订单创建月，非取消订单的订单金额",
    "shipped": "发货口径：该订单首批实际发货日所在月，按订单整单金额归月",
    "received": "回款口径：财务确认回款日，只计已确认的回款",
    "repeat": "老客口径：年初固定客户集合（1 月 1 日前已有非取消订单）在本期的订单净额；本期新客不进老客池",
    "new_by_created": "新客口径一：客户档案在本月新建",
    "new_by_first_deal": "新客口径二：该客户首笔非取消订单落在本月",
}


def _bucket(out: dict[str, float], month, amount) -> None:
    if month is None:
        return
    key = f"{int(month):02d}"
    out[key] = out.get(key, 0.0) + float(amount or 0)


async def annual_bases(session: AsyncSession, user: CurrentUser, year: int) -> dict:
    """按年返回六个口径的月度序列（键是 `01`~`12`）。"""
    owner_ids = await scoped_owner_ids(session, user)
    scope = (lambda stmt, col: stmt) if owner_ids is None else (
        lambda stmt, col: stmt.where(col.in_(owner_ids or [0]))
    )
    sales_owner = func.coalesce(SalesOrder.sales_owner_id, SalesOrder.owner_id)
    year_start = date(year, 1, 1)
    next_year = date(year + 1, 1, 1)

    # ---- 签单：订单创建月 ----
    signed: dict[str, float] = {}
    rows = await session.execute(
        scope(
            select(
                func.extract("month", SalesOrder.created_at),
                func.coalesce(func.sum(SalesOrder.total_amount), 0),
            )
            .where(
                SalesOrder.status != "cancelled",
                func.extract("year", SalesOrder.created_at) == year,
            )
            .group_by(func.extract("month", SalesOrder.created_at)),
            sales_owner,
        )
    )
    for month, amount in rows.all():
        _bucket(signed, month, amount)

    # ---- 发货：首批实际发货日所在月（整单归月） ----
    shipped: dict[str, float] = {}
    first_ship = (
        select(
            OrderShipmentBatch.order_id.label("order_id"),
            func.min(OrderShipmentBatch.actual_ship_date).label("first_ship"),
        )
        .where(
            OrderShipmentBatch.status == "shipped",
            OrderShipmentBatch.actual_ship_date.is_not(None),
        )
        .group_by(OrderShipmentBatch.order_id)
        .subquery()
    )
    rows = await session.execute(
        scope(
            select(
                func.extract("month", first_ship.c.first_ship),
                func.coalesce(func.sum(SalesOrder.total_amount), 0),
            )
            .select_from(SalesOrder)
            .join(first_ship, first_ship.c.order_id == SalesOrder.id)
            .where(
                SalesOrder.status != "cancelled",
                func.extract("year", first_ship.c.first_ship) == year,
            )
            .group_by(func.extract("month", first_ship.c.first_ship)),
            sales_owner,
        )
    )
    for month, amount in rows.all():
        _bucket(shipped, month, amount)

    # ---- 回款：财务确认日 ----
    received: dict[str, float] = {}
    rows = await session.execute(
        scope(
            select(
                func.extract("month", PaymentRecord.received_date),
                func.coalesce(func.sum(PaymentRecord.received_amount), 0),
            )
            .select_from(PaymentRecord)
            .join(SalesOrder, SalesOrder.id == PaymentRecord.order_id)
            .where(
                PaymentRecord.status == "confirmed",
                PaymentRecord.received_date >= year_start,
                PaymentRecord.received_date < next_year,
            )
            .group_by(func.extract("month", PaymentRecord.received_date)),
            sales_owner,
        )
    )
    for month, amount in rows.all():
        _bucket(received, month, amount)

    # ---- 老客净额：期初固定客户集合 ----
    # 期初 = 该年 1 月 1 日之前已有非取消订单的客户；这些客户在本期的订单净额
    veteran_ids = select(SalesOrder.customer_id).where(
        SalesOrder.status != "cancelled", SalesOrder.created_at < year_start
    )
    repeat_net: dict[str, float] = {}
    rows = await session.execute(
        scope(
            select(
                func.extract("month", SalesOrder.created_at),
                func.coalesce(func.sum(SalesOrder.total_amount), 0),
            ).where(
                SalesOrder.status != "cancelled",
                SalesOrder.customer_id.in_(veteran_ids),
                func.extract("year", SalesOrder.created_at) == year,
            ).group_by(func.extract("month", SalesOrder.created_at)),
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
                func.extract("month", Customer.created_at), func.count()
            )
            .where(
                Customer.deleted_at.is_(None),
                func.extract("year", Customer.created_at) == year,
            )
            .group_by(func.extract("month", Customer.created_at)),
            Customer.owner_id,
        )
    )
    for month, count in rows.all():
        _bucket(new_by_created, month, count)

    first_deal = (
        select(
            SalesOrder.customer_id.label("customer_id"),
            func.min(SalesOrder.created_at).label("first_at"),
        )
        .where(SalesOrder.status != "cancelled")
        .group_by(SalesOrder.customer_id)
        .subquery()
    )
    new_by_first_deal: dict[str, float] = {}
    rows = await session.execute(
        scope(
            select(
                func.extract("month", first_deal.c.first_at), func.count()
            )
            .select_from(first_deal)
            .join(Customer, Customer.id == first_deal.c.customer_id)
            .where(
                Customer.deleted_at.is_(None),
                func.extract("year", first_deal.c.first_at) == year,
            )
            .group_by(func.extract("month", first_deal.c.first_at)),
            Customer.owner_id,
        )
    )
    for month, count in rows.all():
        _bucket(new_by_first_deal, month, count)

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
        "source_note": SOURCE_NOTE,
    }
