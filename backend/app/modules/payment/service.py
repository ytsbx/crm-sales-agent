"""应收与回款逻辑。"""

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.core.timebase import today_business
from app.modules.file.model import FileRecord
from app.modules.order.model import SalesOrder
from app.modules.payment.model import (
    PAYMENT_STATUS_LABEL,
    PLAN_STATUS_LABEL,
    PaymentAllocation,
    PaymentRecord,
    ReceivablePlan,
)
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
    """某一期**实际分配到的**已确认金额（第十批主人 2026-10-13 定的口径）。

    ## 为什么不再按 `payment_records.receivable_plan_id` 汇总

    那个列是"登记时选的那一期"（用户意图），不是"这笔钱实际覆盖了哪些期"。
    两者只在**不超收**时一致。主人定的口径要求"每一期必须实际分配的金额达到
    该期应收金额才能算完成"，所以汇总必须读**分配表**：

      · 一条 200 的回款挂在一期应收 100 上 → 只有 100 属于这一期，
        另外 100 按到期日自动抵到后续期（`allocate_payment`）；
      · 读 `receivable_plan_id` 会把 200 全算给一期，二期永远显示未收
        （这正是改造前的实测现象）。

    ⚠️ 返回的是**已确认**回款的分配额；未确认（pending/rejected）的钱不算已收。
    """
    allocated = (
        await session.execute(
            select(func.coalesce(func.sum(PaymentAllocation.amount), 0))
            .join(PaymentRecord, PaymentRecord.id == PaymentAllocation.payment_id)
            .where(
                PaymentAllocation.plan_id == plan_id,
                PaymentRecord.status == "confirmed",
            )
        )
    ).scalar_one()
    # ⚠️ **兜底**：**从未有过任何分配**的已确认回款，仍按 `receivable_plan_id` 计入。
    #
    # 判据是"这笔回款**在整个系统里**一条分配都没有"，不是"对**这一期**没有分配"
    # —— 后者会漏一个大洞（2026-10-13 实测踩到）：
    #   人工把 200 全改分给二期之后，一期就没有分配行了；若按"对这一期没分配"
    #   去兜底，这 200 又被算回一期 → **一期凭空显示已收 200**（双计），
    #   而它其实一分钱都没分到。
    #
    # 为什么还需要兜底（2026-10-13 加）：
    #   · 迁移只回填了**当时已存在**的已确认回款；之后若有代码路径/人工补数
    #     插入"有回款、无分配"的行（实测：有 15 个既有测试脚本与部分历史数据
    #     就是这种形状），按分配表汇总会把这些钱**算成没收**，
    #     于是"明明收齐了却显示未收"——比改造前的毛病更难查；
    #   · 兜底只认"**完全没有**分配行的回款"：已经分配过的回款不再按
    #     `receivable_plan_id` 计一次，避免**双计**（一笔 200 分成 100+100 时，
    #     若两边都算，一期会显示 200）。
    unallocated = (
        await session.execute(
            select(func.coalesce(func.sum(PaymentRecord.received_amount), 0)).where(
                PaymentRecord.receivable_plan_id == plan_id,
                PaymentRecord.status == "confirmed",
                # "这笔回款一条分配都没有"（而不是"对这一期没有"）
                ~select(PaymentAllocation.id)
                .where(PaymentAllocation.payment_id == PaymentRecord.id)
                .exists(),
            )
        )
    ).scalar_one()
    return Decimal(allocated) + Decimal(unallocated)


