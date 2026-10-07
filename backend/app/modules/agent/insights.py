"""Agent 专用分析（03-API §37 Specialized）。

## 为什么这些接口不等于"调一次大模型"

`/agent/risk-analysis`（回款风险）算的是**逾期金额、逾期天数、集中度**；
`/agent/followup-suggestion`（跟进建议）算的是**多久没联系、有没有逾期任务**。
这些是**确定性的事实**，有标准答案，不该依赖大模型（也不该在
`DEEPSEEK_API_KEY` 没配时整个功能不可用）。

所以这里的口径是：

- **`analysis` 是主体**：本地确定性计算，永远可用，可测试、可复现；
- **`commentary` 是可选增强**：模型配了就给一段叙述，没配就是 `None` 并说明原因
  （生成逻辑见 `commentary.py`，本文件只管算事实）。

这样"回款风险"这种功能在没配模型的部署里照样能报警，
而配了模型的地方多一层人话解释 —— 而不是把核心能力绑在外部服务上。
"""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.data_scope import ensure_in_scope, scoped_owner_ids
from app.core.errors import AppError, ErrorCode
from app.core.timebase import today_business, to_business
from app.modules.customer.model import Customer
from app.modules.opportunity.model import Opportunity, OpportunityStage
from app.modules.order.model import SalesOrder
from app.modules.payment.model import PaymentRecord, ReceivablePlan
from app.modules.task.model import Task
from app.modules.user.model import User

ZERO = Decimal(0)


def _f(value) -> float | None:
    return None if value is None else round(float(value), 2)


async def _opportunity_items(session: AsyncSession, opportunity_id: int):
    """取商机需求明细（ORM 对象，不是序列化 dict）。

    调用方要拿 `sku_id` / `quantity` 去核价，dict 形态还得再解一遍字段名，
    直接用 ORM 更直接（`opp_service.list_items` 是给接口返回用的）。
    """
    from app.modules.opportunity.model import OpportunityItem

    return list(
        (
            await session.execute(
                select(OpportunityItem)
                .where(OpportunityItem.opportunity_id == opportunity_id)
                .order_by(OpportunityItem.id.asc())
            )
        ).scalars().all()
    )


async def _visible_order_ids(session: AsyncSession, user) -> list[int] | None:
    """当前用户可见的订单 id；None 表示不限（数据范围 all）。

    应收/回款/订单都没有自己的负责人口径，统一跟着订单的 owner_id 走
    （与 payment 模块一致）。
    """
    owner_ids = await scoped_owner_ids(session, user)
    if owner_ids is None:
        return None
    stmt = select(SalesOrder.id)
    if owner_ids:
        stmt = stmt.where(SalesOrder.owner_id.in_(owner_ids))
    else:
        stmt = stmt.where(SalesOrder.id < 0)
    return list((await session.execute(stmt)).scalars().all())


def _is_overdue(due_at, today) -> bool:
    """这条待办逾期了吗（按**业务日期**比，§9.10）。

    原来写 `due_at.date() < today`：`.date()` 取的是 **UTC** 日期，而 `due_at` 是
    timestamptz（库里存 UTC）。北京时间凌晨那 8 小时里，"今天到期"会被算成
    "昨天到期"，凭空多出一条"有 N 个任务已逾期"的结论。
    """
    if due_at is None:
        return False
    return to_business(due_at).date() < today


