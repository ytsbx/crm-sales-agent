"""跟单里程碑（领导模块⑤）：从客户最终交期倒推关键节点，用跟单检验生产端。

节点清单领导已点名：签订合同 → 付定金 → 产前样发出 → 产前样确认 →
首批发货（分批在备注说明）→ 收款。

- MILESTONE_NODES 仅提供建议参数；每张订单的提前天数经责任人确认后保存。
- 初始化只建立空节点；计划由交期与计划预览、确认流程写入。实际日期由跟单登记；
- 状态是算出来的：有实际日期=完成；过了计划日期还没完成=逾期；否则待办；
- 逾期推送走 notify_overdue_milestones（每日调度），每节点只推一次。
"""

import logging
from datetime import UTC, date, datetime, time
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.errors import AppError, ErrorCode
# 业务时区的"今天"（第九批 §9.10）：节点逾期判断原来用 `today_business()`，
# 跟着宿主机时区走 —— 服务器换成 UTC 之后，北京时间凌晨的判定会差一天。
from app.core.timebase import today_business
from app.modules.order.model import OrderMilestone, OrderShipmentBatch, SalesOrder
from app.modules.order.dates import shift_date
from app.modules.payment.model import PaymentRecord, ReceivablePlan

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
    "skipped": "不适用",
}


def default_plan(delivery_date: date | None) -> dict[str, date | None]:
    """按交期倒推各节点计划日期；交期未知时计划日期留空，等交期明确后重排。"""
    if delivery_date is None:
        return {key: None for key, _label, _offset in MILESTONE_NODES}
    return {
        key: shift_date(delivery_date, -offset)
        for key, _label, offset in MILESTONE_NODES
    }


def node_status(planned: date | None, actual: date | None, today: date) -> str:
    if actual is not None:
        return STATUS_DONE
    if planned is not None and planned < today:
        return STATUS_OVERDUE
    return STATUS_PENDING