async def serialize_allocations(session: AsyncSession, payment_id: int) -> list[dict]:
    """一笔回款的分配明细（审计与页面共用一份形状）。

    带 `plan_name` / `due_date`：审计里光有 `plan_id` 说不清"原来算在哪一期"，
    而主人明确要求审计要能回答**原归属期数 → 新归属期数**。
    """
    rows = (
        await session.execute(
            select(PaymentAllocation, ReceivablePlan)
            .join(ReceivablePlan, ReceivablePlan.id == PaymentAllocation.plan_id)
            .where(PaymentAllocation.payment_id == payment_id)
            .order_by(ReceivablePlan.due_date.asc(), ReceivablePlan.id.asc())
        )
    ).all()
    return [
        {
            "plan_id": allocation.plan_id,
            "plan_name": plan.plan_name,
            "due_date": plan.due_date.isoformat() if plan.due_date else None,
            "amount": _f(allocation.amount),
            "source": allocation.source,
        }
        for allocation, plan in rows
    ]


async def _plan_allocated(session: AsyncSession, plan_id: int) -> Decimal:
    """某一期已被分配掉的**全部**确认金额（含自动与人工）。"""
    return await confirmed_amount(session, plan_id)


async def allocate_payment(
    session: AsyncSession, record: PaymentRecord, *, operator_id: int | None
) -> Decimal:
    """把这笔已确认回款**按到期日从早到晚**分配到各期，返回实际分出去的金额。

    ## 规则（主人 2026-10-13 拍板）

    1. 一笔银行流水仍然只有一行 `payment_records` —— 超出的部分**不拆记录**；
    2. 先补**人工**分配（`source='manual'`）：人定过的归属系统不许改
       （主人：「不能擅自调整已经登记的收款归属」）；
    3. 剩下的额度按**到期日从早到晚**填各期的空缺
       （该期应收 − 已分配额），填满一期再下一期；
    4. 全部应收都填满后仍有余额 → **不分配**（挂"待分配"）。
       不硬塞给某一期：那会让那一期显示虚高的已收，比"待分配"更难查。

    ## 幂等

    重算前先删掉这笔回款的**自动**分配，再按当前各期状态重算：
    重复确认、或先确认后被别的回款改变分布，都会得到一致结果。
    人工分配始终保留，且优先级高于自动。
    """
    # ① 保留人工分配，清掉旧的自动分配（重算）
    manual_rows = (
        await session.execute(
            select(PaymentAllocation).where(
                PaymentAllocation.payment_id == record.id,
                PaymentAllocation.source == "manual",
            )
        )
    ).scalars().all()
    await session.execute(
        delete(PaymentAllocation).where(
            PaymentAllocation.payment_id == record.id,
            PaymentAllocation.source == "auto",
        )
    )
    await session.flush()
    manual_total = sum((Decimal(row.amount or 0) for row in manual_rows), Decimal(0))
    remaining = Decimal(record.received_amount or 0) - manual_total
    if remaining <= 0:
        return manual_total

    # ② 各期还缺多少（按到期日从早到晚；同日按 id，保证顺序确定）
    plans = (
        await session.execute(
            select(ReceivablePlan)
            .where(
                ReceivablePlan.order_id == record.order_id,
                ReceivablePlan.status != "cancelled",
            )
            .order_by(ReceivablePlan.due_date.asc(), ReceivablePlan.id.asc())
        )
    ).scalars().all()
    manual_by_plan: dict[int, Decimal] = {}
    for row in manual_rows:
        manual_by_plan[row.plan_id] = manual_by_plan.get(row.plan_id, Decimal(0)) + Decimal(
            row.amount or 0
        )

    allocated = Decimal(0)
    now = datetime.now(UTC)
    for plan in plans:
        if remaining <= 0:
            break
        need = Decimal(plan.amount or 0) - manual_by_plan.get(plan.id, Decimal(0))
        # 自动分配只填**当前还没有被任何确认回款覆盖**的空缺
        need -= await _auto_allocated_excluding(session, plan.id, record.id)
        if need <= 0:
            continue
        take = min(need, remaining)
        plan.currency = plan.currency or record.currency
        session.add(
            PaymentAllocation(
                payment_id=record.id,
                plan_id=plan.id,
                amount=take,
                source="auto",
                created_by=operator_id,
                created_at=now,
                updated_at=now,
            )
        )
        allocated += take
        remaining -= take
    await session.flush()
    return manual_total + allocated