async def _visible_open_tasks(
    session: AsyncSession,
    user,
    *,
    customer_id: int | None = None,
    lead_id: int | None = None,
    opportunity_id: int | None = None,
    limit: int | None = None,
) -> list[Task] | None:
    """某个业务对象下**当前用户看得见**的未办完待办。

    §9.1 复审（第九批复验）：客户摘要 / 跟进建议 / 商机分析原来是各写一遍
    `select(Task).where(<对象>_id == X, status in ...)`，**都只判了 `task:view`、
    没按负责人收范围** —— 于是"业务对象在范围内"又成了一条绕过任务模块的通道：
    只管自己数据的销售，能通过 AI 摘要读到**别人负责**的待办标题，而同一个客户的
    概览页与 `/customers/{id}/tasks` 却正确地什么也不给（同一份响应里自相矛盾）。

    现在三处共用这一份，口径与 `customer/router.py::/customers/{id}/tasks`、
    `customer/service.py::customer_overview` 完全一致（`scoped_owner_ids`）。

    返回 `None` 表示**没有 `task:view` 权限**（"看不到" ≠ "一条都没有"）；
    返回 `[]` 表示有权限、但这个对象名下确实没有你看得见的未办完待办。
    """
    if not ("admin" in getattr(user, "roles", []) or user.has("task:view")):
        return None

    conditions = [Task.status.in_(("pending", "doing"))]
    if customer_id is not None:
        conditions.append(Task.customer_id == customer_id)
    if lead_id is not None:
        conditions.append(Task.lead_id == lead_id)
    if opportunity_id is not None:
        conditions.append(Task.opportunity_id == opportunity_id)

    stmt = select(Task).where(*conditions)
    owner_ids = await scoped_owner_ids(session, user)
    if owner_ids is not None:
        # 范围不是 all 时按负责人收；`[]`（有权限但一个人都看不到）用恒假条件，
        # 不用 `in_([])` —— 后者在不同库上的语义不直观，恒假更直白。
        stmt = stmt.where(Task.owner_id.in_(owner_ids) if owner_ids else Task.id < 0)
    # 排序要可移植：`.nulls_last()` 是 PG 专有写法，本项目 `task/router.py:159`
    # 已经踩过（换库即 500）。改成"先排没有截止时间的、再按时间升序"。
    stmt = stmt.order_by(Task.due_at.is_(None).asc(), Task.due_at.asc())
    if limit is not None:
        stmt = stmt.limit(limit)
    return list((await session.execute(stmt)).scalars().all())


# ---------------------------------------------------------------------------
# 回款风险（03-API §37 POST /agent/risk-analysis）
# ---------------------------------------------------------------------------

