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
        new_planned = plan.get(row.node)
        if new_planned == row.planned_date:
            continue
        affected_nodes.append(
            {
                "node": row.node,
                "label": milestones_svc.NODE_LABELS.get(row.node, row.node),
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


def serialize_change(row: OrderScheduleChange, *, names: dict[int, str] | None = None) -> dict:
    names = names or {}
    return {
        "id": row.id,
        "order_id": row.order_id,
        "old_delivery_date": row.old_delivery_date,
        "new_delivery_date": row.new_delivery_date,
        "reason": row.reason,
        "status": row.status,
        "status_label": {"pending": "待确认", "confirmed": "已确认"}.get(
            row.status, row.status
        ),
        "owner_id": row.owner_id,
        "owner_name": names.get(row.owner_id) if row.owner_id else None,
        "confirmed_by": row.confirmed_by,
        "confirmed_by_name": names.get(row.confirmed_by) if row.confirmed_by else None,
        "confirmed_at": row.confirmed_at.isoformat() if row.confirmed_at else None,
        "confirm_remark": row.confirm_remark,
        "affected": row.affected,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
