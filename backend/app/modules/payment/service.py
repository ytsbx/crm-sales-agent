"""应收与回款逻辑。"""

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.core.timebase import today_business
from app.modules.file.model import FileRecord
from app.modules.order.model import SalesOrder
from app.modules.payment.model import PAYMENT_STATUS_LABEL, PLAN_STATUS_LABEL, PaymentRecord, ReceivablePlan
from app.modules.user.model import User

ZERO = Decimal(0)

#: **不许被"按已确认回款重算"覆盖**的状态。
#:
#: 这里**只有 `cancelled`**（N02 的修复点）：它随订单取消而来，不参与催收，
#: 也不该被重算"复活"成待收/逾期。取消状态由订单状态驱动，不由回款金额驱动。
#:
#: ⚠️ `paid` **不在这里**（C4-01，2026-10-09 修）。原先它也在，前提是
#: "`paid` 无法合法退回"—— 那个前提**是错的**：它只算了"改回款"这条路径
#: （`PATCH /payments/{id}` 确实拒绝改已确认的回款），**漏了"改应收金额"**
#: 这条：`PATCH /receivables/{id}` 允许改 `amount`，把应收从 100 改成 200 之后
#: 已收仍是 100，节点就成了"**余额 100 却显示已结清**"——对账时说不清算没算完。
#:
#: 现在 `paid` 照常参与重算：金额被改大就如实退回 `partial`（状态说真话）。
#: 要"不许结清后改金额"是另一种口径（会挡掉修正录错金额的正当需求），未采用。
TERMINAL_PLAN_STATUSES = frozenset({"cancelled"})


def ensure_payment_pending(record: PaymentRecord) -> None:
    """Only pending receipts may be edited, confirmed, or rejected."""
    if record.status != "pending":
        label = PAYMENT_STATUS_LABEL.get(record.status, record.status)
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"只有待财务确认的回款可以操作（当前：{label}）",
        )


