"""交期变更：受影响面预览 → 责任人确认 → 保留前后版本（方案 :105）。

原文：「客户改交期、样品未通过或生产延期时，展示受影响节点及批次，
责任人确认调整并保留修改前后版本。」

三条纪律：
1. **未确认不动数据**：`preview` 只算不改，确认那一刻才真正落库。
   否则"确认"就只是装饰，跟单人还是事后才发现计划被系统改了。
2. **已发生的事实不动**：已登记实际日期的节点、已发货/已取消的批次都不重排
   ——历史事实不能因为改交期被抹掉，这也是"保留前后版本"的另一半含义。
3. **前后版本存快照**：`affected` 在确认时不回写。再改一次生成新单，旧单永远
   留着它当时的对比；这比"只记最后一次"更能回答"客户问我们什么时候答应的"。
"""

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.core.errors import AppError, ErrorCode
from app.modules.order import milestones as milestones_svc
from app.modules.order.model import (
    OrderMilestone,
    OrderScheduleChange,
    OrderShipmentBatch,
    SalesOrder,
)


async def _nodes(session: AsyncSession, order_id: int) -> list[OrderMilestone]:
    return list(
        (
            await session.execute(
                select(OrderMilestone)
                .where(OrderMilestone.order_id == order_id)
                .order_by(OrderMilestone.id)
            )
        ).scalars().all()
    )


async def _batches(session: AsyncSession, order_id: int) -> list[OrderShipmentBatch]:
    return list(
        (
            await session.execute(
                select(OrderShipmentBatch)
                .where(
                    OrderShipmentBatch.order_id == order_id,
                    OrderShipmentBatch.status.not_in(("shipped", "cancelled")),
                )
                .order_by(OrderShipmentBatch.batch_no)
            )
        ).scalars().all()
    )


async def preview(
    session: AsyncSession, order: SalesOrder, new_delivery_date: date
) -> dict:
    """算一遍"改了会动到谁"：节点按交期倒推重算，批次按天数整体平移。"""
    old_due = order.delivery_date
    delta = (new_delivery_date - old_due).days if old_due else None
    plan = milestones_svc.default_plan(new_delivery_date)

    affected_nodes = []
    for row in await _nodes(session, order.id):
        if row.actual_date is not None:
            continue  # 已发生的事实不动
        # 口径与批次**保持一致：按天数平移**，而不是拿新交期重新倒推。
        # 倒推会把跟单员手工推后的日子一把拉回默认值——那些手工调整往往是因为
        # 产前样延期、客户改期，抹掉它不但排错日期，还会凭空造出逾期提醒。
        # 只有"原本没有计划日"（或没有旧交期做基准）时才回退到倒推。
        if row.planned_date is not None and delta is not None:
            new_planned = row.planned_date + timedelta(days=delta)
        else:
            new_planned = plan.get(row.node)
        if new_planned == row.planned_date:
            continue
        affected_nodes.append(
            {
                "node": row.node,
                "label": milestones_svc.node_label(row.node),
                "before": row.planned_date.isoformat() if row.planned_date else None,
                "after": new_planned.isoformat() if new_planned else None,
            }
        )

    affected_batches = []
    for batch in await _batches(session, order.id):
        if batch.planned_date is None or delta is None:
            # 没有旧交期做基准、或本就没排计划日：不猜，交给责任人手工确认
            new_planned = batch.planned_date
        else:
            new_planned = batch.planned_date + timedelta(days=delta)
        if new_planned == batch.planned_date:
            continue
        affected_batches.append(
            {
                "batch_id": batch.id,
                "batch_no": batch.batch_no,
                "before": batch.planned_date.isoformat() if batch.planned_date else None,
                "after": new_planned.isoformat() if new_planned else None,
            }
        )

    return {
        "order_id": order.id,
        "order_no": order.order_no,
        "old_delivery_date": old_due.isoformat() if old_due else None,
        "new_delivery_date": new_delivery_date.isoformat(),
        "shift_days": delta,
        "nodes": affected_nodes,
        "batches": affected_batches,
    }


async def create_change(
    session: AsyncSession,
    order: SalesOrder,
    *,
    user: CurrentUser,
    new_delivery_date: date,
    reason: str | None,
) -> OrderScheduleChange:
    affected = await preview(session, order, new_delivery_date)
    if not affected["nodes"] and not affected["batches"]:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            "新交期不影响任何节点或批次（可能交期没变，或受影响的都已发生）",
            422,
        )
    # 同一订单**只能有一张待确认的变更单**：两张并存时各自确认会互相覆盖计划日，
    # 而 old_delivery_date 的档案也跟着失真（后者以"前者已改过的交期"为基准）。
    existing_pending = (
        await session.execute(
            select(OrderScheduleChange.id).where(
                OrderScheduleChange.order_id == order.id,
                OrderScheduleChange.status == "pending",
            )
        )
    ).first()
    if existing_pending is not None:
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            "该订单已有一张待确认的交期变更单，请先确认或作废它再发起新的",
            409,
        )
    row = OrderScheduleChange(
        order_id=order.id,
        old_delivery_date=order.delivery_date,
        new_delivery_date=new_delivery_date,
        reason=reason,
        affected=affected,
        status="pending",
        owner_id=order.owner_id,
        created_by=user.id,
        created_at=datetime.now(UTC),
    )
    session.add(row)
    await session.flush()
    return row