def row_status(row: OrderMilestone, today: date) -> str:
    if row.actual_date is None and row.skipped_at is not None:
        return "skipped"
    return node_status(row.planned_date, row.actual_date, today)


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
    """订单没有里程碑时按六节点初始化为空计划（幂等 + 并发安全）。

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

    plan = default_plan(None)  # 初始化不产生未经确认的计划
    try:
        async with session.begin_nested():
            for key, _label, _offset in missing:
                session.add(
                    OrderMilestone(
                        order_id=order_id,
                        node=key,
                        planned_date=plan.get(key),
                        created_by=created_by,
                        created_at=_datetime_of(today_business()),
                    )
                )
            await session.flush()
    except IntegrityError:
        # 并发初始化：对方已建好，回滚到保存点后读现成的
        logger.info("订单 %s 里程碑被并发初始化，复用已存在行", order_id)

    return await _load_rows(session, order_id)


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
        created_at=_datetime_of(today_business()),
    )
    session.add(node)
    await session.flush()
    return node


#: 这些固定节点的实际日**由真实事实派生**，不接受人工直填（issue #11）。
#:
#: 审查实测的反例：普通销售把「首批发货」与「收款」的实际日期直接填上，
#: 节点立刻显示完成，而同一张订单**实发数量仍是 0、已确认到账也是 0** ——
#: 主管看到全绿，货和钱其实都没发生。
#:
#: 与动态批次节点同一条道理（那边早就堵了）：一件事只能有一个来源。
DERIVED_NODES: dict[str, str] = {
    "first_shipment": "取已发货批次里最早的实际发货日（在「发货」里登记）",
    "deposit": "取已确认收款的日期（在「应收/回款」里确认收款）",
    "payment": "取已确认收款的日期（在「应收/回款」里确认收款）",
}

#: 这几个节点没有对应的系统事实可派生，只能人工登记 —— 但登记后必须**标明是人工声明**，
#: 不能和"已验证事实"混成同一类（审查原话：确需人工补录的节点标识"人工声明/待核实"）。
MANUAL_NODES: tuple[str, ...] = (
    "contract",
    "pre_sample_sent",
    "pre_sample_confirmed",
)

#: 人工登记时写进 `evidence` 的标记。前端据此显示「人工声明」而不是「已验证」。
MANUAL_EVIDENCE_TAG = "[人工声明·待核实]"


async def sync_payment_milestones(
    session: AsyncSession, order_id: int, *, operator_id: int | None = None
) -> None:
    """把「付定金」「收款」两个节点**按当前财务事实完整重建**（issue #11、第三批审查 P1）。

    口径（刻意保守，宁可不标完成也不假完成）：
    - `deposit`：有任意一笔**已确认**收款 → 取最早那笔的到账日；
    - `payment`：该订单下所有应收计划都收齐（且至少有一笔已确认）→ 取最晚那笔到账日。

    2026-10-12（第三批审查 P1）补的两条，都是"只补不撤"造成的假完成：

    1. **不再只往里写**。从前只在条件成立时写 `actual_date`，条件不再成立时
       一个字都不动 —— 应收 100 收齐后节点是 `done`，随后把应收改成 200，
       应收状态正确变 `partial`，节点却永远是 `done`。现在条件不成立就**撤销**
       旧的完成日期（派生节点的事实只能来自事实本身），并留一条纠正痕迹，让
       "这行为什么被撤"事后查得到。
    2. **节点不存在时先补建**。从前 `if not rows: return` —— 先确认回款、后首次
       打开跟单页时，这一步发生在节点还没建的时候，等于什么都没同步，
       两个节点永远是空的。现在先走 `ensure_initialized` 再算。

    定金日期同样**重算**：补录一笔更早的到账记录后，最早那笔才是定金事实。
    """
    # 行不在就补建（幂等 + 并发安全由 `ensure_initialized` 内部保证）
    rows = await ensure_initialized(session, order_id, None, created_by=operator_id)
    by_node = {
        row.node: row for row in rows if row.node in ("deposit", "payment")
    }
    if not by_node:
        return

    confirmed = (
        await session.execute(
            select(
                func.min(PaymentRecord.received_date),
                func.max(PaymentRecord.received_date),
                func.count(PaymentRecord.id),
            ).where(
                PaymentRecord.order_id == order_id,
                PaymentRecord.status == "confirmed",
                PaymentRecord.received_date.is_not(None),
            )
        )
    ).one()
    earliest, latest, count = confirmed

    deposit = by_node.get("deposit")
    if deposit is not None:
        # 有已确认收款才有定金事实；一笔都没有（或都被驳回/删除）就得撤掉
        await _apply_derived_fact(
            session,
            deposit,
            earliest if count else None,
            note="按已确认收款的最早到账日重算",
        )

    # 收齐才标「收款」：**逐期**核对（都在库里算，不用前端传）。
    #
    # ## 为什么不能只比总额（第十批审查 ②，2026-10-13）
    #
    # 原来写的是 `received >= planned`（应收总额 vs 已确认总额）。实测：
    # 两期各应收 100 元，把真实回款 200 元**全部挂到第一期**并确认 ——
    # 总额相等，于是「收款」节点判定完成，可第二期一分钱没收到。
    # 节点是"这批货的钱收齐了"的业务事实，一期多收**不能**替另一期结清。
    #
    # 口径（与报告一致）：逐期算"这一期收够了没有"，
    # 多收的钱走明确的分配/核销流程转到别期，**不在这里自动抵消**。
    # ⚠️ "已收"读的是**分配额**（`payment_allocations`），不是
    # `payment_records.receivable_plan_id`（第十批主人 2026-10-13 拍板）。
    #
    # 那个列是"登记时选的那一期"（用户意图）；分配表才是"这笔钱实际覆盖了哪些期"。
    # 两者只在**不超收**时一致 —— 一笔 200 挂在一期应收 100 上时，按前者会把 200
    # 全算给一期（二期永远显示未收），按后者才是 100 归一期、100 抵二期。
    # ⚠️ 逐期"已收"**必须调 `payment.confirmed_amount`**，不要在这里再写一份
    # 聚合 SQL（我第一版就是这么写的，结果漏了那边的"无分配行回款"兜底，
    # 同一个数在财务页对、在跟单节点上不对 —— 两份实现必然漂移）。
    # 同步只读财务事实，绝不初始化或重写分配；历史兼容由金额来源处理。
    plan_rows = (
        await session.execute(
            select(ReceivablePlan.id, ReceivablePlan.amount)
            .where(
                ReceivablePlan.order_id == order_id,
                ReceivablePlan.status != "cancelled",
            )
            .order_by(ReceivablePlan.id.asc())
        )
    ).all()
    from app.modules.payment import service as payment_service

    per_plan = [
        (plan_id, amount, await payment_service.confirmed_amount(session, plan_id))
        for plan_id, amount in plan_rows
    ]
    planned = sum((Decimal(str(row[1] or 0)) for row in per_plan), Decimal(0))
    received = sum((Decimal(str(row[2] or 0)) for row in per_plan), Decimal(0))
    unsettled = [
        (row[0], Decimal(str(row[1] or 0)), Decimal(str(row[2] or 0)))
        for row in per_plan
        if Decimal(str(row[2] or 0)) < Decimal(str(row[1] or 0))
    ]
    payment = by_node.get("payment")
    if payment is not None:
        settled = bool(count) and planned > 0 and not unsettled
        cash = (await payment_service.allocation_totals(session, [order_id])).get(order_id, {})
        actual_cash = cash.get("received_amount", Decimal(0))
        unallocated_cash = cash.get("unallocated_amount", Decimal(0))
        note = f"应收合计 {planned}、实际确认到账 {actual_cash}、已分配 {received}、待分配 {unallocated_cash}"
        if settled:
            note += "（每一期都已结清）"
        elif unsettled:
            short = "、".join(
                f"第{index + 1}期差 {plan_amount - paid}"
                for index, (_plan_id, plan_amount, paid) in enumerate(unsettled)
            )
            note += f"（还有 {len(unsettled)} 期未结清：{short}）"
            # 主动提示"可能是回款挂错了期"（第十批主人选的方案1）：
            # 有人把整笔钱登记到某一期时，别的期就会显示未收 —— 这是最常见的成因，
            # 说清它，用户才知道该去「调整回款分配」，而不是以为客户没付钱。
            if actual_cash >= planned or unallocated_cash > 0:
                note += "；节点未结清不等于客户未付款，请核对后调整回款分配"
        else:
            note += "（还没有已确认收款，撤销原来的完成标识）"
        await _apply_derived_fact(session, payment, latest if settled else None, note=note)
    await session.flush()


async def _apply_derived_fact(
    session: AsyncSession,
    row: OrderMilestone,
    new_date: date | None,
    *,
    note: str,
) -> None:
    """把派生节点写成**当前事实值**；新值为空时撤销旧的完成标识并留痕。

    为什么撤销也要留痕：节点从"已完成"退回"未完成"是一次状态纠正，
    事后必须能回答"谁/什么时候/为什么把它撤了"，而不是让人看到一行
    悄悄变空的记录。留痕写进审计（与人工登记走同一张表、不同的 action）。
    """
    if new_date == row.actual_date:
        if new_date is not None:
            # 事实仍在：把"跳过"清掉（跳过不等于实际完成）
            _clear_skip(row)
        return
    if new_date is None:
        revoked = row.actual_date
        if revoked is None:
            return
        row.actual_date = None
        await write_audit(
            session,
            operator_id=None,
            action="milestone_revoked",
            business_type="order_milestone",
            business_id=row.id,
            before={"node": row.node, "actual_date": revoked.isoformat()},
            after={"node": row.node, "actual_date": None, "reason": note},
            source="SYSTEM",
        )
        return
    row.actual_date = new_date
    _clear_skip(row)


def _clear_skip(row: OrderMilestone) -> None:
    """事实发生了就把"跳过"清掉：跳过不等于实际完成，但现在它真的完成了。"""
    row.skipped_at = None
    row.skipped_by = None
    row.skip_reason = None


async def mark_batch_shipped(    session: AsyncSession, order_id: int, batch_no: int, actual_date: date | None
) -> None:
    """批次实发时登记实际日（第一批返修 §3.5）。

    第 2 批起登记它自己的动态节点；**「首批发货」这个固定节点改由"首个有效实际
    发货批次"定义**——即该订单已发货批次里 `actual_ship_date` 的最小值。

    原来首批**只靠人工登记**，于是与分析侧 `min(actual_ship_date)` 两套口径：
    同一张单"首批发货是哪天"，跟单页写一个数、交付准时率算另一个数，
    对账时谁也说不清哪个对。改成一件事只有一个来源（谁更早谁说了算）。
    """
    if actual_date is None:
        return
    if batch_no >= 2:
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
            row.skipped_at = None
            row.skipped_by = None
            row.skip_reason = None
            await session.flush()
    # 首批节点：取已发货批次里最早的实际发货日。**任何一批实发都要重算**——
    # 第 2 批有可能填了比第 1 批更早的日期（补登记），那它才是真正的首批。
    earliest = (
        await session.execute(
            select(func.min(OrderShipmentBatch.actual_ship_date)).where(
                OrderShipmentBatch.order_id == order_id,
                OrderShipmentBatch.status == "shipped",
                OrderShipmentBatch.actual_ship_date.is_not(None),
            )
        )
    ).scalar_one()
    if earliest is None:
        return
    # ⚠️ **行不在就补建**（2026-10-14 审查修）。
    #
    # `order_milestones` 的固定六节点只由三处创建：`GET /orders/{id}/milestones`、
    # 交期变更确认、`sync_payment_milestones`。而前端跟单页签是**懒加载**
    # （`OrderDetailPage.tsx` 里 `enabled: activeKey === "milestones"`）——
    # 用户在「发货批次」页签建批次、登记实发时，节点行**还不存在**。
    # 于是下面 `if first is not None` 静默落空：跟单页永远显示"未发货"，
    # 而库里明明有 `status="shipped"` 的批次；`first_shipment` 又是交付准时率
    # 与交期分析的同一来源，会连带算错。
    #
    # 同一课在回款那边已经补过（`sync_payment_milestones` 的注释：
    # "从前 `if not rows: return` —— 等于什么都没同步。现在先走
    # `ensure_initialized` 再算"）—— 发货这边当时漏了。
    await ensure_initialized(session, order_id, None, created_by=None)
    first = (
        await session.execute(
            select(OrderMilestone).where(
                OrderMilestone.order_id == order_id,
                OrderMilestone.node == "first_shipment",
            )
        )
    ).scalar_one_or_none()
    if first is not None:
        first.actual_date = earliest
        # 实发就把"跳过"清掉：跳过不等于实际完成，但现在它真的完成了
        first.skipped_at = None
        first.skipped_by = None
        first.skip_reason = None
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
        # 已经登记过实际发货的节点不能顺手删掉——那是**已发生的业务事实**，
        # 删了以后"这一批到底发没发"就查不出来了。要取消请先更正这条事实。
        if row.actual_date is not None:
            raise AppError(
                ErrorCode.STATUS_NOT_ALLOWED,
                "该批次对应的跟单节点已登记实际发货日期，请先更正该事实再取消批次",
                422,
            )
        await session.delete(row)
        await session.flush()


async def notify_overdue_milestones(session: AsyncSession) -> int:
    """每日扫描：逾期未完成的节点推给订单负责人 + 业务主管，每节点只推一次。

    "每节点只推一次"用 overdue_notified_at 记凭证——推过就不再重复推，
    避免每天一封骚扰；节点完成或计划日期被调整后不会重推旧状态。
    """
    from app.modules.notification import service as notification_service

    today = today_business()
    rows = list(
        (
            await session.execute(
                select(OrderMilestone)
                .join(SalesOrder, SalesOrder.id == OrderMilestone.order_id)
                .where(
                    OrderMilestone.actual_date.is_(None),
                    OrderMilestone.skipped_at.is_(None),
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

    today = today_business()
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