async def rebalance_order_allocations(
    session: AsyncSession, order_id: int, *, operator_id: int | None = None
) -> int:
    """把这张单的**全部已确认回款**按到期日重新分配一遍，返回重算的回款笔数。

    ## 为什么需要（第十批实测发现）

    分配原来只在"确认回款"那一刻算一次。可是**应收金额会变**：
    财务把某一期从 100 改大到 200（实测用例 `check_round3_risk_repairs` 就是
    这个动作），原来收的钱不够了，而另一期超收出来的钱**不会自动补过来** ——
    于是"客户其实已经付够了，系统显示还差 100"。

    主人定的口径是"超收按到期日自动抵后续期"，所以分配必须跟着**应收的变化**
    重新平衡，不能只在收款那一刻定死。

    ## 规则

    逐笔按到期日从早到晚重新分配（复用 `allocate_payment`，它自己就只重算
    `auto` 那部分、保留人工分配）。**按回款登记顺序**逐笔处理：先登记的先用
    额度，后登记的往后排 —— 与"先到账先冲抵"的直觉一致，且结果确定（不依赖
    数据库返回顺序）。

    人工调过分配的回款：其人工部分原样保留，剩余额度仍参与自动平衡。
    """
    records = (
        await session.execute(
            select(PaymentRecord)
            .where(
                PaymentRecord.order_id == order_id,
                PaymentRecord.status == "confirmed",
            )
            .order_by(PaymentRecord.received_date.asc(), PaymentRecord.id.asc())
        )
    ).scalars().all()
    for record in records:
        await allocate_payment(session, record, operator_id=operator_id)
    await session.flush()
    return len(records)


async def _auto_allocated_excluding(
    session: AsyncSession, plan_id: int, exclude_payment_id: int
) -> Decimal:
    """某一期**除这笔回款之外**已分配到的确认金额（用于算"还差多少"）。"""
    total = (
        await session.execute(
            select(func.coalesce(func.sum(PaymentAllocation.amount), 0))
            .join(PaymentRecord, PaymentRecord.id == PaymentAllocation.payment_id)
            .where(
                PaymentAllocation.plan_id == plan_id,
                PaymentAllocation.payment_id != exclude_payment_id,
                PaymentRecord.status == "confirmed",
            )
        )
    ).scalar_one()
    return Decimal(total)


def is_plan_overdue(
    *, status: str, balance: Decimal, due_date: date | None, today: date
) -> bool:
    """应收节点是否逾期 —— **全项目唯一判据**（分析、AI 洞察、审批条件都走它）。

    口径（2026-10-13 主人拍板）：
    - 到期日已过、且**没有收齐**（余额 > 0）→ 逾期。**收过一部分也算**
      （改前"付了 1 元就不算逾期"会把大额未收货款藏住，主人点名要改）；
    - 已取消（`cancelled`）不算逾期 —— 它是终态，不参与催收；
    - 没有到期日（理论上建节点必填，防脏数据）不算逾期；
    - 收齐了（余额 ≤ 0）不算逾期。

    改口径时**只改这一处**：报表、AI、审批规则全部引用它，
    不会出现"这边算逾期、那边不算"的两套账。
    """
    return (
        status != "cancelled"
        and due_date is not None
        and balance > ZERO
        and due_date < today
    )