async def receivable_risk(session: AsyncSession, user, *, order_id: int | None = None) -> dict:
    """回款风险分析：逾期节点、逾期金额、客户集中度。

    这是**确定性**风险：一个节点过了到期日还没收齐，就是逾期，
    不需要模型判断。模型只负责把它讲成人话。
    """
    today = today_business()
    visible = await _visible_order_ids(session, user)

    stmt = select(ReceivablePlan).where(
        ReceivablePlan.status.in_(("pending", "partial", "overdue"))
    )
    if order_id is not None:
        order = await session.get(SalesOrder, order_id)
        if order is None:
            raise AppError(ErrorCode.NOT_FOUND, "订单不存在", 404)
        await ensure_in_scope(session, user, owner_id=order.owner_id, label="订单")
        stmt = stmt.where(ReceivablePlan.order_id == order_id)
    elif visible is not None:
        stmt = stmt.where(ReceivablePlan.order_id.in_(visible))

    plans = (await session.execute(stmt)).scalars().all()

    received_map: dict[int, Decimal] = {}
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
        received_map = {int(pid): Decimal(total) for pid, total in rows}

    overdue: list[dict] = []
    upcoming: list[dict] = []
    overdue_amount = ZERO
    open_amount = ZERO

    for plan in plans:
        received = received_map.get(plan.id, ZERO)
        remaining = (plan.amount or ZERO) - received
        if remaining <= 0:
            continue
        open_amount += remaining
        item = {
            "plan_id": plan.id,
            "order_id": plan.order_id,
            "plan_name": plan.plan_name,
            "due_date": plan.due_date,
            "amount": _f(plan.amount),
            "received_amount": _f(received),
            "remaining_amount": _f(remaining),
        }
        if plan.due_date < today:
            item["overdue_days"] = (today - plan.due_date).days
            overdue_amount += remaining
            overdue.append(item)
        else:
            item["days_until_due"] = (plan.due_date - today).days
            upcoming.append(item)

    overdue.sort(key=lambda row: -row["overdue_days"])
    upcoming.sort(key=lambda row: row["days_until_due"])

    # 客户集中度：未收金额里最大的一家占多少 —— 单一客户欠太多本身就是风险
    concentration = None
    if open_amount > 0:
        by_order = {}
        for plan in plans:
            received = received_map.get(plan.id, ZERO)
            remaining = (plan.amount or ZERO) - received
            if remaining > 0:
                by_order[plan.order_id] = by_order.get(plan.order_id, ZERO) + remaining
        if by_order:
            order_ids = list(by_order)
            customer_rows = (
                await session.execute(
                    select(SalesOrder.id, SalesOrder.customer_id).where(
                        SalesOrder.id.in_(order_ids)
                    )
                )
            ).all()
            cust_of = {int(oid): cid for oid, cid in customer_rows}
            by_customer: dict[int, Decimal] = {}
            for oid, amount in by_order.items():
                cid = cust_of.get(oid)
                if cid is not None:
                    by_customer[cid] = by_customer.get(cid, ZERO) + amount
            if by_customer:
                top_cid, top_amount = max(by_customer.items(), key=lambda kv: kv[1])
                name = (
                    await session.execute(
                        select(Customer.name).where(Customer.id == top_cid)
                    )
                ).scalar_one_or_none()
                concentration = {
                    "customer_id": top_cid,
                    "customer_name": name,
                    "amount": _f(top_amount),
                    "ratio": round(float(top_amount / open_amount), 4),
                }

    level = "high" if overdue_amount > 0 else ("medium" if open_amount > 0 else "low")
    insights: list[str] = []
    if overdue:
        insights.append(
            f"有 {len(overdue)} 个应收节点已逾期，合计 {_f(overdue_amount)} 元，"
            f"最长逾期 {overdue[0]['overdue_days']} 天"
        )
    if concentration and concentration["ratio"] >= 0.5:
        insights.append(
            f"未收金额的 {concentration['ratio'] * 100:.0f}% 集中在"
            f"「{concentration['customer_name']}」一家，单一客户风险偏高"
        )
    if not overdue and open_amount > 0:
        insights.append(f"暂逾期记录，未收合计 {_f(open_amount)} 元")

    return {
        "scope": {"order_id": order_id},
        "level": level,
        "level_label": {"high": "高风险", "medium": "关注", "low": "正常"}[level],
        "summary": {
            "overdue_count": len(overdue),
            "overdue_amount": _f(overdue_amount),
            "open_amount": _f(open_amount),
            "upcoming_count": len(upcoming),
        },
        "overdue": overdue,
        "upcoming": upcoming[:10],
        "concentration": concentration,
        "insights": insights,
        "as_of": today,
    }


# ---------------------------------------------------------------------------
# 客户摘要（03-API §37 POST /agent/customer-summary）
# ---------------------------------------------------------------------------

