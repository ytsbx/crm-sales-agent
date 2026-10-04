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

#: 动态批次节点前缀。口径（2026-10-04）：跟单节点**按批次动态生成**——
#: 首批对应固定的「首批发货」节点；第 2 批起每批自动多一个节点，
#: 这样"分批导致的延期"才统计得出来（文档 :103 点名的后续分批发货）。
BATCH_NODE_PREFIX = "shipment_batch_"


def batch_node_key(batch_no: int) -> str:
    return f"{BATCH_NODE_PREFIX}{batch_no}"


def node_label(node: str) -> str:
    """节点展示名：固定节点查表，动态批次节点按 key 现算。"""
    label = NODE_LABELS.get(node)
    if label:
        return label
    if node.startswith(BATCH_NODE_PREFIX):
        suffix = node[len(BATCH_NODE_PREFIX):]
        if suffix.isdigit():
            return f"第 {suffix} 批发货"
    return node

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
    have = {row.node for row in rows}
    # 只看"固定六节点齐没齐"：按批次动态生成的节点（第 2 批起）可能先于本次
    # 初始化就存在，不能用"有没有行"当成"六节点齐了"，否则固定节点会被漏建。
    missing = [node for node in MILESTONE_NODES if node[0] not in have]
    if not missing:
        return rows

    plan = default_plan(delivery_date)
    try:
        async with session.begin_nested():
            for key, _label, _offset in missing:
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
        # 并发初始化：对方已建好，回滚到保存点后读现成的
        logger.info("订单 %s 里程碑被并发初始化，复用已存在行", order_id)

    return await _load_rows(session, order_id)


async def replan(
    session: AsyncSession,
    order_id: int,
    delivery_date: date | None,
) -> int:
    """补齐**还没有计划日**的节点；已排定 / 已登记的都不动。返回补齐条数。

    这里曾经是"按交期重新倒推每个节点"，于是点一次就把跟单员手工推后的日子
    一把拉回默认值（产前样延期、客户改期导致的手工调整全被抹掉，还会凭空造出
    逾期提醒）。交期**真正变化**时要按天数平移，那条路走交期变更单
    （`schedule.preview` 的平移口径）；这个独立入口只负责"从没排过的节点补上默认
    计划日"，绝不覆盖人工已经排好的日期。
    """
    rows = await ensure_initialized(session, order_id, delivery_date)
    plan = default_plan(delivery_date)
    changed = 0
    for row in rows:
        if row.actual_date is not None:
            continue
        if row.planned_date is not None:
            continue  # 已排定（可能是人工调整过的）：不动
        new_planned = plan.get(row.node)
        if new_planned is not None:
            row.planned_date = new_planned
            changed += 1
    await session.flush()
    return changed


async def ensure_batch_node(
    session: AsyncSession,
    order_id: int,
    batch_no: int,
    planned_date: date | None,
    created_by: int | None = None,
) -> OrderMilestone | None:
    """给"第 N（>=2）批"生成一个独立跟单节点（首批对应固定的「首批发货」）。

    幂等：(order_id, node) 有唯一约束，已存在就复用。计划日取批次的计划日，
    之后批次计划日随交期变更平移时，节点也一起平移（schedule.preview 对全部节点
    一视同仁），所以两边不会脱节。
    """
    if batch_no < 2:
        return None
    key = batch_node_key(batch_no)
    existing = (
        await session.execute(
            select(OrderMilestone).where(
                OrderMilestone.order_id == order_id, OrderMilestone.node == key
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if planned_date is not None and existing.actual_date is None:
            existing.planned_date = planned_date
        return existing
    node = OrderMilestone(
        order_id=order_id,
        node=key,
        planned_date=planned_date,
        created_by=created_by,
        created_at=_datetime_of(date.today()),
    )
    session.add(node)
    await session.flush()
    return node


async def mark_batch_shipped(
    session: AsyncSession, order_id: int, batch_no: int, actual_date: date | None
) -> None:
    """批次实发时把对应动态节点的实际日登记上（首批仍是人工登记，不动）。"""
    if batch_no < 2 or actual_date is None:
        return
    row = (
        await session.execute(
            select(OrderMilestone).where(
                OrderMilestone.order_id == order_id,
                OrderMilestone.node == batch_node_key(batch_no),
            )
        )
    ).scalar_one_or_none()
    if row is not None:
        row.actual_date = actual_date
        await session.flush()


async def drop_batch_node(session: AsyncSession, order_id: int, batch_no: int) -> None:
    """批次取消时撤掉它的动态节点（批次都不发了，节点留着只会造逾期提醒）。"""
    if batch_no < 2:
        return
    row = (
        await session.execute(
            select(OrderMilestone).where(
                OrderMilestone.order_id == order_id,
                OrderMilestone.node == batch_node_key(batch_no),
            )
        )
    ).scalar_one_or_none()
    if row is not None:
        await session.delete(row)
        await session.flush()


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
        label = node_label(row.node)
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


async def notify_overdue_batches(session: AsyncSession) -> int:
    """每日扫描：逾期的**发货批次**推给订单负责人 + 业务主管，每批只推一次。

    为什么是单独一段而不是复用节点提醒：批次不是跟单节点（15 号清单 §2-1 的取舍
    ——领导点名六节点、分批写备注），而节点提醒只扫 order_milestones，扫不到批次。
    业务要的是"第 2 批该发没发有人管"，那就单独给批次一条提醒；
    代价是系统里多一条平行的逾期逻辑，这一点在代码注释里写明，别当成遗漏。

    `overdue_notified_at` 与节点同一套去重：推过就不再推，批次后来发了也不重推。
    """
    from app.modules.notification import service as notification_service
    from app.modules.order.model import OrderShipmentBatch

    today = date.today()
    rows = list(
        (
            await session.execute(
                select(OrderShipmentBatch)
                .join(SalesOrder, SalesOrder.id == OrderShipmentBatch.order_id)
                .where(
                    OrderShipmentBatch.status == "planned",
                    OrderShipmentBatch.planned_date.is_not(None),
                    OrderShipmentBatch.planned_date < today,
                    OrderShipmentBatch.overdue_notified_at.is_(None),
                    SalesOrder.status != "cancelled",
                )
                .order_by(OrderShipmentBatch.id.asc())
            )
        ).scalars().all()
    )
    if not rows:
        return 0

    orders = {
        o.id: o
        for o in (
            await session.execute(
                select(SalesOrder).where(
                    SalesOrder.id.in_({r.order_id for r in rows})
                )
            )
        ).scalars().all()
    }
    from app.modules.user.model import User

    owner_ids = {o.owner_id for o in orders.values() if o.owner_id}
    owner_departments: dict[int, int | None] = {}
    if owner_ids:
        for uid, dept in (
            await session.execute(
                select(User.id, User.department_id).where(User.id.in_(owner_ids))
            )
        ).all():
            owner_departments[uid] = dept

    notified = 0
    for row in rows:
        order = orders.get(row.order_id)
        if order is None or row.planned_date is None:
            continue
        days = (today - row.planned_date).days
        title = f"发货逾期：{order.order_no} 第 {row.batch_no} 批"
        content = (
            f"{order.order_no} 的第 {row.batch_no} 批计划 {row.planned_date} 发货，"
            f"已逾期 {days} 天仍未发货，请跟进"
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
            logger.warning("批次逾期提醒推送失败（batch=%s）：%s", row.id, exc)
            continue
        row.overdue_notified_at = datetime.now(UTC)
        notified += 1
    await session.flush()
    return notified