async def recalc_plan(session: AsyncSession, plan: ReceivablePlan) -> None:
    """按已确认的回款重算应收节点状态。

    规则（口径 2026-10-13 主人拍板，见 `is_plan_overdue`）：
    全额收齐 → 已回款；**到期日已过且没收齐 → 已逾期**（收过一部分也算）；
    未到期且收过一部分 → 部分回款；未到期且一分没收 → 待回款。

    ⚠️ 口径变更说明（2026-10-13，主人点名）：改前的规则是"收过一分钱就不算逾期"，
    于是 10 万的应收收了 1 元就永远显示"部分回款"，逾期条数/审批加签都看不见它
    —— 大额未收货款被藏住。现在"过期 + 没收齐"一律逾期，
    `partial` 只留给**还没到期**的节点。

    **`cancelled` 不被重算覆盖**（N02，2026-10-09 修）：见 `TERMINAL_PLAN_STATUSES`。
    从前这里没有任何保护，而 `PATCH /receivables/{id}` 结尾**无条件**调用本函数 ——
    于是"只改一个备注"就会把随订单取消的节点按"没收到钱 + 已过期"算回 `overdue`，
    取消状态凭空消失。

    **`paid` 会参与重算**（C4-01，2026-10-09 修）：金额被改大、或回款被作废之后，
    它必须能如实退回 —— 否则会出现"余额大于零却显示已结清"。
    退回的目标按 `is_plan_overdue` 决定：未到期退 `partial`，已到期退 `overdue`
    （口径变更后：过期且没收齐就是逾期，不再因为"收过一点"而留在 `partial`）。

    **调用方必须已经持有该节点的行锁**（`lock_plan`）：这里的汇总结果直接写回
    节点状态，不在锁内汇总就等于"读旧数、写覆盖"（两个 50 并发确认会留下
    partial）。函数体内不再加锁，免得同一事务里重复取锁掩盖掉调用方的漏锁。
    """
    if plan.status in TERMINAL_PLAN_STATUSES:
        return
    received = await confirmed_amount(session, plan.id)
    amount = plan.amount or ZERO
    if received > 0 and received >= amount:
        plan.status = "paid"
    elif is_plan_overdue(
        status=plan.status, balance=amount - received, due_date=plan.due_date, today=today_business()
    ):
        plan.status = "overdue"
    elif received > 0:
        plan.status = "partial"
    else:
        plan.status = "pending"


async def refresh_overdue_plans(session: AsyncSession) -> int:
    """把"时间自己走过、没人写库"的应收节点状态刷新一遍（每日定时任务调用）。

    为什么要这个：逾期与否取决于**今天**，而 `recalc_plan` 只在有人写节点/
    确认回款/取消订单时被调用。一张 9 月到期的应收如果 10 月没人碰过，
    它的状态会一直停在 `pending`/`partial`，界面上看不出逾期。
    本函数扫"未收齐 且 已过期 且 状态还不是 overdue"的节点，逐个走 `recalc_plan`
    （同一条判据，不写第二份），返回刷新条数。

    只扫到期日已过、状态未终态、且金额大于已确认回款的行 —— 一条 SQL 捞出来，
    不受数据量增长影响。
    """
    received_sq = (
        select(func.coalesce(func.sum(PaymentRecord.received_amount), 0))
        .where(
            PaymentRecord.receivable_plan_id == ReceivablePlan.id,
            PaymentRecord.status == "confirmed",
        )
        .scalar_subquery()
    )
    plans = (
        await session.execute(
            select(ReceivablePlan).where(
                ReceivablePlan.status.in_(("pending", "partial")),
                ReceivablePlan.due_date.is_not(None),
                ReceivablePlan.due_date < today_business(),
                ReceivablePlan.amount > received_sq,
            )
        )
    ).scalars().all()
    for plan in plans:
        await recalc_plan(session, plan)
    return len(plans)