async def customer_summary(session: AsyncSession, user, *, customer_id: int) -> dict:
    """客户摘要：基本盘 + 最近动态 + 待办，一次性给全。

    与 `/customers/{id}/overview` 的区别：那个给前端拼页面（数量 + 最近几条），
    这个给**人和模型读的摘要**（带判断性结论，比如"多久没联系了"）。
    """
    from app.modules.customer import service as customer_service

    customer = await customer_service.get_visible_customer(session, user, customer_id)
    overview = await customer_service.customer_overview(session, user, customer_id)
    owner = await session.get(User, customer.owner_id) if customer.owner_id else None
    today = today_business()

    days_since_followup = None
    if customer.last_followup_at:
        days_since_followup = (datetime.now(UTC) - customer.last_followup_at).days

    # 待办与概览同一口径（第九批 §9.1；复审时补上**负责人数据范围**）：
    # 只判 `task:view` 挡得住"没有任务模块权限的人"，挡不住"有权限、但这条待办是
    # 别人负责的" —— 那正是复验抓到的越权（概览返回空、摘要却列出别人的待办）。
    # 返回 None＝没有 task:view；[]＝有权限但没有你看得见的待办。两者不能混。
    open_tasks_rows = await _visible_open_tasks(
        session, user, customer_id=customer_id, limit=10
    )
    open_tasks: list[Task] = open_tasks_rows or []

    insights: list[str] = []
    if days_since_followup is None:
        insights.append("这个客户还没有任何跟进记录")
    elif days_since_followup >= 30:
        insights.append(f"已经 {days_since_followup} 天没有跟进，建议尽快联系")
    elif days_since_followup >= 14:
        insights.append(f"{days_since_followup} 天没跟进了，可以安排一次回访")

    overdue_tasks = [row for row in open_tasks if _is_overdue(row.due_at, today)]
    if overdue_tasks:
        insights.append(f"有 {len(overdue_tasks)} 个任务已逾期")

    # 第九批 §9.1：概览改成逐板块判权限后，没权限的板块计数是 None
    # （"看不到" ≠ "一条都没有"）。这里按"没有就不说"处理 ——
    # 不能把 None 当成 0 去比大小，更不能据此编出一句结论。
    open_opps = overview["counts"]["open_opportunities"]
    if open_opps:
        insights.append(f"还有 {open_opps} 个在谈商机")
    # 金额按币种分组（第九批 §9.9）：只有一种币种时才下"累计成交多少"的结论；
    # 多币种就并列写出来 —— 折算是业务规则，没拍板前不替它选汇率、也不加总。
    amounts = overview["counts"]["order_amounts"] or []
    if len(amounts) == 1:
        single = amounts[0]
        insights.append(
            f"累计成交 {single['amount']:.2f} {single['currency'] or '（币种待核实）'}"
        )
    elif amounts:
        parts = "；".join(
            f"{item['currency'] or '币种待核实'} {item['amount']:.2f}" for item in amounts
        )
        insights.append(f"累计成交（分币种）：{parts}")

    return {
        "customer": {
            "id": customer.id,
            "name": customer.name,
            "short_name": customer.short_name,
            "level": customer.level,
            "region": customer.region,
            "source": customer.source,
            "status": customer.status,
            "pool_status": customer.pool_status,
            "owner_id": customer.owner_id,
            "owner_name": owner.name if owner else None,
            "last_followup_at": customer.last_followup_at,
            "days_since_followup": days_since_followup,
        },
        "counts": overview["counts"],
        # 没权限、因而**没有取**的板块（第九批 §9.1）。明确列出来，
        # 免得模型把"没给"读成"这个客户没有"。
        "restricted": overview["restricted"],
        "recent_opportunities": overview["opportunities"],
        "recent_quotes": overview["quotes"],
        "recent_orders": overview["orders"],
        "recent_followups": overview["followups"],
        # 没有 task:view 时给 None（"看不到" ≠ "一条都没有"）；有权限但没有可见待办时给 []
        "open_tasks": (
            None
            if open_tasks_rows is None
            else [
                {
                    "id": row.id,
                    "title": row.title,
                    "status": row.status,
                    "priority": row.priority,
                    "due_at": row.due_at,
                    "overdue": _is_overdue(row.due_at, today),
                }
                for row in open_tasks
            ]
        ),
        "insights": insights,
        "as_of": today,
    }


# ---------------------------------------------------------------------------
# 跟进建议（03-API §37 POST /agent/followup-suggestion）
# ---------------------------------------------------------------------------