async def confirm_change(
    session: AsyncSession,
    order: SalesOrder,
    change: OrderScheduleChange,
    *,
    user: CurrentUser,
    remark: str | None,
) -> OrderScheduleChange:
    """责任人确认后才真正改动：订单交期、节点计划日、批次计划日。"""
    # 行锁：两个人同时点"确认"时，第二个必须等第一个提交完再读状态，
    # 否则两边都读到 pending、都往下走，计划日被写两遍。
    locked = (
        await session.execute(
            select(OrderScheduleChange)
            .where(OrderScheduleChange.id == change.id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if locked is None:
        raise AppError(ErrorCode.NOT_FOUND, "交期变更单不存在", 404)
    change = locked
    if change.status != "pending":
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"该交期变更单已是「{change.status}」，不能重复确认",
            # 第三个参数是 **HTTP 状态码**，不是业务码。这里原先误传了业务码 40002，
            # uvicorn 拿它去查状态行直接 KeyError、连接被掐断——接口 500 都没有，
            # 只有一条 RemoteDisconnected，非常难查。
            422,
        )
    affected = change.affected or {}
    # 以确认这一刻重算一次为准：预览之后可能又有人登记了实际日期/加了批次，
    # 用旧快照去写会覆盖掉那期间的事实
    fresh = await preview(session, order, change.new_delivery_date)
    order.delivery_date = change.new_delivery_date

    by_node = {n["node"]: n["after"] for n in fresh["nodes"]}
    for row in await _nodes(session, order.id):
        if row.node in by_node and by_node[row.node]:
            row.planned_date = date.fromisoformat(by_node[row.node])

    by_batch = {b["batch_id"]: b["after"] for b in fresh["batches"]}
    for batch in await _batches(session, order.id):
        after = by_batch.get(batch.id)
        if after:
            batch.planned_date = date.fromisoformat(after)

    change.status = "confirmed"
    change.confirmed_by = user.id
    change.confirmed_at = datetime.now(UTC)
    change.confirm_remark = remark
    # 快照保持创建时那一版（预览所见），另存"确认时实际生效"的版本，
    # 两者不一致时说明中间有人动过东西，事后能查出来
    change.affected = {**affected, "applied": fresh}
    await session.flush()
    return change


async def cancel_change(
    session: AsyncSession,
    order: SalesOrder,
    change: OrderScheduleChange,
    *,
    user: CurrentUser,
    reason: str | None,
) -> OrderScheduleChange:
    """作废一张待确认的变更单。

    **为什么必须有这条出口**：库上有"一个订单只允许一张 pending"的部分唯一索引。
    没有作废路径的话，一张发起后没人确认的变更单会**永久堵死**该订单之后所有的
    交期变更——除了直接改库没有出路；而错误文案还写着"请先确认或作废它"，
    指的是一条不存在的路。

    作废只改状态与留痕，**不动任何计划日期**——它本来就没生效过。
    """
    locked = (
        await session.execute(
            select(OrderScheduleChange)
            .where(OrderScheduleChange.id == change.id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if locked is None:
        raise AppError(ErrorCode.NOT_FOUND, "交期变更单不存在", 404)
    change = locked
    if change.status != "pending":
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"该交期变更单已是「{change.status}」，不能作废",
            422,
        )
    change.status = "cancelled"
    change.cancel_reason = reason
    change.cancelled_by = user.id
    change.cancelled_at = datetime.now(UTC)
    await session.flush()
    return change


def serialize_change(row: OrderScheduleChange, *, names: dict[int, str] | None = None) -> dict:
    names = names or {}
    return {
        "id": row.id,
        "order_id": row.order_id,
        "old_delivery_date": row.old_delivery_date,
        "new_delivery_date": row.new_delivery_date,
        "reason": row.reason,
        "status": row.status,
        "status_label": {
            "pending": "待确认",
            "confirmed": "已确认",
            "cancelled": "已作废",
        }.get(row.status, row.status),
        "cancel_reason": row.cancel_reason,
        "cancelled_by": row.cancelled_by,
        "cancelled_at": row.cancelled_at.isoformat() if row.cancelled_at else None,
        "owner_id": row.owner_id,
        "owner_name": names.get(row.owner_id) if row.owner_id else None,
        "confirmed_by": row.confirmed_by,
        "confirmed_by_name": names.get(row.confirmed_by) if row.confirmed_by else None,
        "confirmed_at": row.confirmed_at.isoformat() if row.confirmed_at else None,
        "confirm_remark": row.confirm_remark,
        "affected": row.affected,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