def _f(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


async def confirmed_amount(session: AsyncSession, plan_id: int) -> Decimal:
    total = (
        await session.execute(
            select(func.coalesce(func.sum(PaymentRecord.received_amount), 0)).where(
                PaymentRecord.receivable_plan_id == plan_id,
                PaymentRecord.status == "confirmed",
            )
        )
    ).scalar_one()
    return Decimal(total)


async def recalc_plan(session: AsyncSession, plan: ReceivablePlan) -> None:
    """按已确认的回款重算应收节点状态。

    规则：全额收齐 → 已回款；收了一部分 → 部分回款；一分没收到且过期 → 已逾期。

    **`cancelled` 不被重算覆盖**（N02，2026-10-09 修）：见 `TERMINAL_PLAN_STATUSES`。
    从前这里没有任何保护，而 `PATCH /receivables/{id}` 结尾**无条件**调用本函数 ——
    于是"只改一个备注"就会把随订单取消的节点按"没收到钱 + 已过期"算回 `overdue`，
    取消状态凭空消失。

    **`paid` 会参与重算**（C4-01，2026-10-09 修）：金额被改大、或回款被作废之后，
    它必须能如实退回 `partial` —— 否则会出现"余额大于零却显示已结清"。
    退回的**只有** `partial`（确实收过钱），不会退回 `overdue`：
    收过钱就不该被算成逾期，"已收齐的节点被算回待收"那条老毛病也不会回来。

    **调用方必须已经持有该节点的行锁**（`lock_plan`）：这里的汇总结果直接写回
    节点状态，不在锁内汇总就等于"读旧数、写覆盖"（两个 50 并发确认会留下
    partial）。函数体内不再加锁，免得同一事务里重复取锁掩盖掉调用方的漏锁。
    """
    if plan.status in TERMINAL_PLAN_STATUSES:
        return
    received = await confirmed_amount(session, plan.id)
    if received > 0 and received >= (plan.amount or ZERO):
        plan.status = "paid"
    elif received > 0:
        # 收过钱（哪怕只收了一部分）就不算逾期 —— 金额被改大后退回这里，
        # 而不是掉进下面的 overdue 分支。
        plan.status = "partial"
    else:
        today = today_business()
        plan.status = "overdue" if plan.due_date < today else "pending"


async def serialize_plan(session: AsyncSession, plan: ReceivablePlan) -> dict:
    received = await confirmed_amount(session, plan.id)
    order = await session.get(SalesOrder, plan.order_id)
    return {
        "id": plan.id,
        "order_id": plan.order_id,
        "order_no": order.order_no if order else None,
        "customer_id": order.customer_id if order else None,
        "plan_name": plan.plan_name,
        "due_date": plan.due_date,
        "amount": _f(plan.amount),
        "received_amount": _f(received),
        "remaining_amount": _f((plan.amount or ZERO) - received),
        "currency": plan.currency,
        "status": plan.status,
        "status_label": PLAN_STATUS_LABEL.get(plan.status, plan.status),
        "remark": plan.remark,
    }


async def serialize_payment(session: AsyncSession, record: PaymentRecord) -> dict:
    plan = (
        await session.get(ReceivablePlan, record.receivable_plan_id)
        if record.receivable_plan_id
        else None
    )
    order = await session.get(SalesOrder, record.order_id)
    voucher = (
        await session.get(FileRecord, record.voucher_file_id) if record.voucher_file_id else None
    )
    confirmer = await session.get(User, record.confirmed_by) if record.confirmed_by else None
    return {
        "id": record.id,
        "order_id": record.order_id,
        "order_no": order.order_no if order else None,
        "receivable_plan_id": record.receivable_plan_id,
        "plan_name": plan.plan_name if plan else None,
        "received_date": record.received_date,
        "received_amount": _f(record.received_amount),
        "currency": record.currency,
        "payment_method": record.payment_method,
        "voucher_note": record.voucher_note,
        "voucher_file_id": record.voucher_file_id,
        "voucher_file_name": voucher.file_name if voucher else None,
        "status": record.status,
        "status_label": PAYMENT_STATUS_LABEL.get(record.status, record.status),
        "confirmed_by": record.confirmed_by,
        "confirmed_by_name": confirmer.name if confirmer else None,
        "confirmed_at": record.confirmed_at,
        "created_at": record.created_at,
    }


async def get_plan_or_404(session: AsyncSession, plan_id: int) -> ReceivablePlan:
    plan = await session.get(ReceivablePlan, plan_id)
    if plan is None:
        raise AppError(ErrorCode.NOT_FOUND, "应收节点不存在", 404)
    return plan


# ---------------------------------------------------------------------------
# 锁（第七批 7.9）
#
# 统一锁序：**sales_orders → receivable_plans → payment_records**。
# 会同时碰多张表的写入路径一律按这个顺序取行锁，任何两笔事务都不会互相
# 持锁等待，也就不可能成环。谁要加新锁，先看这里再决定放在哪一段。
# ---------------------------------------------------------------------------
async def lock_order(session: AsyncSession, order_id: int) -> SalesOrder | None:
    """按统一锁序锁住订单行；不存在返回 None（由调用方决定报什么错）。

    为什么"生成应收计划"必须走它：生成是「先查这个订单有没有计划、没有再插」。
    两个并发请求在各自事务里都查到"还没有"，于是各插一套 —— 两套金额都合法，
    库里却凭空多出一整套应收期，事后只能人工比对删哪一套。锁住订单行后两个
    请求串行：后到的那个拿到锁时先到者已提交，`existing` 这次一定看得见。

    手工建节点（`_create_plan`）也走同一把锁：否则"生成"查无再插的窗口里还能
    挤进一个手工节点，两边的判断都不算错，结果却是混着的两套口径。
    """
    # ⚠️ `populate_existing=True` 不能省（C5-02，2026-10-10 修）。
    #
    # 不加它时，若这个订单**已经在本 session 的 identity map 里**（调用方先
    # `get_order_or_404` 查过一次就属于这种），`with_for_update()` 只锁行、
    # 拿回来仍是**缓存里的旧值**。实测：
    #   库内已改成 cancelled，裸 with_for_update 返回的对象 status 仍读作 pending；
    #   加上 populate_existing 才刷新成 cancelled。
    # 后果：`assert_order_can_add_receivable` 看到的是"取消之前"的状态，
    # 于是"先读到 pending → 订单被并发取消 → 新增应收成功"这个窗口就成立了。
    # 加锁的意义是"拿到锁之后看到最新事实"，不刷新就等于白锁。
    return (
        await session.execute(
            select(SalesOrder)
            .where(SalesOrder.id == order_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


async def assert_plan_accepts_payment(
    session: AsyncSession, plan: ReceivablePlan, *, action: str = "登记回款"
) -> None:
    """这个应收节点还收不收钱（C5-01，2026-10-10 修）。

    **两个判据都要看，缺一不可**：

    1. **节点本身已取消**：`cancelled` 随订单取消而来（`payment/model.py` 的标签就是
       "已取消（随订单）"），它不再参与催收。
    2. **所属订单已取消**：这一条不能靠"节点是 cancelled"代替 —— 取消订单时
       `order/router.py` **只把非 paid 的节点置为 cancelled**，已结清的节点保持
       `paid` 不动。所以"订单已取消、节点还是 paid"是**真实可达**的状态。

    **修的是什么**：实测订单与节点都已取消时，`POST /payments` 仍返回 200 并落库、
    `POST /payments/{id}/confirm` 再返回 200 —— 最后形成"订单已取消、节点已取消、
    回款已确认"三者互相矛盾的状态，账上多出一笔永远对不上的钱。
    根因是这两个入口**都没看生命周期**（`assert_order_can_add_receivable` 只被
    建应收的三个入口调用），而取消订单的提示语还写着"回款未受影响" ——
    那句话把缺陷说成了设计意图。

    **锁序**：调用方必须**已经持有节点行锁**（`get_visible_plan(for_update=True)` /
    `lock_plan`）。这里再锁订单，方向是 `sales_orders → receivable_plans`，
    **与项目统一锁序一致**，不会引入死锁。只有真要拒绝时才去锁订单，
    正常路径不多付一次锁的代价。
    """
    if plan.status == "cancelled":
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"该应收节点已随订单取消，不能再{action}；如确需继续收款，请先恢复订单",
            422,
        )
    order = await lock_order(session, plan.order_id)
    if order is not None and order.status == "cancelled":
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"订单已取消，不能再{action}；如确需继续收款，请先恢复订单",
            422,
        )


async def lock_plan(session: AsyncSession, plan_id: int) -> ReceivablePlan:
    """按统一锁序锁住应收节点行；不存在抛 404。

    为什么确认/驳回必须锁它：两个各 50 的确认请求锁的是**两条不同的回款记录**，
    彼此锁不到；只锁回款记录时两边都会读到"已确认合计 50"，各写一次「部分回款」，
    实际合计已是 100 而节点状态还停在部分回款（写偏斜 + 最后写覆盖）。
    先锁共同节点再汇总，后到的那个在锁上等；等到时先到者已提交，
    PostgreSQL READ COMMITTED 下重新汇总的这条 SELECT 取新快照，才看得到 100。
    """
    plan = (
        await session.execute(
            select(ReceivablePlan)
            .where(ReceivablePlan.id == plan_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if plan is None:
        raise AppError(ErrorCode.NOT_FOUND, "应收节点不存在", 404)
    return plan


def resolve_payment_currency(plan: ReceivablePlan, requested: str | None) -> str:
    """定这笔回款的币种：默认继承应收节点，给了不一致的币种就明确拒绝。

    为什么要专门写一个函数：`payment_records.currency` 有列默认值 CNY，
    登记时不传币种于是等于"记成人民币" —— 美元节点上凭空多出一笔人民币回款，
    汇总时按 1:1 与节点金额相减，账直接错，而且错得看不出来。

    给了不同币种时既不猜汇率也不悄悄改成节点币种：跨币种核销按哪个汇率折算、
    汇兑差异记到哪里都还没有定论，随便实现一种都可能是错的，所以拒绝并说清
    该怎么处理，把判断留给业务口径。
    """
    source = (plan.currency or "").strip().upper()
    if not source:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"应收节点 #{plan.id} 没有币种，无法判定这笔回款的币种；请先补齐节点币种再登记",
            422,
        )
    if requested is None or not requested.strip():
        return source
    asked = requested.strip().upper()
    if asked != source:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"回款币种 {asked} 与应收节点 #{plan.id} 的币种 {source} 不一致："
            "跨币种核销规则未确认，暂不支持（不按汇率折算、也不改记成节点币种）；"
            "请按节点币种登记，或先换汇后按节点币种录入",
            422,
        )
    return source


async def get_payment_or_404(session: AsyncSession, payment_id: int) -> PaymentRecord:
    record = await session.get(PaymentRecord, payment_id)
    if record is None:
        raise AppError(ErrorCode.NOT_FOUND, "回款记录不存在", 404)
    return record


# ---------------------------------------------------------------------------
# 数据范围
#
# 应收/回款没有自己的 owner_id —— 归属跟着订单走，所以可见性统一
# 「取订单负责人 -> ensure_in_scope」。列表用子查询把范围下推到 SQL，
# 这样分页和计数都正确（先查出来再过滤会让 total 偏大）。
# ---------------------------------------------------------------------------
async def visible_order_ids_stmt(session: AsyncSession, user):
    """当前用户可见的订单 id 子查询；`all` 权限返回 None 表示不过滤。"""
    from app.core.data_scope import scoped_owner_ids

    owner_ids = await scoped_owner_ids(session, user)
    if owner_ids is None:
        return None
    stmt = select(SalesOrder.id)
    if owner_ids:
        stmt = stmt.where(SalesOrder.owner_id.in_(owner_ids))
    else:
        # 范围内一个负责人都没有 —— 用一个恒假条件，别退化成"看全部"
        stmt = stmt.where(SalesOrder.id < 0)
    return stmt


async def assert_order_visible(session: AsyncSession, user, order_id: int) -> None:
    """校验订单在数据范围内（应收/回款的可见性就等于订单的可见性）。"""
    from app.core.data_scope import ensure_in_scope

    order = await session.get(SalesOrder, order_id)
    await ensure_in_scope(session, user, owner_id=order.owner_id if order else None, label="订单")


async def assert_order_can_add_receivable(order: SalesOrder) -> None:
    """取消的订单不允许再产生应收（N03，2026-10-09 修）。

    从前三个入口（`POST /receivables`、`POST /orders/{id}/receivables`、
    `POST /orders/{id}/receivables/generate`）都只校验"订单存在 + 在数据范围内"，
    **没有一条看订单生命周期** —— 订单正式取消之后还能继续建应收，实测返回 200
    并生成了一条有效逾期节点，于是已取消的订单又在催收队列里冒出来。

    `assert_order_visible` 只管可见性（它的文档字符串就是这么写的），
    生命周期是另一件事，所以单独一个函数、三个入口统一调用。

    只拦 `cancelled`：别的状态（待生产、已发货……）建应收都是正常的。
    """
    if order.status == "cancelled":
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            "订单已取消，不能再新增应收节点；如确需继续收款，请先恢复订单",
            422,
        )


async def get_visible_plan(
    session: AsyncSession, user, plan_id: int, *, for_update: bool = False
) -> ReceivablePlan:
    """取节点并校验数据范围；`for_update=True` 时按统一锁序先锁住节点行。

    所有会写节点状态（含"改金额后重算"）的入口都必须走 `for_update=True`：
    重算必须在锁内发生，否则与并发确认互相覆盖（后写的那个用的是旧汇总）。
    """
    plan = (
        await lock_plan(session, plan_id)
        if for_update
        else await get_plan_or_404(session, plan_id)
    )
    await assert_order_visible(session, user, plan.order_id)
    return plan


async def get_visible_payment(
    session: AsyncSession, user, payment_id: int, *, for_update: bool = False
) -> PaymentRecord:
    """取回款并校验数据范围；`for_update=True` 时按统一锁序加锁。

    锁序固定为「应收节点 → 回款记录」：先读出它挂的节点（这一步不加锁，只为
    知道该锁哪一行），锁住共同节点，再锁回款记录自己。反过来（先锁回款再锁节点）
    会和"改计划金额"那条路径的加锁顺序相反，两边各持一把互相等就是死锁。
    """
    if for_update:
        # 不加锁地读一次，只为拿到 receivable_plan_id。
        known = await get_payment_or_404(session, payment_id)
        if known.receivable_plan_id:
            await lock_plan(session, known.receivable_plan_id)
        record = (
            await session.execute(
                select(PaymentRecord)
                .where(PaymentRecord.id == payment_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if record is None:
            raise AppError(ErrorCode.NOT_FOUND, "回款记录不存在", 404)
    else:
        record = await get_payment_or_404(session, payment_id)
    await assert_order_visible(session, user, record.order_id)
    return record



async def order_finance_summary(session: AsyncSession, order_id: int) -> dict:
    """订单的应收/回款概览，给订单详情页顶部用。"""
    plans = (
        await session.execute(
            select(ReceivablePlan).where(ReceivablePlan.order_id == order_id)
        )
    ).scalars().all()
    order = await session.get(SalesOrder, order_id)
    received = (
        await session.execute(
            select(func.coalesce(func.sum(PaymentRecord.received_amount), 0)).where(
                PaymentRecord.order_id == order_id, PaymentRecord.status == "confirmed"
            )
        )
    ).scalar_one()
    pending_confirm = (
        await session.execute(
            select(func.coalesce(func.sum(PaymentRecord.received_amount), 0)).where(
                PaymentRecord.order_id == order_id, PaymentRecord.status == "pending"
            )
        )
    ).scalar_one()
    total = order.total_amount if order else ZERO
    return {
        "order_amount": _f(total),
        "planned_amount": _f(sum((p.amount for p in plans), ZERO)),
        "received_amount": _f(Decimal(received)),
        "pending_confirm_amount": _f(Decimal(pending_confirm)),
        "unreceived_amount": _f((total or ZERO) - Decimal(received)),
        "plan_count": len(plans),
        "overdue_count": len([p for p in plans if p.status == "overdue"]),
    }