async def followup_suggestion(
    session: AsyncSession, user, *, customer_id: int | None = None, lead_id: int | None = None
) -> dict:
    """跟进建议：基于"多久没联系 / 有没有逾期任务 / 在谈商机"给下一步动作。

    建议是**规则推出来的**，不是模型编的：每一条都能指向一个具体事实
    （"14 天没跟进"、"2 个任务逾期"），业务才敢照着做。
    """
    if customer_id is None and lead_id is None:
        raise AppError(
            ErrorCode.REQUIRED_FIELD_MISSING, "至少要提供 customer_id 或 lead_id"
        )

    today = today_business()
    target: dict
    last_followup_at = None
    open_tasks: list[Task] = []
    open_opportunities = 0

    if customer_id is not None:
        from app.modules.customer import service as customer_service

        customer = await customer_service.get_visible_customer(session, user, customer_id)
        last_followup_at = customer.last_followup_at
        # 只算**自己范围内**的待办（§9.1 复审）：原来这里不按负责人过滤，
        # 于是"14 天没跟进"的建议里会混进别人负责的逾期任务。
        open_tasks = await _visible_open_tasks(session, user, customer_id=customer_id) or []
        open_opportunities = int(
            (
                await session.execute(
                    select(func.count(Opportunity.id)).where(
                        Opportunity.customer_id == customer_id,
                        Opportunity.status == "open",
                        Opportunity.deleted_at.is_(None),
                    )
                )
            ).scalar_one()
        )
        target = {"type": "customer", "id": customer.id, "name": customer.name}
    else:
        from app.modules.lead import service as lead_service

        lead = await lead_service.get_visible_lead(session, user, lead_id)
        last_followup_at = lead.last_followup_at
        open_tasks = await _visible_open_tasks(session, user, lead_id=lead_id) or []
        target = {"type": "lead", "id": lead.id, "name": lead.name}

    days_since = None
    if last_followup_at:
        days_since = (datetime.now(UTC) - last_followup_at).days
    overdue_tasks = [t for t in open_tasks if _is_overdue(t.due_at, today)]

    suggestions: list[dict] = []
    if days_since is None:
        suggestions.append(
            {
                "action": "首次联系",
                "reason": "还没有任何跟进记录",
                "priority": "high",
                "suggested_channel": "电话",
            }
        )
    elif days_since >= 30:
        suggestions.append(
            {
                "action": "紧急回访",
                "reason": f"已经 {days_since} 天没有跟进",
                "priority": "high",
                "suggested_channel": "电话",
            }
        )
    elif days_since >= 14:
        suggestions.append(
            {
                "action": "安排回访",
                "reason": f"{days_since} 天没有跟进",
                "priority": "normal",
                "suggested_channel": "微信",
            }
        )
    if overdue_tasks:
        suggestions.append(
            {
                "action": "清理逾期任务",
                "reason": f"有 {len(overdue_tasks)} 个任务已逾期",
                "priority": "high",
                "task_ids": [t.id for t in overdue_tasks],
            }
        )
    if open_opportunities:
        suggestions.append(
            {
                "action": "推进在谈商机",
                "reason": f"有 {open_opportunities} 个商机仍在进行中",
                "priority": "normal",
            }
        )
    if not suggestions:
        suggestions.append(
            {
                "action": "维持常规跟进",
                "reason": "近期跟进正常，没有逾期任务",
                "priority": "low",
            }
        )

    return {
        "target": target,
        "last_followup_at": last_followup_at,
        "days_since_followup": days_since,
        "open_task_count": len(open_tasks),
        "overdue_task_count": len(overdue_tasks),
        "suggestions": suggestions,
        "as_of": today,
    }


# ---------------------------------------------------------------------------
# 商机分析（03-API §37 POST /agent/opportunity-analysis）
# ---------------------------------------------------------------------------