async def serialize_plan(session: AsyncSession, plan: ReceivablePlan) -> dict:
    received = await confirmed_amount(session, plan.id)
    order = await session.get(SalesOrder, plan.order_id)
    balance = (plan.amount or ZERO) - received
    # 显示状态按**口径现算**（2026-10-13）：存储的状态列是缓存，写路径与每日
    # 定时任务都会刷它；但"时间自己走过"（到期日到了）不会触发任何写，
    # 所以列表/详情必须自己判一次，否则会出现"工作台说逾期、列表显示部分回款"。
    status = (
        "overdue"
        if is_plan_overdue(
            status=plan.status, balance=balance, due_date=plan.due_date, today=today_business()
        )
        else plan.status
    )
    return {
        "id": plan.id,
        "order_id": plan.order_id,
        "order_no": order.order_no if order else None,
        "customer_id": order.customer_id if order else None,
        "plan_name": plan.plan_name,
        "due_date": plan.due_date,
        "amount": _f(plan.amount),
        "received_amount": _f(received),
        "remaining_amount": _f(balance),
        "currency": plan.currency,
        "status": status,
        "status_label": PLAN_STATUS_LABEL.get(status, status),
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
        #: 这笔钱**实际分到哪几期、各多少**（第十批）；未确认时为空。
        #: 带上它，前端"调整分配"弹窗就不用再发一次请求，而且列表上能一眼看出
        #: "登记的期"与"实际分的期"是否一致（超收时两者会不同）。
        "allocations": (
            await serialize_allocations(session, record.id)
            if record.status == "confirmed"
            else []
        ),
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


def assert_received_date_not_future(received_date, *, action: str = "登记回款") -> None:
    """实收日期不能在未来（第四批审查 P1，2026-10-12 实测复现）。

    ## 为什么这是 P1

    从前登记 / 修改 / 确认三处**都没有**校验实收日期。实测：今天 10 月 12 日、
    到账日填 10 月 22 日，走完真实的登记 + 确认之后，回款是 `confirmed`、
    应收节点是 `paid`，**付定金/收款两个跟单节点都标成完成**。

    两个后果，都不是"显示不好看"：

    1. **钱还没到就算收齐** —— 订单页显示"款已收齐"，看的人据此不再催款；
    2. **按确认时间统计的本月回款提前计入** —— 报表里这个月的回款多了一笔
       实际还没发生的钱（外部对账时对不上）。

    ## 口径

    判据是"实收日期 ≤ 业务今天"（`today_business()`，与全站同一份业务日期，
    不是 UTC 日期 —— 用 UTC 会在北京时间早上 8 点前把"今天"判成未来）。
    确需预登记未来款项时，那属于**应收计划**要表达的"预期"，
    不该记成"已经收到的钱"。

    ## 为什么三处都要调（含确认入口）

    确认入口也必须查：库里**存量**可能有异常的未来日期待确认记录（本次修复前
    登记的），只在登记处加校验拦不住它们；而且确认才是"这笔钱成立"的那一步。
    校验必须放在改状态、重算应收、生成通知**之前** —— 被拒时不能留下半改的状态。
    """
    if received_date is None:
        return
    today = today_business()
    if received_date > today:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"{action}失败：实收日期 {received_date:%Y-%m-%d} 在未来（今天是 {today:%Y-%m-%d}）。"
            "「已收到」记的是**已经到账**的钱，日期不能晚于今天；"
            "预期将来收款请登记到应收计划（到期日）上，不要记成已收款",
            422,
        )


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

    **锁序**：调用方必须已经按 `sales_orders → receivable_plans` 取得订单和节点
    行锁（`get_visible_plan(for_update=True)` / `get_visible_payment(..., for_update=True)`）。
    这里不能再补锁订单：回款登记若先持有节点锁再来拿订单锁，会和订单取消的
    `订单 → 节点` 顺序形成循环等待。订单锁由调用方统一取得后，这里只读取锁内事实。
    """
    if plan.status == "cancelled":
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"该应收节点已随订单取消，不能再{action}；如确需继续收款，请先恢复订单",
            422,
        )
    order = await session.get(SalesOrder, plan.order_id)
    if order is not None and order.status == "cancelled":
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"订单已取消，不能再{action}；如确需继续收款，请先恢复订单",
            422,
        )


async def lock_plan(session: AsyncSession, plan_id: int) -> ReceivablePlan:
    """锁住应收节点行；涉及订单的调用方必须先锁订单。

    `lock_order_and_plan` 是需要两把锁时的标准入口；本函数保留为已经持有
    订单锁的路径（例如确认流程在同一事务内的再次刷新）使用。不存在抛 404。

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


