"""跟单里程碑（领导模块⑤）：从客户最终交期倒推关键节点，用跟单检验生产端。

节点清单领导已点名：签订合同 → 付定金 → 产前样发出 → 产前样确认 →
首批发货（分批在备注说明）→ 收款。

- `DEFAULT_OFFSETS`/`MILESTONE_NODES` 是各节点相对交期的天数（负数 = 交期后）
  ——默认口径集中在这一个常量里，业务想调只改这里；
- 计划日期从交期**倒推**得出，实际日期由跟单人工登记；
- 状态是算出来的：有实际日期=完成；过了计划日期还没完成=逾期；否则待办。
"""

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.order.model import OrderMilestone

#: (节点键, 名称, 相对交期天数)。负数 = 交期之后
MILESTONE_NODES: list[tuple[str, str, int]] = [
    ("contract", "签订合同", 30),
    ("deposit", "付定金", 28),
    ("pre_sample_sent", "产前样发出", 20),
    ("pre_sample_confirmed", "产前样确认", 15),
    ("first_shipment", "首批发货", 0),
    ("payment", "收款", -15),
]

NODE_LABELS: dict[str, str] = {key: label for key, label, _ in MILESTONE_NODES}

STATUS_DONE = "done"
STATUS_OVERDUE = "overdue"
STATUS_PENDING = "pending"

STATUS_LABELS = {
    STATUS_DONE: "已完成",
    STATUS_OVERDUE: "已逾期",
    STATUS_PENDING: "待办",
}


def default_plan(delivery_date: date | None) -> dict[str, date | None]:
    """按交期倒推各节点计划日期；交期未知时计划日期留空，等交期明确后重排。"""
    if delivery_date is None:
        return {key: None for key, _label, _offset in MILESTONE_NODES}
    return {
        key: delivery_date - timedelta(days=offset)
        for key, _label, offset in MILESTONE_NODES
    }


def node_status(planned: date | None, actual: date | None, today: date) -> str:
    if actual is not None:
        return STATUS_DONE
    if planned is not None and planned < today:
        return STATUS_OVERDUE
    return STATUS_PENDING


async def ensure_initialized(session: AsyncSession, order_id: int, delivery_date: date | None) -> list[OrderMilestone]:
    """订单没有里程碑时按六节点初始化（幂等：已有则原样返回）。

    计划日期按当前交期倒推；交期为空则计划留空，交期补上后可用 replan 重排。
    """
    rows = list(
        (
            await session.execute(
                select(OrderMilestone)
                .where(OrderMilestone.order_id == order_id)
                .order_by(OrderMilestone.id.asc())
            )
        ).scalars().all()
    )
    if rows:
        return rows

    plan = default_plan(delivery_date)
    today = date.today()
    for key, _label, _offset in MILESTONE_NODES:
        session.add(
            OrderMilestone(
                order_id=order_id,
                node=key,
                planned_date=plan.get(key),
                created_at=datetime_combine(today),
            )
        )
    await session.flush()
    return list(
        (
            await session.execute(
                select(OrderMilestone)
                .where(OrderMilestone.order_id == order_id)
                .order_by(OrderMilestone.id.asc())
            )
        ).scalars().all()
    )


def datetime_combine(d: date):
    from datetime import datetime as _dt

    return _dt(d.year, d.month, d.day)


async def replan(
    session: AsyncSession,
    order_id: int,
    delivery_date: date | None,
) -> int:
    """按（新）交期重排计划日期；**已登记实际日期的节点不动**。返回重排条数。"""
    rows = await ensure_initialized(session, order_id, delivery_date)
    plan = default_plan(delivery_date)
    changed = 0
    for row in rows:
        if row.actual_date is not None:
            continue
        new_planned = plan.get(row.node)
        if new_planned != row.planned_date:
            row.planned_date = new_planned
            changed += 1
    await session.flush()
    return changed