async def opportunity_analysis(session: AsyncSession, user, *, opportunity_id: int) -> dict:
    """商机分析：阶段停留时长、金额、下一步动作、有没有逾期任务。

    "卡了多久"是商机最关键的信号 —— 同一个阶段待太久基本等于要丢单，
    而这是可以从 stage_history 直接算出来的事实。
    """
    from app.modules.opportunity import service as opp_service
    from app.modules.opportunity.model import OpportunityStageHistory

    opportunity = await opp_service.get_visible_opportunity(session, user, opportunity_id)
    today = today_business()

    stage = (
        await session.get(OpportunityStage, opportunity.stage_id)
        if opportunity.stage_id
        else None
    )
    history = (
        await session.execute(
            select(OpportunityStageHistory)
            .where(OpportunityStageHistory.opportunity_id == opportunity_id)
            .order_by(OpportunityStageHistory.id.asc())
        )
    ).scalars().all()

    stage_entered_at = history[-1].entered_at if history else opportunity.created_at
    days_in_stage = (
        (datetime.now(UTC) - stage_entered_at).days if stage_entered_at else None
    )

    # 同 §9.1：只算自己范围内的待办，否则商机分析会泄露别人负责的任务数
    open_tasks = (
        await _visible_open_tasks(session, user, opportunity_id=opportunity_id)
    ) or []
    overdue_tasks = [t for t in open_tasks if _is_overdue(t.due_at, today)]

    items = await _opportunity_items(session, opportunity_id)

    insights: list[str] = []
    level = "normal"
    if days_in_stage is not None and days_in_stage >= 30:
        level = "stalled"
        insights.append(f"在「{stage.name if stage else '当前阶段'}」已经停留 {days_in_stage} 天，可能已经停滞")
    elif days_in_stage is not None and days_in_stage >= 14:
        insights.append(f"在当前阶段停留 {days_in_stage} 天，建议推进")
    if overdue_tasks:
        level = "at_risk" if level != "stalled" else level
        insights.append(f"有 {len(overdue_tasks)} 个任务已逾期")
    if not opportunity.next_action:
        insights.append("没有填写下一步动作，建议补充")
    if not items:
        insights.append("还没有需求明细，无法核价")

    return {
        "opportunity": {
            "id": opportunity.id,
            "title": opportunity.title,
            "customer_id": opportunity.customer_id,
            "status": opportunity.status,
            "stage_id": opportunity.stage_id,
            "stage_name": stage.name if stage else None,
            "expected_amount": _f(opportunity.expected_amount),
            "expected_close_date": opportunity.expected_close_date,
            "risk_level": opportunity.risk_level,
            "next_action": opportunity.next_action,
            "owner_id": opportunity.owner_id,
        },
        "days_in_stage": days_in_stage,
        "stage_entered_at": stage_entered_at,
        "stage_history_count": len(history),
        "item_count": len(items),
        "open_task_count": len(open_tasks),
        "overdue_task_count": len(overdue_tasks),
        "level": level,
        "level_label": {
            "normal": "正常推进",
            "stalled": "已停滞",
            "at_risk": "有风险",
        }[level],
        "insights": insights,
        "as_of": today,
    }


# ---------------------------------------------------------------------------
# 核价分析（03-API §37 POST /agent/pricing-analysis）
# ---------------------------------------------------------------------------