async def lock_order_and_plan(
    session: AsyncSession, plan_id: int
) -> tuple[SalesOrder, ReceivablePlan]:
    """按统一顺序锁住订单和应收节点，返回两行的最新值。

    先无锁读取节点只是为了得到所属订单 id；真正的写锁严格按订单、节点的
    顺序取得。订单可能已被删除或节点可能在等待期间消失，均按不存在处理。
    """
    known = (
        await session.execute(
            select(ReceivablePlan).where(ReceivablePlan.id == plan_id)
        )
    ).scalar_one_or_none()
    if known is None:
        raise AppError(ErrorCode.NOT_FOUND, "应收节点不存在", 404)
    order = await lock_order(session, known.order_id)
    if order is None:
        raise AppError(ErrorCode.NOT_FOUND, "订单不存在", 404)
    plan = await lock_plan(session, plan_id)
    return order, plan


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
            f"应收节点编号 {plan.id} 没有币种，无法判定这笔回款的币种；请先补齐节点币种再登记",
            422,
        )
    if requested is None or not requested.strip():
        return source
    asked = requested.strip().upper()
    if asked != source:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"回款币种 {asked} 与应收节点编号 {plan.id} 的币种 {source} 不一致："
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


def split_amounts(total: Decimal, ratios: list[Decimal]) -> list[Decimal]:
    """按比例把订单总额拆成各期金额。**唯一的拆法**（第九批问题 ①）。

    规则（与前端预览、与后建应收计划必须完全一致）：

    - 前面各期 `total × ratio` **四舍五入到分**；
    - **末期补差额**（`总额 − 前面之和`），保证各期之和严格等于总额；
    - 补完**回头检查有没有非正数期**：金额极小时前面各期各自进位，之和可能
      已经超过总额，末期就成了负数（`0.03` 元按 5×20% 分 →
      `0.01、0.01、0.01、0.01、−0.01`）。负数应收会让财务核销与催收都对不上，
      所以**明确拒绝**，要求减少期数或调整比例。

    ## 为什么必须抽成一处

    这套算法原来在 `payment/router.py`（后建应收计划）里写了一遍**并且带了
    非正数校验**，而 `order/service.py`（转单时按计划建应收）里**又写了一遍、
    却漏了那道校验** —— 于是转单能生成负数额应收，后建却不行。
    两份实现必然漂移，所以合成这一处。

    ⚠️ 舍入用 Python `Decimal` 的默认模式（`ROUND_HALF_EVEN`）。前端预览是
    JavaScript 的 `Math.round`（半分向上），两者在"恰好半分"的金额上会给出
    不同结果（`0.05` 分两期：JS 给 `0.03/0.02`，这里给 `0.02/0.03`）。
    所以**前端不要自己算预览**，一律用后端返回的结果（见 `preview_split`）。
    """
    if not ratios:
        raise AppError(ErrorCode.PARAM_ERROR, "至少要有一期比例", 422)
    amounts: list[Decimal] = []
    for index, ratio in enumerate(ratios):
        if index == len(ratios) - 1:
            amounts.append(total - sum(amounts, Decimal(0)))
        else:
            amounts.append((total * ratio).quantize(Decimal("0.01")))
    if any(amount <= 0 for amount in amounts):
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"订单总额 {total} 按当前比例分 {len(ratios)} 期会分出不大于 0 的期"
            f"（{'、'.join(str(a) for a in amounts)}）。请减少期数或调整比例",
            422,
        )
    return amounts


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
    """取节点并校验数据范围；`for_update=True` 时按统一锁序先锁订单、再锁节点。

    所有会写节点状态（含"改金额后重算"）的入口都必须走 `for_update=True`：
    重算必须在锁内发生，否则与并发确认互相覆盖（后写的那个用的是旧汇总）。
    """
    if for_update:
        _, plan = await lock_order_and_plan(session, plan_id)
    else:
        plan = await get_plan_or_404(session, plan_id)
    await assert_order_visible(session, user, plan.order_id)
    return plan


