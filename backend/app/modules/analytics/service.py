"""聚合统计。

两条原则：
1. 工作台与分析页不新建业务表，全部实时聚合现有数据，避免"两份真相"；
2. 能用数据库聚合函数就不用 Python 循环求和。

例外：按月分组的趋势统计**故意**在 Python 里分组。
原因见 `order_payment_trend` 的注释：按月份格式化日期是方言相关的
（PostgreSQL 用 to_char、MySQL 用 DATE_FORMAT），用方言函数会让
「改 MySQL 只换连接串」的承诺（core/config.py）失效。
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import Select, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.data_scope import scoped_owner_ids
from app.core.deps import CurrentUser
from app.modules.approval.model import ApprovalInstance
from app.modules.customer.model import Customer
from app.modules.followup.model import FollowUp
from app.modules.lead.model import Lead
from app.modules.lead.service import STATUS_LABEL as LEAD_STATUS_LABEL
from app.modules.opportunity.model import LossReason, Opportunity, OpportunityItem, OpportunityStage
from app.modules.opportunity.model import OpportunityStageHistory
from app.modules.order.model import SalesOrder
from app.modules.payment.model import PLAN_STATUS_LABEL as PLAN_LABEL
from app.modules.payment.model import PaymentRecord, ReceivablePlan
from app.modules.product.model import Product, Sku
from app.modules.quote.model import Quote, QuoteItem, QuoteVersion
from app.modules.settings import service as settings_service
from app.modules.task.model import Task
from app.modules.user.model import User

ZERO = Decimal(0)


def _f(value) -> float:
    return float(value or 0)


def _month_key(value) -> str | None:
    """把一个日期类值折成 `YYYY-MM`，跨方言通用。"""
    if value is None:
        return None
    return f"{value.year:04d}-{value.month:02d}"


async def _scope_filter(
    stmt: Select, user: CurrentUser, column, session: AsyncSession
) -> Select:
    """按数据范围过滤（统一走 app/core/data_scope.py）。

    `department_and_sub` 会递归到下级部门。
    """
    owner_ids = await scoped_owner_ids(session, user)
    if owner_ids is None:
        return stmt
    return stmt.where(column.in_(owner_ids))


async def dashboard_summary(session: AsyncSession, user: CurrentUser) -> dict:
    now = datetime.now(UTC)
    month_start = now.date().replace(day=1)

    todo_count = (
        await session.execute(
            await _scope_filter(
                select(func.count(Task.id)).where(
                    Task.status.in_(["pending", "doing"]), Task.owner_id.is_not(None)
                ),
                user,
                Task.owner_id,
                session,
            )
        )
    ).scalar_one()
    overdue_count = (
        await session.execute(
            await _scope_filter(
                select(func.count(Task.id)).where(
                    Task.due_at < now, Task.status.in_(["pending", "doing"])
                ),
                user,
                Task.owner_id,
                session,
            )
        )
    ).scalar_one()

    # 待跟进客户：30 天内没跟过，或从未跟进
    from app.modules.settings import service as settings_service

    stale_date = now - timedelta(
        days=int(await settings_service.get_number(session, "customer_stale_days", "days", 30))
    )
    stale_customers = (
        await session.execute(
            await _scope_filter(
                select(func.count(Customer.id)).where(
                    Customer.deleted_at.is_(None),
                    Customer.pool_status == "private",
                    or_(
                        Customer.last_followup_at.is_(None),
                        Customer.last_followup_at < stale_date,
                    ),
                ),
                user,
                Customer.owner_id,
                session,
            )
        )
    ).scalar_one()

    open_count, open_amount = (
        await session.execute(
            await _scope_filter(
                select(
                    func.count(Opportunity.id),
                    func.coalesce(func.sum(Opportunity.expected_amount), 0),
                ).where(Opportunity.deleted_at.is_(None), Opportunity.status == "open"),
                user,
                Opportunity.owner_id,
                session,
            )
        )
    ).one()

    pending_approval = (
        await session.execute(
            select(func.count(ApprovalInstance.id)).where(ApprovalInstance.status == "pending")
        )
    ).scalar_one()

    month_won_amount = (
        await session.execute(
            await _scope_filter(
                select(func.coalesce(func.sum(SalesOrder.total_amount), 0)).where(
                    SalesOrder.status != "cancelled", SalesOrder.created_at >= month_start
                ),
                user,
                SalesOrder.owner_id,
                session,
            )
        )
    ).scalar_one()

    # 待回款 = 未结清节点的（应收金额 − 财务已确认回款）
    paid_sub = (
        select(
            PaymentRecord.receivable_plan_id.label("plan_id"),
            func.coalesce(func.sum(PaymentRecord.received_amount), 0).label("paid"),
        )
        .where(PaymentRecord.status == "confirmed")
        .group_by(PaymentRecord.receivable_plan_id)
        .subquery()
    )
    pending_receivable = (
        await session.execute(
            await _scope_filter(
                select(
                    func.coalesce(
                        func.sum(ReceivablePlan.amount - func.coalesce(paid_sub.c.paid, 0)), 0
                    )
                )
                .select_from(ReceivablePlan)
                .join(SalesOrder, SalesOrder.id == ReceivablePlan.order_id)
                .outerjoin(paid_sub, paid_sub.c.plan_id == ReceivablePlan.id)
                .where(ReceivablePlan.status != "paid"),
                user,
                SalesOrder.owner_id,
                session,
            )
        )
    ).scalar_one()

    # 本月回款（财务已确认）
    month_received = (
        await session.execute(
            await _scope_filter(
                select(func.coalesce(func.sum(PaymentRecord.received_amount), 0))
                .select_from(PaymentRecord)
                .join(SalesOrder, SalesOrder.id == PaymentRecord.order_id)
                .where(PaymentRecord.status == "confirmed", PaymentRecord.received_date >= month_start),
                user,
                SalesOrder.owner_id,
                session,
            )
        )
    ).scalar_one()

    overdue_receivable = (
        await session.execute(
            select(func.count(ReceivablePlan.id)).where(ReceivablePlan.status == "overdue")
        )
    ).scalar_one()

    open_leads = (
        await session.execute(
            await _scope_filter(
                select(func.count(Lead.id)).where(
                    Lead.deleted_at.is_(None),
                    Lead.status.in_(["pending", "assigned", "following"]),
                ),
                user,
                Lead.owner_id,
                session,
            )
        )
    ).scalar_one()

    return {
        "todo_count": int(todo_count),
        "overdue_task_count": int(overdue_count),
        "stale_customer_count": int(stale_customers),
        "open_opportunity_count": int(open_count),
        "open_opportunity_amount": _f(open_amount),
        "pending_approval_count": int(pending_approval),
        "month_won_amount": _f(month_won_amount),
        "month_received_amount": _f(month_received),
        "pending_receivable_amount": _f(pending_receivable),
        "overdue_receivable_count": int(overdue_receivable),
        "open_lead_count": int(open_leads),
    }


async def order_payment_trend(
    session: AsyncSession, user: CurrentUser, months: int = 6
) -> list[dict]:
    """近 N 个月的订单金额与回款金额，用于工作台趋势图。"""
    months = max(1, min(months, 12))
    now = datetime.now(UTC)
    # 生成月份序列（含本月）
    series: list[str] = []
    year, month = now.year, now.month
    for _ in range(months):
        series.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    series.reverse()
    start = datetime.fromisoformat(f"{series[0]}-01T00:00:00+00:00")

    order_rows = (
        await session.execute(
            await _scope_filter(
                select(SalesOrder.created_at, SalesOrder.total_amount).where(
                    SalesOrder.status != "cancelled", SalesOrder.created_at >= start
                ),
                user,
                SalesOrder.owner_id,
                session,
            )
        )
    ).all()
    payment_rows = (
        await session.execute(
            await _scope_filter(
                select(PaymentRecord.received_date, PaymentRecord.received_amount)
                .select_from(PaymentRecord)
                .join(SalesOrder, SalesOrder.id == PaymentRecord.order_id)
                .where(
                    PaymentRecord.status == "confirmed",
                    PaymentRecord.received_date >= start.date(),
                ),
                user,
                SalesOrder.owner_id,
                session,
            )
        )
    ).all()

    # 在 Python 里按月累加。数据量是「近 N 个月的订单/回款条数」，
    # 比引入方言相关的日期格式化函数更划算（见模块 docstring 的例外说明）。
    orders: dict[str, float] = {}
    for created_at, amount in order_rows:
        key = _month_key(created_at)
        if key:
            orders[key] = orders.get(key, 0.0) + _f(amount)
    payments: dict[str, float] = {}
    for received_date, amount in payment_rows:
        key = _month_key(received_date)
        if key:
            payments[key] = payments.get(key, 0.0) + _f(amount)

    return [
        {
            "month": m,
            "label": f"{int(m[5:7])}月",
            "order_amount": round(orders.get(m, 0.0), 2),
            "received_amount": round(payments.get(m, 0.0), 2),
        }
        for m in series
    ]


async def recent_activities(
    session: AsyncSession, user: CurrentUser, limit: int = 8
) -> list[dict]:
    """团队与业务动态：取最近的操作记录，转成人话。

    必须按数据范围过滤：动态流暴露的是"谁动了什么"的全局审计，
    不加过滤的话 `self` 范围的业务员也能看到全公司（含管理员）的操作。
    口径与列表一致——只展示**数据范围内的人**做出的操作。
    """
    from app.core.audit import AuditLog
    from app.core.data_scope import scoped_owner_ids

    conditions = [
        AuditLog.business_type.is_not(None),
        AuditLog.action.not_in(["login"]),
    ]
    owner_ids = await scoped_owner_ids(session, user)
    if owner_ids is not None:
        conditions.append(AuditLog.operator_id.in_(owner_ids))

    rows = (
        await session.execute(
            select(AuditLog, User.name)
            .outerjoin(User, User.id == AuditLog.operator_id)
            .where(*conditions)
            .order_by(AuditLog.id.desc())
            .limit(limit)
        )
    ).all()
    label = {
        "customer": "客户",
        "contact": "联系人",
        "lead": "线索",
        "opportunity": "商机",
        "quote": "报价单",
        "order": "订单",
        "task": "任务",
        "followup": "跟进",
        "payment": "回款",
        "product": "产品",
        "sku": "SKU",
        "file": "文件",
        "price_rule": "价格规则",
        "product_cost": "成本",
        "customer_price_rule": "客户特殊价",
        "price_permission": "价格权限",
        "setting": "系统配置",
        "logistics_rate": "运费费率",
        # 新增的业务对象（后续补的模块）也要有中文名，否则活动流会显示英文类型
        "receivable_plan": "应收计划",
        "tag": "客户标签",
        "sample": "样品",
        "sample_item": "样品明细",
        "user": "用户",
        "department": "部门",
        "role": "角色",
        "agent_session": "AI 会话",
        "quote_charge": "报价附加费用",
        "quote_item": "报价明细",
        "logistics_quote": "物流试算",
        "auth": "登录",
    }
    action_label = {
        "create": "新建",
        "update": "更新",
        "delete": "删除",
        "create_version": "新建版本",
        "change_stage": "推进阶段",
        "change_status": "更新状态",
        "create_item": "添加需求明细",
        "update_item": "修改需求明细",
        "delete_item": "删除需求明细",
        "update_version": "修改版本",
        "set_items": "保存报价明细",
        "release_to_pool": "放入公海",
        "claim": "领取",
        "win": "标记成交",
        "lose": "标记失单",
        "send": "发送",
        "accept": "客户接受",
        "submit_approval": "提交审批",
        "approve": "审批通过",
        "reject": "审批拒绝",
        "confirm": "确认回款",
        "transfer": "转移负责人",
        "convert": "线索转化",
        "upload": "上传",
    }
    return [
        {
            "id": row.id,
            "operator": name or "系统",
            "title": f"{action_label.get(row.action, row.action)}{label.get(row.business_type or '', '记录')}",
            "business_type": row.business_type,
            "business_id": row.business_id,
            "source": row.source,
            "at": row.created_at,
        }
        for row, name in rows
    ]


async def my_tasks(session: AsyncSession, user: CurrentUser, limit: int = 10) -> list[dict]:
    now = datetime.now(UTC)
    rows = (
        await session.execute(
            select(Task)
            .where(Task.owner_id == user.id, Task.status.in_(["pending", "doing"]))
            # 用 `NULLS LAST` 的可移植写法：先按"有没有到期日"排，再按到期日排。
            # 直接调 .nullslast() 是 PostgreSQL 专有，换库即 500。
            .order_by(Task.due_at.is_(None).asc(), Task.due_at.asc(), Task.id.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [
        {
            "id": task.id,
            "title": task.title,
            "priority": task.priority,
            "due_at": task.due_at,
            "opportunity_id": task.opportunity_id,
            "customer_id": task.customer_id,
            "lead_id": task.lead_id,
            "overdue": bool(
                task.due_at
                and (task.due_at if task.due_at.tzinfo else task.due_at.replace(tzinfo=UTC)) < now
            ),
        }
        for task in rows
    ]


async def risk_opportunities(
    session: AsyncSession, user: CurrentUser, limit: int = 10
) -> list[dict]:
    """风险商机：预计成交日临近，或超过 14 天没更新。"""
    today = datetime.now(UTC).date()
    from app.modules.settings import service as settings_service

    soon = today + timedelta(
        days=int(
            await settings_service.get_number(session, "opportunity_risk_days", "days", 7)
        )
    )
    stale = datetime.now(UTC) - timedelta(
        days=int(
            await settings_service.get_number(session, "opportunity_stale_days", "days", 14)
        )
    )
    stmt = await _scope_filter(
        select(Opportunity, OpportunityStage.name, Customer.name)
        .join(OpportunityStage, OpportunityStage.id == Opportunity.stage_id)
        .join(Customer, Customer.id == Opportunity.customer_id)
        .where(
            Opportunity.deleted_at.is_(None),
            Opportunity.status == "open",
            or_(
                Opportunity.expected_close_date <= soon,
                Opportunity.updated_at < stale,
            ),
        )
        .order_by(
            Opportunity.expected_close_date.is_(None).asc(),
            Opportunity.expected_close_date.asc(),
            Opportunity.id.desc(),
        )
        .limit(limit),
        user,
        Opportunity.owner_id,
        session,
    )
    rows = (await session.execute(stmt)).all()
    return [
        {
            "id": opp.id,
            "title": opp.title,
            "customer_name": customer_name,
            "stage_name": stage_name,
            "expected_amount": _f(opp.expected_amount),
            "expected_close_date": opp.expected_close_date,
            "days_left": (opp.expected_close_date - today).days
            if opp.expected_close_date
            else None,
            "stale": bool(opp.updated_at and opp.updated_at < stale),
            "risk_reason": (
                "预计成交日临近"
                if opp.expected_close_date and opp.expected_close_date <= soon
                else "超过 14 天没有更新"
            ),
        }
        for opp, stage_name, customer_name in rows
    ]


async def funnel(session: AsyncSession, user: CurrentUser) -> list[dict]:
    stages = (
        await session.execute(select(OpportunityStage).order_by(OpportunityStage.sequence.asc()))
    ).scalars().all()
    stmt = await _scope_filter(
        select(
            Opportunity.stage_id,
            func.count(Opportunity.id),
            func.coalesce(func.sum(Opportunity.expected_amount), 0),
        )
        .where(Opportunity.deleted_at.is_(None), Opportunity.status == "open")
        .group_by(Opportunity.stage_id),
        user,
        Opportunity.owner_id,
        session,
    )
    rows = (await session.execute(stmt)).all()
    stats = {int(sid): (int(count), _f(amount)) for sid, count, amount in rows}
    return [
        {
            "stage_id": stage.id,
            "stage_name": stage.name,
            "sequence": stage.sequence,
            "count": stats.get(stage.id, (0, 0.0))[0],
            "amount": stats.get(stage.id, (0, 0.0))[1],
        }
        for stage in stages
    ]


async def opportunity_stats(session: AsyncSession, user: CurrentUser) -> dict:
    won_count = (
        await session.execute(
            await _scope_filter(
                select(func.count(Opportunity.id)).where(
                    Opportunity.status == "win", Opportunity.deleted_at.is_(None)
                ),
                user,
                Opportunity.owner_id,
                session,
            )
        )
    ).scalar_one()
    loss_count = (
        await session.execute(
            await _scope_filter(
                select(func.count(Opportunity.id)).where(
                    Opportunity.status == "loss", Opportunity.deleted_at.is_(None)
                ),
                user,
                Opportunity.owner_id,
                session,
            )
        )
    ).scalar_one()
    settled = int(won_count) + int(loss_count)

    return {
        "won_count": int(won_count),
        "loss_count": int(loss_count),
        "win_rate": round(int(won_count) / settled, 4) if settled else 0,
        "loss_rate": round(int(loss_count) / settled, 4) if settled else 0,
        "funnel": await funnel(session, user),
        "stage_conversion": await stage_conversion(session, user),
        "cycle": await opportunity_cycle(session, user),
    }


async def stage_conversion(session: AsyncSession, user: CurrentUser) -> list[dict]:
    """PRD §23「商机：阶段转化」。

    口径：每个阶段有多少商机**到达过**（用阶段历史去重），
    相邻两级的到达数之比就是转化率。比"当前停在该阶段的数量"更准，
    因为推进过的商机不会停留在中间阶段。
    """
    stages = (
        await session.execute(
            select(OpportunityStage).order_by(OpportunityStage.sequence.asc())
        )
    ).scalars().all()

    stmt = (
        select(OpportunityStageHistory.to_stage_id, OpportunityStageHistory.opportunity_id)
        .join(Opportunity, Opportunity.id == OpportunityStageHistory.opportunity_id)
        .where(Opportunity.deleted_at.is_(None))
        .distinct()
    )
    rows = (
        await session.execute(
            await _scope_filter(stmt, user, Opportunity.owner_id, session)
        )
    ).all()

    reached: dict[int, set[int]] = {}
    for stage_id, opportunity_id in rows:
        reached.setdefault(stage_id, set()).add(opportunity_id)

    result: list[dict] = []
    previous: int | None = None
    for stage in stages:
        count = len(reached.get(stage.id, set()))
        conversion = None
        if previous is not None and previous > 0:
            conversion = round(count / previous, 4)
        result.append(
            {
                "stage_id": stage.id,
                "stage_name": stage.name,
                "sequence": stage.sequence,
                "reached_count": count,
                "conversion_from_previous": conversion,
            }
        )
        previous = count
    return result


async def opportunity_cycle(session: AsyncSession, user: CurrentUser) -> dict:
    """PRD §23「商机：周期」。

    成交周期 = 从首次进入阶段历史 到 成交 的天数。
    `opportunity_stage_history.duration_seconds` 记录的是在某一阶段的停留时长，
    这里需要的是整单时长，所以取「最早 entered_at → 最新 entered_at」。
    """
    stmt = (
        select(
            OpportunityStageHistory.opportunity_id,
            func.min(OpportunityStageHistory.entered_at),
            func.max(OpportunityStageHistory.entered_at),
        )
        .join(Opportunity, Opportunity.id == OpportunityStageHistory.opportunity_id)
        .where(Opportunity.deleted_at.is_(None), Opportunity.status == "win")
        .group_by(OpportunityStageHistory.opportunity_id)
    )
    rows = (
        await session.execute(
            await _scope_filter(stmt, user, Opportunity.owner_id, session)
        )
    ).all()

    durations: list[float] = []
    for _, first_at, last_at in rows:
        if first_at is None or last_at is None:
            continue
        days = (last_at - first_at).total_seconds() / 86400
        durations.append(days)

    if not durations:
        return {"won_with_history": 0, "average_days": None, "min_days": None, "max_days": None}

    durations.sort()
    mid = len(durations) // 2
    median = (
        durations[mid]
        if len(durations) % 2
        else (durations[mid - 1] + durations[mid]) / 2
    )
    return {
        "won_with_history": len(durations),
        "average_days": round(sum(durations) / len(durations), 1),
        "median_days": round(median, 1),
        "min_days": round(durations[0], 1),
        "max_days": round(durations[-1], 1),
    }


async def quote_stats(session: AsyncSession, user: CurrentUser) -> dict:
    quotes = (
        await session.execute(await _scope_filter(select(Quote), user, Quote.owner_id, session))
    ).scalars().all()
    quote_ids = [quote.id for quote in quotes]
    if not quote_ids:
        return {
            "quote_count": 0,
            "average_versions": 0,
            "average_discount": 0,
            "accept_rate": 0,
            "approval_rate": 0,
            "accepted_count": 0,
            "approval_required_count": 0,
        }

    version_rows = (
        await session.execute(
            select(
                QuoteVersion.quote_id,
                func.count(QuoteVersion.id),
                func.sum(case((QuoteVersion.approval_required.is_(True), 1), else_=0)),
            )
            .where(QuoteVersion.quote_id.in_(quote_ids))
            .group_by(QuoteVersion.quote_id)
        )
    ).all()
    total_versions = sum(int(count) for _, count, _ in version_rows)
    approval_required = sum(int(flag or 0) for _, _, flag in version_rows)

    average_discount = (
        await session.execute(
            select(
                func.avg(
                    case(
                        (
                            QuoteItem.recommended_price_snapshot > 0,
                            (QuoteItem.recommended_price_snapshot - QuoteItem.quoted_price)
                            / QuoteItem.recommended_price_snapshot,
                        ),
                        else_=None,
                    )
                )
            )
            .join(QuoteVersion, QuoteVersion.id == QuoteItem.quote_version_id)
            .where(QuoteVersion.quote_id.in_(quote_ids))
        )
    ).scalar_one()

    accepted = len([quote for quote in quotes if quote.status == "accepted"])
    sent = len([quote for quote in quotes if quote.status in ("sent", "accepted", "declined")])
    return {
        "quote_count": len(quotes),
        "average_versions": round(total_versions / len(quotes), 2),
        "average_discount": round(_f(average_discount), 4),
        "accept_rate": round(accepted / sent, 4) if sent else 0,
        "approval_rate": round(approval_required / total_versions, 4) if total_versions else 0,
        "accepted_count": accepted,
        "approval_required_count": approval_required,
    }


async def customer_stats(session: AsyncSession, user: CurrentUser) -> dict:
    customers = (
        await session.execute(
            await _scope_filter(
                select(Customer).where(Customer.deleted_at.is_(None)),
                user,
                Customer.owner_id,
                session,
            )
        )
    ).scalars().all()
    by_source: dict[str, int] = {}
    by_level: dict[str, int] = {}
    for customer in customers:
        source = customer.source or "未填写"
        level = customer.level or "未分级"
        by_source[source] = by_source.get(source, 0) + 1
        by_level[level] = by_level.get(level, 0) + 1

    now = datetime.now(UTC)
    month_start = now.date().replace(day=1)
    new_this_month = len(
        [c for c in customers if c.created_at and c.created_at.date() >= month_start]
    )

    # PRD §23「客户：活跃 / 沉睡」：口径由系统配置决定，不写死天数。
    #   活跃 = 最近 N 天内有跟进；沉睡 = 超过 M 天没有跟进（且不是公海）
    active_days = int(await settings_service.get_number(session, "customer_active_days", "days", 30))
    dormant_days = int(await settings_service.get_number(session, "customer_stale_days", "days", 30))
    active_cutoff = now - timedelta(days=active_days)
    dormant_cutoff = now - timedelta(days=dormant_days)

    active_count = 0
    dormant_count = 0
    for customer in customers:
        last = customer.last_followup_at
        if last is None:
            # 从没跟进过的客户按"创建时间"判断，否则刚建的客户会被直接算成沉睡
            last = customer.created_at
        if last is None:
            continue
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        if last >= active_cutoff:
            active_count += 1
        elif last < dormant_cutoff:
            dormant_count += 1

    # PRD §23「客户：复购」：同一个客户有 2 张及以上非取消订单 = 发生过复购
    repeat_customer_ids = set(
        (
            await session.execute(
                await _scope_filter(
                    select(SalesOrder.customer_id)
                    .where(SalesOrder.status != "cancelled", SalesOrder.customer_id.is_not(None))
                    .group_by(SalesOrder.customer_id)
                    .having(func.count(SalesOrder.id) >= 2),
                    user,
                    SalesOrder.owner_id,
                    session,
                )
            )
        ).scalars().all()
    )

    return {
        "total": len(customers),
        "new_this_month": new_this_month,
        "active_count": active_count,
        "dormant_count": dormant_count,
        "repeat_customer_count": len(repeat_customer_ids),
        "active_days": active_days,
        "dormant_days": dormant_days,
        "by_source": [
            {"name": k, "value": v} for k, v in sorted(by_source.items(), key=lambda x: -x[1])
        ],
        "by_level": [{"name": k, "value": v} for k, v in sorted(by_level.items())],
    }


async def product_stats(session: AsyncSession, user: CurrentUser, limit: int = 10) -> list[dict]:
    """PRD §23「产品：询盘 / 报价 / 成交 / 失单 / 利润」。

    口径说明（写在代码里，避免以后各算各的）：
    - 询盘：该 SKU 出现在多少条**商机需求明细**里（客户问过这个产品）
    - 报价：该 SKU 出现在多少条**报价明细**里
    - 成交：该 SKU 出现在多少张**已成交商机**的需求明细里
    - 失单：该 SKU 出现在多少张**已失单商机**的需求明细里
    - 利润：该 SKU 在报价明细上的 `profit_snapshot × quantity` 累计（快照口径，历史不受改价影响）
    """
    # 询盘 / 成交 / 失单：都从商机需求明细出发，按商机状态分类
    inquiry_stmt = (
        select(OpportunityItem.sku_id, Opportunity.status, func.count(OpportunityItem.id))
        .join(Opportunity, Opportunity.id == OpportunityItem.opportunity_id)
        .where(Opportunity.deleted_at.is_(None))
        .group_by(OpportunityItem.sku_id, Opportunity.status)
    )
    inquiry_rows = (
        await session.execute(
            await _scope_filter(inquiry_stmt, user, Opportunity.owner_id, session)
        )
    ).all()

    inquiry: dict[int, int] = {}
    won: dict[int, int] = {}
    lost: dict[int, int] = {}
    for sku_id, status, count in inquiry_rows:
        inquiry[sku_id] = inquiry.get(sku_id, 0) + int(count)
        if status == "win":
            won[sku_id] = won.get(sku_id, 0) + int(count)
        elif status == "loss":
            lost[sku_id] = lost.get(sku_id, 0) + int(count)

    # 报价次数 / 报价数量 / 利润快照：从报价明细出发，按报价所属人过滤
    quote_stmt = (
        select(
            QuoteItem.sku_id,
            func.count(QuoteItem.id),
            func.coalesce(func.sum(QuoteItem.quantity), 0),
            func.coalesce(func.sum(QuoteItem.profit_snapshot * QuoteItem.quantity), 0),
        )
        .join(QuoteVersion, QuoteVersion.id == QuoteItem.quote_version_id)
        .join(Quote, Quote.id == QuoteVersion.quote_id)
        .group_by(QuoteItem.sku_id)
    )
    quote_rows = (
        await session.execute(await _scope_filter(quote_stmt, user, Quote.owner_id, session))
    ).all()
    quoted = {
        sku_id: (int(count), _f(quantity), _f(profit))
        for sku_id, count, quantity, profit in quote_rows
    }

    sku_ids = set(inquiry) | set(quoted)
    if not sku_ids:
        return []

    meta_rows = (
        await session.execute(
            select(Sku.id, Sku.sku_code, Sku.specification, Product.name)
            .join(Product, Product.id == Sku.product_id)
            .where(Sku.id.in_(sku_ids))
        )
    ).all()
    meta = {row[0]: row[1:] for row in meta_rows}

    items = []
    for sku_id in sku_ids:
        code, spec, product_name = meta.get(sku_id, (None, None, None))
        count, quantity, profit = quoted.get(sku_id, (0, 0.0, 0.0))
        items.append(
            {
                "sku_id": sku_id,
                "sku_code": code,
                "specification": spec,
                "product_name": product_name,
                "inquiry_times": inquiry.get(sku_id, 0),
                "quote_times": count,
                "quote_quantity": quantity,
                "won_times": won.get(sku_id, 0),
                "lost_times": lost.get(sku_id, 0),
                "profit_amount": round(profit, 2),
            }
        )
    items.sort(key=lambda x: (-x["quote_times"], -x["inquiry_times"]))
    return items[:limit]


async def sales_user_stats(
    session: AsyncSession, user: CurrentUser, limit: int = 20
) -> list[dict]:
    """PRD §23「人员：客户数 / 跟进数 / 商机数 / 报价数 / 成交额 / 回款额」。

    可见范围：管理员/财务看到全部在岗人员；其余角色只看得到数据范围内的
    负责人（原实现无过滤，业务员能看到全公司的成交额与回款额）。
    """
    stmt = select(
        User.id,
        User.name,
        select(func.count(Customer.id))
        .where(Customer.owner_id == User.id, Customer.deleted_at.is_(None))
        .scalar_subquery(),
        select(func.count(Opportunity.id))
        .where(Opportunity.owner_id == User.id, Opportunity.deleted_at.is_(None))
        .scalar_subquery(),
        select(func.count(Quote.id)).where(Quote.owner_id == User.id).scalar_subquery(),
        select(func.coalesce(func.sum(SalesOrder.total_amount), 0))
        .where(SalesOrder.owner_id == User.id, SalesOrder.status != "cancelled")
        .scalar_subquery(),
        # 跟进数
        select(func.count(FollowUp.id))
        .where(FollowUp.owner_id == User.id)
        .scalar_subquery(),
        # 回款额（业绩口径，方案 §3.8）：只算财务已确认的，且按**订单负责人**归属——
        # "谁的单，回款就算谁的业绩"。此前按 confirmed_by（财务确认人）聚合，
        # 结果是财务成了收钱最多的人、销售回款业绩全零，还会被直接当成
        # 业务员目标完成额（文档点名"不能直接用作业务员目标完成额"）。
        # 财务自己确认了多少，在「回款中心」按确认人单独看得到，不受影响。
        select(func.coalesce(func.sum(PaymentRecord.received_amount), 0))
        .join(SalesOrder, SalesOrder.id == PaymentRecord.order_id)
        .where(PaymentRecord.status == "confirmed", SalesOrder.owner_id == User.id)
        .scalar_subquery(),
    ).where(User.status == "active")

    owner_ids = await scoped_owner_ids(session, user)
    if owner_ids is not None:
        stmt = stmt.where(User.id.in_(owner_ids))

    rows = (await session.execute(stmt.order_by(User.id.asc()).limit(limit))).all()
    return [
        {
            "user_id": uid,
            "name": name,
            "customer_count": int(customers),
            "opportunity_count": int(opportunities),
            "quote_count": int(quotes),
            "order_amount": _f(order_amount),
            "followup_count": int(followups),
            "received_amount": _f(received),
        }
        for uid, name, customers, opportunities, quotes, order_amount, followups, received in rows
    ]


async def receivable_stats(session: AsyncSession, user: CurrentUser) -> dict:
    """应收与回款汇总。按订单负责人做数据范围过滤（原实现返回全员数据）。"""
    plans = (
        await session.execute(
            await _scope_filter(
                select(ReceivablePlan)
                .join(SalesOrder, SalesOrder.id == ReceivablePlan.order_id)
                .where(SalesOrder.status != "cancelled"),
                user,
                SalesOrder.owner_id,
                session,
            )
        )
    ).scalars().all()
    received = (
        await session.execute(
            await _scope_filter(
                select(func.coalesce(func.sum(PaymentRecord.received_amount), 0))
                .select_from(PaymentRecord)
                .join(SalesOrder, SalesOrder.id == PaymentRecord.order_id)
                .where(PaymentRecord.status == "confirmed"),
                user,
                SalesOrder.owner_id,
                session,
            )
        )
    ).scalar_one()
    plan_amount = sum((plan.amount for plan in plans), ZERO)
    by_status: dict[str, int] = {}
    for plan in plans:
        by_status[plan.status] = by_status.get(plan.status, 0) + 1
    return {
        "plan_amount": _f(plan_amount),
        "received_amount": _f(received),
        "unreceived_amount": _f(plan_amount - Decimal(received)),
        "overdue_count": by_status.get("overdue", 0),
        "by_status": [
            {"name": PLAN_LABEL.get(k, k), "value": v} for k, v in by_status.items()
        ],
    }


async def loss_reason_stats(session: AsyncSession, user: CurrentUser) -> list[dict]:
    rows = (
        await session.execute(
            await _scope_filter(
                select(LossReason.name, func.count(Opportunity.id))
                .join(Opportunity, Opportunity.loss_reason_id == LossReason.id)
                .where(Opportunity.status == "loss", Opportunity.deleted_at.is_(None))
                .group_by(LossReason.name),
                user,
                Opportunity.owner_id,
                session,
            )
        )
    ).all()
    result = [{"name": name, "value": int(count)} for name, count in rows]
    result.sort(key=lambda x: -x["value"])
    return result


# ================================================================== 新增分析维度
# 03-API §35 里列出但此前缺失的三个整接口：leads / pricing / payments

async def lead_stats(session: AsyncSession, user: CurrentUser) -> dict:
    """线索分析：来源分布、状态分布、转化率、平均转化时长、无效原因。

    线索池里 owner_id 为空的（未分配）也算进总量，否则管理员看到的
    "待分配线索"会凭空消失；但已分配的按数据范围过滤。
    """
    stmt = select(Lead).where(Lead.deleted_at.is_(None))
    owner_ids = await scoped_owner_ids(session, user)
    if owner_ids is not None:
        stmt = stmt.where(
            or_(Lead.owner_id.in_(owner_ids), Lead.owner_id.is_(None))
        )
    leads = (await session.execute(stmt)).scalars().all()

    by_source: dict[str, int] = {}
    by_status: dict[str, int] = {}
    invalid_reasons: dict[str, int] = {}
    conversion_days: list[float] = []

    for lead in leads:
        by_source[lead.source or "未填写"] = by_source.get(lead.source or "未填写", 0) + 1
        by_status[lead.status] = by_status.get(lead.status, 0) + 1
        if lead.status == "invalid":
            key = (lead.invalid_reason or "未填写").strip()
            invalid_reasons[key] = invalid_reasons.get(key, 0) + 1
        if lead.status == "converted" and lead.created_at and lead.updated_at:
            # 转化时长用「创建 → 最后更新」近似。线索表没有单独的 converted_at，
            # 转化时会更新该行，所以这是最接近的可用口径。
            created = lead.created_at
            updated = lead.updated_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=UTC)
            conversion_days.append((updated - created).total_seconds() / 86400)

    total = len(leads)
    converted = by_status.get("converted", 0)
    return {
        "total": total,
        "converted_count": converted,
        "conversion_rate": round(converted / total, 4) if total else 0,
        "average_conversion_days": (
            round(sum(conversion_days) / len(conversion_days), 1) if conversion_days else None
        ),
        "by_source": [
            {"name": k, "value": v} for k, v in sorted(by_source.items(), key=lambda x: -x[1])
        ],
        "by_status": [
            {"name": LEAD_STATUS_LABEL.get(k, k), "value": v}
            for k, v in sorted(by_status.items(), key=lambda x: -x[1])
        ],
        "invalid_reasons": [
            {"name": k, "value": v}
            for k, v in sorted(invalid_reasons.items(), key=lambda x: -x[1])
        ],
    }


async def pricing_stats(session: AsyncSession, user: CurrentUser) -> dict:
    """价格与核价分析：低价审批率、让价幅度、按客户等级的报价水平。

    让价 = (建议价快照 - 实际报价) / 建议价快照。
    这是"业务实际让了多少"，不是"折扣权限"——两者口径不同，别混用。
    """
    stmt = (
        select(QuoteItem, QuoteVersion, Customer.level)
        .join(QuoteVersion, QuoteVersion.id == QuoteItem.quote_version_id)
        .join(Quote, Quote.id == QuoteVersion.quote_id)
        .outerjoin(Customer, Customer.id == Quote.customer_id)
    )
    rows = (
        await session.execute(await _scope_filter(stmt, user, Quote.owner_id, session))
    ).all()

    total_items = len(rows)
    discounted: list[float] = []
    need_approval = 0
    by_level: dict[str, list[float]] = {}

    for item, _version, level in rows:
        if item.approval_required:
            need_approval += 1
        recommended = item.recommended_price_snapshot
        if recommended and float(recommended) > 0:
            gap = (float(recommended) - float(item.quoted_price)) / float(recommended)
            discounted.append(gap)
            key = level or "未分级"
            by_level.setdefault(key, []).append(float(item.quoted_price))

    return {
        "item_count": total_items,
        "approval_required_count": need_approval,
        "low_price_approval_rate": (
            round(need_approval / total_items, 4) if total_items else 0
        ),
        "average_discount_rate": (
            round(sum(discounted) / len(discounted), 4) if discounted else 0
        ),
        "max_discount_rate": round(max(discounted), 4) if discounted else 0,
        "average_quoted_price_by_level": [
            {
                "level": level,
                "item_count": len(prices),
                "average_price": round(sum(prices) / len(prices), 2),
            }
            for level, prices in sorted(by_level.items())
        ],
    }


async def payment_stats(session: AsyncSession, user: CurrentUser) -> dict:
    """回款分析：应收状态分布、逾期账龄分布、回款方式分布。

    账龄按「今天 - 应收日期」分档，只统计还没结清的节点。
    """
    plans = (
        await session.execute(
            await _scope_filter(
                select(ReceivablePlan)
                .join(SalesOrder, SalesOrder.id == ReceivablePlan.order_id)
                .where(SalesOrder.status != "cancelled"),
                user,
                SalesOrder.owner_id,
                session,
            )
        )
    ).scalars().all()

    today = datetime.now(UTC).date()
    buckets = {"未到期": 0, "1-30 天": 0, "31-60 天": 0, "61-90 天": 0, "90 天以上": 0}
    overdue_amount = 0.0
    for plan in plans:
        if plan.status in ("paid", "cancelled"):
            continue
        if plan.due_date is None:
            continue
        overdue_days = (today - plan.due_date).days
        if overdue_days <= 0:
            buckets["未到期"] += 1
            continue
        overdue_amount += float(plan.amount)
        if overdue_days <= 30:
            buckets["1-30 天"] += 1
        elif overdue_days <= 60:
            buckets["31-60 天"] += 1
        elif overdue_days <= 90:
            buckets["61-90 天"] += 1
        else:
            buckets["90 天以上"] += 1

    method_stmt = (
        select(PaymentRecord.payment_method, func.coalesce(func.sum(PaymentRecord.received_amount), 0))
        .select_from(PaymentRecord)
        .join(SalesOrder, SalesOrder.id == PaymentRecord.order_id)
        .where(PaymentRecord.status == "confirmed")
        .group_by(PaymentRecord.payment_method)
    )
    method_rows = (
        await session.execute(
            await _scope_filter(method_stmt, user, SalesOrder.owner_id, session)
        )
    ).all()

    return {
        "plan_count": len(plans),
        "overdue_node_count": sum(v for k, v in buckets.items() if k != "未到期"),
        "overdue_amount": round(overdue_amount, 2),
        "aging": [{"name": k, "value": v} for k, v in buckets.items()],
        "by_payment_method": [
            {"name": method or "未填写", "value": _f(amount)} for method, amount in method_rows
        ],
    }


# ================================================================== 主管视图
# PRD §4.2 主管工作台：团队任务 / 团队逾期 / 待审批报价 / 商机漏斗 /
# 风险商机 / 客户分配 / 团队成交情况。
# 这些是**团队维度**指标，必须按角色数据范围判定能否展示（见下）。

# 能看到团队视图的数据范围。`self` 的人只有自己的数据，
# 给他返回"团队"指标等于泄露全公司数据（这类越权本项目已经修过多次）。
TEAM_SCOPES = {"department", "department_and_sub", "all"}


async def team_summary(session: AsyncSession, user: CurrentUser) -> dict:
    """主管工作台汇总。

    数据范围是 `self` 时返回 `is_team_view: False` 与空指标，
    由前端隐藏整块——**不是**返回全员数据再靠前端不显示。
    """
    if user.data_scope not in TEAM_SCOPES:
        return {"is_team_view": False, "data_scope": user.data_scope}

    now = datetime.now(UTC)
    month_start = now.date().replace(day=1)
    owner_ids = await scoped_owner_ids(session, user)

    def scoped(stmt: Select, column) -> Select:
        """团队指标只看数据范围内的负责人；all 时不加限制。"""
        if owner_ids is None:
            return stmt
        return stmt.where(column.in_(owner_ids))

    # 团队成员（在岗、且在范围内）
    member_stmt = select(User).where(User.status == "active")
    if owner_ids is not None:
        member_stmt = member_stmt.where(User.id.in_(owner_ids))
    members = (await session.execute(member_stmt.order_by(User.id.asc()))).scalars().all()
    member_ids = [m.id for m in members]

    # 团队任务 / 逾期
    team_task_count = (
        await session.execute(
            scoped(
                select(func.count(Task.id)).where(Task.status.in_(["pending", "doing"])),
                Task.owner_id,
            )
        )
    ).scalar_one()
    team_overdue_count = (
        await session.execute(
            scoped(
                select(func.count(Task.id)).where(
                    Task.due_at < now, Task.status.in_(["pending", "doing"])
                ),
                Task.owner_id,
            )
        )
    ).scalar_one()

    # 待审批报价（团队提交的）
    pending_approval = (
        await session.execute(
            scoped(
                select(func.count(ApprovalInstance.id)).where(
                    ApprovalInstance.status == "pending"
                ),
                ApprovalInstance.applicant_id,
            )
        )
    ).scalar_one()

    # 客户分配：无负责人的客户（公海 + 从未分配）
    unassigned_customers = (
        await session.execute(
            select(func.count(Customer.id)).where(
                Customer.deleted_at.is_(None), Customer.owner_id.is_(None)
            )
        )
    ).scalar_one()

    # 团队成交情况（本月）
    won_count, won_amount = (
        await session.execute(
            scoped(
                select(
                    func.count(Opportunity.id),
                    func.coalesce(func.sum(Opportunity.expected_amount), 0),
                ).where(
                    Opportunity.deleted_at.is_(None),
                    Opportunity.status == "win",
                    Opportunity.updated_at >= datetime.combine(
                        month_start, datetime.min.time(), tzinfo=UTC
                    ),
                ),
                Opportunity.owner_id,
            )
        )
    ).one()

    # 团队成员明细：各自的待办 / 逾期 / 本月成交额 / 待跟进客户
    stale_date = now - timedelta(
        days=int(await settings_service.get_number(session, "customer_stale_days", "days", 30))
    )
    per_member: list[dict] = []
    for member in members:
        todo = (
            await session.execute(
                select(func.count(Task.id)).where(
                    Task.owner_id == member.id, Task.status.in_(["pending", "doing"])
                )
            )
        ).scalar_one()
        overdue = (
            await session.execute(
                select(func.count(Task.id)).where(
                    Task.owner_id == member.id,
                    Task.due_at < now,
                    Task.status.in_(["pending", "doing"]),
                )
            )
        ).scalar_one()
        amount = (
            await session.execute(
                select(func.coalesce(func.sum(Opportunity.expected_amount), 0)).where(
                    Opportunity.owner_id == member.id,
                    Opportunity.deleted_at.is_(None),
                    Opportunity.status == "win",
                    Opportunity.updated_at >= datetime.combine(
                        month_start, datetime.min.time(), tzinfo=UTC
                    ),
                )
            )
        ).scalar_one()
        stale = (
            await session.execute(
                select(func.count(Customer.id)).where(
                    Customer.deleted_at.is_(None),
                    Customer.owner_id == member.id,
                    Customer.pool_status == "private",
                    or_(
                        Customer.last_followup_at.is_(None),
                        Customer.last_followup_at < stale_date,
                    ),
                )
            )
        ).scalar_one()
        per_member.append(
            {
                "user_id": member.id,
                "name": member.name,
                "todo_count": int(todo),
                "overdue_count": int(overdue),
                "won_amount_this_month": _f(amount),
                "stale_customer_count": int(stale),
            }
        )

    # 风险商机（团队范围内）
    risky = await risk_opportunities(session, user, limit=10)

    # 商机漏斗（团队范围内）
    team_funnel = await funnel(session, user)

    return {
        "is_team_view": True,
        "data_scope": user.data_scope,
        "member_count": len(member_ids),
        "team_task_count": int(team_task_count),
        "team_overdue_count": int(team_overdue_count),
        "pending_approval_count": int(pending_approval),
        "unassigned_customer_count": int(unassigned_customers),
        "team_won_count_this_month": int(won_count),
        "team_won_amount_this_month": _f(won_amount),
        "members": per_member,
        "risky_opportunities": risky,
        "funnel": team_funnel,
    }