async def pricing_analysis(
    session: AsyncSession, user, *, sku_id: int, quantity, customer_id: int | None = None
) -> dict:
    """核价分析：把核价结果翻译成"能不能成交、底线在哪"的判断。

    不重复实现算价 —— 直接调 `pricing_service.calculate_price`（与
    报价明细、check-permission 同一份），只在其上做解读。
    """
    # 客户专属价挂在客户上：不校验可见性，任何有 agent:use 的人换个 customer_id
    # 就能读到别人客户的协议价。本文件其它入口都走 get_visible_customer，这里此前漏了。
    if customer_id is not None:
        from app.modules.customer import service as customer_service

        await customer_service.get_visible_customer(session, user, customer_id)

    from app.modules.pricing import service as pricing_service
    # 成本与底价只向获授权角色返回（方案 §4.3/§7）。直接复用价格中心那一份脱敏口径，
    # 不在这里写第二份——写第二份迟早会漏字段或漏一句文案。
    # 放在解读之前：下面的 insights 是照着 result 的数字拼出来的，先脱敏，
    # 那些"低于 X 要审批""公司保护价 X"的句子才会因为值为 None 而自然不再出现
    # （否则等于用自然语言把底价念了一遍，脱敏数字字段也没用）。
    from app.modules.pricing.router import _sanitize_pricing_result

    result = await pricing_service.calculate_price(
        session,
        sku_id=sku_id,
        quantity=quantity,
        customer_id=customer_id,
        quoted_price=None,
        role_codes=user.roles,
    )
    result = _sanitize_pricing_result(result, user)

    recommended = result["recommended_price"]
    low = result["recommended_range"][0] if result["recommended_range"] else None
    floor_price = result["minimum_price"]
    protection = result["protection_price"]
    triggers = result["approval_triggers"]

    insights: list[str] = []
    if recommended is None:
        insights.append("缺少成本或价格规则，算不出建议价 —— 先去价格中心补成本")
    else:
        insights.append(f"建议报价 {recommended:.2f} {result['currency']}")
        if low is not None:
            insights.append(f"可让到 {low:.2f} 仍在你角色的授权区间内")
        if floor_price is not None:
            insights.append(f"低于 {floor_price:.2f} 需要审批")
        if protection is not None:
            insights.append(f"公司保护价 {protection:.2f}，任何情况下不应低于它")
        rate = result["profit_rate"]
        if rate is not None and rate < 0:
            insights.append("按建议价算是负利润，必须重新核价")
        # D7：绝对底价不是"需审批"而是"不可批"，要说清后果与出路
        if triggers.get("below_hard_floor"):
            insights.append(
                "低于公司绝对底价：提交会被直接拒绝，任何审批都无法通过——"
                "联系价格管理员调整价格档位，或走样品/清库存特殊通道"
            )

    return {
        "sku": result["sku"],
        "quantity": result["quantity"],
        "customer_id": result["customer_id"],
        "currency": result["currency"],
        "cost": result["cost"],
        "standard_price": result["standard_price"],
        "recommended_price": recommended,
        "recommended_range": result["recommended_range"],
        "minimum_price": floor_price,
        "protection_price": protection,
        "profit": result["profit"],
        "profit_rate": result["profit_rate"],
        "approval_required": result["approval_required"],
        "below_hard_floor": triggers.get("below_hard_floor", False),
        "insights": insights,
    }


# ---------------------------------------------------------------------------
# 报价草稿（03-API §37 POST /agent/quote-draft）
# ---------------------------------------------------------------------------