async def get_visible_payment(
    session: AsyncSession, user, payment_id: int, *, for_update: bool = False
) -> PaymentRecord:
    """取回款并校验数据范围；`for_update=True` 时按统一锁序加锁。

    锁序固定为「订单 → 应收节点 → 回款记录」：先读出回款所属订单和节点（这一步
    不加锁，只为知道该锁哪几行），锁订单、锁共同节点，再锁回款记录自己。所有会
    同时碰订单与应收的路径都遵守这条顺序，避免与订单取消互相等待。
    """
    if for_update:
        # 不加锁地读一次，只为拿到 receivable_plan_id。
        known = await get_payment_or_404(session, payment_id)
        # 回款路径也必须先锁订单：订单取消是「订单 → 节点」，登记/确认/修改/
        # 凭证操作不能再走「节点 → 回款」后补订单，否则会形成死锁环。
        # 订单归属以应收节点为准；正常数据里它与 payment_records.order_id 一致，
        # 这样即使历史脏数据两列不一致，也不会因为先锁错订单而破坏全局锁序。
        known_plan = (
            await session.get(ReceivablePlan, known.receivable_plan_id)
            if known.receivable_plan_id
            else None
        )
        order_id = known_plan.order_id if known_plan is not None else known.order_id
        order = await lock_order(session, order_id)
        if order is None:
            raise AppError(ErrorCode.NOT_FOUND, "订单不存在", 404)
        locked_plan = None
        if known.receivable_plan_id:
            locked_plan = await lock_plan(session, known.receivable_plan_id)
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
        # 这两列历史上没有外键约束，脏数据可能把回款挂到另一张订单的应收节点。
        # 不能只按 order_id 做范围校验，否则确认/修改时会在另一订单节点上重算，
        # 既可能越过数据范围，也会把两张订单的账混在一起。发现不一致直接冻结，
        # 交给数据修复，不让业务入口继续扩大损害。
        if record.receivable_plan_id:
            if locked_plan is None or locked_plan.order_id != record.order_id:
                raise AppError(
                    ErrorCode.STATUS_NOT_ALLOWED,
                    "回款与应收节点的订单归属不一致，已暂时冻结；请联系管理员修复数据",
                    422,
                )
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
    # 逾期计数按**余额**算（收过部分款+过期也算），与 `is_plan_overdue` 同一口径。
    # 状态列也可能过期没刷新（时间自己走过），所以这里不依赖 status == "overdue"。
    today = today_business()
    balances: dict[int, Decimal] = {}
    if plans:
        rows = (
            await session.execute(
                select(
                    PaymentRecord.receivable_plan_id,
                    func.coalesce(func.sum(PaymentRecord.received_amount), 0),
                )
                .where(
                    PaymentRecord.receivable_plan_id.in_([p.id for p in plans]),
                    PaymentRecord.status == "confirmed",
                )
                .group_by(PaymentRecord.receivable_plan_id)
            )
        ).all()
        balances = {int(pid): Decimal(total) for pid, total in rows}
    total = order.total_amount if order else ZERO
    return {
        "order_amount": _f(total),
        "planned_amount": _f(sum((p.amount for p in plans), ZERO)),
        "received_amount": _f(Decimal(received)),
        "pending_confirm_amount": _f(Decimal(pending_confirm)),
        "unreceived_amount": _f((total or ZERO) - Decimal(received)),
        "plan_count": len(plans),
        "overdue_count": len(
            [
                p
                for p in plans
                if p.status == "overdue"
                or is_plan_overdue(
                    status=p.status,
                    balance=(p.amount or ZERO) - balances.get(p.id, ZERO),
                    due_date=p.due_date,
                    today=today,
                )
            ]
        ),
    }
