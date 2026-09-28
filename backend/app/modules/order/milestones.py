"""跟单里程碑（领导模块⑤）：从客户最终交期倒推关键节点，用跟单检验生产端。

节点清单领导已点名：签订合同 → 付定金 → 产前样发出 → 产前样确认 →
首批发货（分批在备注说明）→ 收款。

- 各节点相对交期的天数（负数 = 交期后）集中在 MILESTONE_NODES 一个常量里，
  业务想调只改这里；
- 计划日期从交期**倒推**得出，实际日期由跟单人工登记；
- 状态是算出来的：有实际日期=完成；过了计划日期还没完成=逾期；否则待办；
- 逾期推送走 notify_overdue_milestones（每日调度），每节点只推一次。
"""

import logging
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.order.model import OrderMilestone, SalesOrder

logger = logging.getLogger("crm.milestones")

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


def _datetime_of(d: date) -> datetime:
    return datetime.combine(d, time.min)


async def _load_rows(session: AsyncSession, order_id: int) -> list[OrderMilestone]:
    return list(
        (
            await session.execute(
                select(OrderMilestone)
                .where(OrderMilestone.order_id == order_id)
                .order_by(OrderMilestone.id.asc())
            )
        ).scalars().all()
    )


async def ensure_initialized(
    session: AsyncSession,
    order_id: int,
    delivery_date: date | None,
    created_by: int | None = None,
) -> list[OrderMilestone]:
    """订单没有里程碑时按六节点初始化（幂等 + 并发安全）。

    并发防护：表上有 (order_id, node) 唯一约束。两个请求同时首次打开时，
    后到者的 flush 会撞唯一索引——在 SAVEPOINT 里插，撞了就回滚到保存点，
    读对方已建的行，外层事务不受污染。
    """
    rows = await _load_rows(session, order_id)
    if rows:
        return rows

    plan = default_plan(delivery_date)
    try:
        async with session.begin_nested():
            for key, _label, _offset in MILESTONE_NODES:
                session.add(
                    OrderMilestone(
                        order_id=order_id,
                        node=key,
                        planned_date=plan.get(key),
                        created_by=created_by,
                        created_at=_datetime_of(date.today()),
                    )
                )
            await session.flush()
    except IntegrityError:
        # 并发初始化：对方已建好六行，回滚到保存点后读现成的
        logger.info("订单 %s 里程碑被并发初始化，复用已存在行", order_id)

    return await _load_rows(session, order_id)


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


async def notify_overdue_milestones(session: AsyncSession) -> int:
    """每日扫描：逾期未完成的节点推给订单负责人 + 业务主管，每节点只推一次。

    "每节点只推一次"用 overdue_notified_at 记凭证——推过就不再重复推，
    避免每天一封骚扰；节点完成或计划日期被调整后不会重推旧状态。
    """
    from app.modules.notification import service as notification_service

    today = date.today()
    rows = list(
        (
            await session.execute(
                select(OrderMilestone)
                .join(SalesOrder, SalesOrder.id == OrderMilestone.order_id)
                .where(
                    OrderMilestone.actual_date.is_(None),
                    OrderMilestone.planned_date.is_not(None),
                    OrderMilestone.planned_date < today,
                    OrderMilestone.overdue_notified_at.is_(None),
                    SalesOrder.status != "cancelled",
                )
                .order_by(OrderMilestone.id.asc())
            )
        ).scalars().all()
    )
    if not rows:
        return 0

    order_ids = {r.order_id for r in rows}
    orders = {
        o.id: o
        for o in (
            await session.execute(select(SalesOrder).where(SalesOrder.id.in_(order_ids)))
        ).scalars().all()
    }
    # 主管按"订单负责人所在部门"定位：跨部门不互扰
    from app.modules.user.model import User

    owner_ids = {o.owner_id for o in orders.values() if o.owner_id}
    owner_departments: dict[int, int | None] = {}
    if owner_ids:
        for uid, dept in (
            await session.execute(select(User.id, User.department_id).where(User.id.in_(owner_ids)))
        ).all():
            owner_departments[uid] = dept

    notified = 0
    for row in rows:
        order = orders.get(row.order_id)
        if order is None or row.planned_date is None:
            continue
        days = (today - row.planned_date).days
        label = NODE_LABELS.get(row.node, row.node)
        title = f"跟单逾期：{order.order_no} {label}"
        content = (
            f"{order.order_no} 的「{label}」计划 {row.planned_date}，"
            f"已逾期 {days} 天未完成，请跟进"
        )
        try:
            if order.owner_id:
                await notification_service.notify(
                    session,
                    user_id=order.owner_id,
                    type_="followup",
                    title=title,
                    content=content,
                    business_type="order",
                    business_id=order.id,
                )
            await notification_service.notify_roles(
                session,
                role_codes=["sales_manager"],
                type_="followup",
                title=title,
                content=content,
                business_type="order",
                business_id=order.id,
                exclude_user_id=order.owner_id,
                department_id=owner_departments.get(order.owner_id) if order.owner_id else None,
            )
        except Exception as exc:  # 通知失败不标记，明天会重试
            logger.warning("逾期提醒推送失败（milestone=%s）：%s", row.id, exc)
            continue
        row.overdue_notified_at = datetime.now(UTC)
        notified += 1
    await session.flush()
    return notified