async def quote_draft(
    session: AsyncSession, user, *, opportunity_id: int, customer_id: int | None = None
) -> dict:
    """报价草稿建议：**只给建议，不落库**。

    真正的建单走 `POST /quotes`（或 Agent 的 `create_quote_draft` 工具，
    那条路要用户确认）。这个接口是"先看看会生成什么" ——
    如果它直接建单，用户每点一次就多一张草稿，反而要手工清理。
    """
    from app.modules.opportunity import service as opp_service
    from app.modules.pricing import service as pricing_service

    opportunity = await opp_service.get_visible_opportunity(session, user, opportunity_id)
    resolved_customer = customer_id or opportunity.customer_id
    if resolved_customer:
        from app.modules.customer import service as customer_service

        await customer_service.get_visible_customer(session, user, resolved_customer)

    items = await _opportunity_items(session, opportunity_id)
    lines: list[dict] = []
    warnings: list[str] = []

    for item in items:
        if item.sku_id is None:
            warnings.append(f"需求「{item.sku_id or '未指定 SKU'}」没有对应 SKU，跳过")
            continue
        result = await pricing_service.calculate_price(
            session,
            sku_id=item.sku_id,
            quantity=item.quantity or 1,
            customer_id=resolved_customer,
            quoted_price=None,
            role_codes=user.roles,
        )
        price = result["recommended_price"]
        # quantity 是 Decimal，recommended_price 经 _f 变成了 float ——
        # 直接相乘会 TypeError，先统一成 Decimal 再算钱。
        quantity = item.quantity or Decimal(0)
        unit_price = Decimal(str(price)) if price is not None else Decimal(0)
        lines.append(
            {
                "opportunity_item_id": item.id,
                "sku_id": item.sku_id,
                "sku": result["sku"],
                "quantity": _f(quantity),
                "suggested_price": price,
                "standard_price": result["standard_price"],
                "minimum_price": result["minimum_price"],
                "protection_price": result["protection_price"],
                "amount": _f((quantity * unit_price).quantize(Decimal("0.01"))),
                "approval_required": result["approval_required"],
            }
        )

    total = sum((line["amount"] or 0) for line in lines)
    if not lines:
        warnings.append("这张商机还没有可报价的明细")

    return {
        "opportunity": {
            "id": opportunity.id,
            "title": opportunity.title,
            "customer_id": resolved_customer,
        },
        "items": lines,
        "item_count": len(lines),
        "suggested_total": round(total, 2),
        "any_requires_approval": any(line["approval_required"] for line in lines),
        "warnings": warnings,
        # 这句是给界面看的操作提示，业务员会看到它（也会被模型复述），
        # 所以**不能出现接口路径**。2026-10-05 修过一次：原来写的是
        # "确认后用 POST /quotes 生成正式报价单"，于是 AI 叙述的末尾就冒出了这句
        # 接口路径给业务员看。回归套件断言里查的是"未落库"三个字，保留它。
        "note": "这只是草稿建议，未落库；确认无误后可直接生成正式报价单。",
    }


# ---------------------------------------------------------------------------
# 产品推荐（03-API §37 POST /agent/product-recommendation）
# ---------------------------------------------------------------------------

async def product_recommendation(
    session: AsyncSession,
    user,
    *,
    customer_id: int | None = None,
    opportunity_id: int | None = None,
    limit: int = 5,
) -> dict:
    """产品推荐（03-API §37）：直接复用商机模块的推荐实现。

    **不自己再写一份**：`opp_service.recommend_products` 已经实现了
    "该客户历史成交过的 SKU 优先、其余按公司整体成交频次补足，每条带理由"，
    而且商机的 `/opportunities/{id}/recommend-products` 也在用它。
    两处各写一份，早晚出现"商机页推 A、Agent 推 B"的分裂。

    `customer_id` 单独给（不带商机）时，用一个临时商机对象复用同一实现 ——
    推荐逻辑只用到 `opportunity.customer_id` 这一个字段。
    """
    from app.modules.opportunity import service as opp_service

    if customer_id is None and opportunity_id is None:
        raise AppError(
            ErrorCode.REQUIRED_FIELD_MISSING, "至少要提供 customer_id 或 opportunity_id"
        )

    if opportunity_id is not None:
        opportunity = await opp_service.get_visible_opportunity(
            session, user, opportunity_id
        )
        if customer_id is not None and opportunity.customer_id != customer_id:
            raise AppError(
                ErrorCode.PARAM_ERROR,
                "客户与商机不匹配：该商机不属于这个客户",
            )
    else:
        from app.modules.customer import service as customer_service

        customer = await customer_service.get_visible_customer(
            session, user, customer_id
        )
        # 推荐实现只读 customer_id，构造一个轻量对象即可，不必真建商机
        opportunity = Opportunity(customer_id=customer.id, title="", stage_id=0)

    rows = await opp_service.recommend_products(
        session, opportunity=opportunity, limit=limit
    )
    return {
        "customer_id": opportunity.customer_id,
        "opportunity_id": opportunity_id,
        "recommendations": rows,
        "count": len(rows),
        "note": (
            "推荐依据：该客户历史成交记录优先，不足时按公司整体成交频次补足；"
            "每条都带推荐理由。没有历史数据时返回空列表，不编造推荐。"
        ),
    }
