"""聚合统计。

两条原则：
1. 工作台与分析页不新建业务表，全部实时聚合现有数据，避免"两份真相"；
2. 金额用数据库聚合函数算，不拉到 Python 里循环求和。
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import Select, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.modules.approval.model import ApprovalInstance
from app.modules.customer.model import Customer
from app.modules.lead.model import Lead
from app.modules.opportunity.model import LossReason, Opportunity, OpportunityStage
from app.modules.order.model import SalesOrder
from app.modules.payment.model import PLAN_STATUS_LABEL as PLAN_LABEL
from app.modules.payment.model import PaymentRecord, ReceivablePlan
from app.modules.product.model import Product, Sku
from app.modules.quote.model import Quote, QuoteItem, QuoteVersion
from app.modules.task.model import Task
from app.modules.user.model import User

ZERO = Decimal(0)


def _f(value) -> float:
    return float(value or 0)


def _scope_filter(stmt: Select, user: CurrentUser, column) -> Select:
    """按数据范围过滤（与各业务模块保持一致）。"""
    if user.data_scope == "all":
        return stmt
    if user.data_scope in ("department", "department_and_sub"):
        sub = select(User.id).where(User.department_id == user.department_id)
        return stmt.where(column.in_(sub))
    return stmt.where(column == user.id)


async def dashboard_summary(session: AsyncSession, user: CurrentUser) -> dict:
    now = datetime.now(UTC)
    month_start = now.date().replace(day=1)

    todo_count = (
        await session.execute(
            _scope_filter(
                select(func.count(Task.id)).where(
                    Task.status.in_(["pending", "doing"]), Task.owner_id.is_not(None)
                ),
                user,
                Task.owner_id,
            )
        )
    ).scalar_one()
    overdue_count = (
        await session.execute(
            _scope_filter(
                select(func.count(Task.id)).where(
                    Task.due_at < now, Task.status.in_(["pending", "doing"])
                ),
                user,
                Task.owner_id,
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
            _scope_filter(
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
            )
        )
    ).scalar_one()

    open_count, open_amount = (
        await session.execute(
            _scope_filter(
                select(
                    func.count(Opportunity.id),
                    func.coalesce(func.sum(Opportunity.expected_amount), 0),
                ).where(Opportunity.deleted_at.is_(None), Opportunity.status == "open"),
                user,
                Opportunity.owner_id,
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
            _scope_filter(
                select(func.coalesce(func.sum(SalesOrder.total_amount), 0)).where(
                    SalesOrder.status != "cancelled", SalesOrder.created_at >= month_start
                ),
                user,
                SalesOrder.owner_id,
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
            _scope_filter(
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
            )
        )
    ).scalar_one()

    # 本月回款（财务已确认）
    month_received = (
        await session.execute(
            _scope_filter(
                select(func.coalesce(func.sum(PaymentRecord.received_amount), 0))
                .select_from(PaymentRecord)
                .join(SalesOrder, SalesOrder.id == PaymentRecord.order_id)
                .where(PaymentRecord.status == "confirmed", PaymentRecord.received_date >= month_start),
                user,
                SalesOrder.owner_id,
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
            _scope_filter(
                select(func.count(Lead.id)).where(
                    Lead.deleted_at.is_(None),
                    Lead.status.in_(["pending", "assigned", "following"]),
                ),
                user,
                Lead.owner_id,
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
            _scope_filter(
                select(
                    func.to_char(SalesOrder.created_at, "YYYY-MM").label("m"),
                    func.coalesce(func.sum(SalesOrder.total_amount), 0),
                )
                .where(SalesOrder.status != "cancelled", SalesOrder.created_at >= start)
                .group_by("m"),
                user,
                SalesOrder.owner_id,
            )
        )
    ).all()
    payment_rows = (
        await session.execute(
            _scope_filter(
                select(
                    func.to_char(PaymentRecord.received_date, "YYYY-MM").label("m"),
                    func.coalesce(func.sum(PaymentRecord.received_amount), 0),
                )
                .select_from(PaymentRecord)
                .join(SalesOrder, SalesOrder.id == PaymentRecord.order_id)
                .where(PaymentRecord.status == "confirmed", PaymentRecord.received_date >= start.date())
                .group_by("m"),
                user,
                SalesOrder.owner_id,
            )
        )
    ).all()
    orders = {str(m): _f(v) for m, v in order_rows}
    payments = {str(m): _f(v) for m, v in payment_rows}
    return [
        {
            "month": m,
            "label": f"{int(m[5:7])}月",
            "order_amount": orders.get(m, 0.0),
            "received_amount": payments.get(m, 0.0),
        }
        for m in series
    ]


async def recent_activities(
    session: AsyncSession, user: CurrentUser, limit: int = 8
) -> list[dict]:
    """团队与业务动态：取最近的操作记录，转成人话。"""
    from app.core.audit import AuditLog

    rows = (
        await session.execute(
            select(AuditLog, User.name)
            .outerjoin(User, User.id == AuditLog.operator_id)
            .where(AuditLog.business_type.is_not(None), AuditLog.action.not_in(["login"]))
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
        "task": "任务",
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
            .order_by(Task.due_at.asc().nullslast(), Task.id.desc())
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
    stmt = _scope_filter(
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
        .order_by(Opportunity.expected_close_date.asc().nullslast(), Opportunity.id.desc())
        .limit(limit),
        user,
        Opportunity.owner_id,
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
    stmt = _scope_filter(
        select(
            Opportunity.stage_id,
            func.count(Opportunity.id),
            func.coalesce(func.sum(Opportunity.expected_amount), 0),
        )
        .where(Opportunity.deleted_at.is_(None), Opportunity.status == "open")
        .group_by(Opportunity.stage_id),
        user,
        Opportunity.owner_id,
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
            select(func.count(Opportunity.id)).where(Opportunity.status == "win")
        )
    ).scalar_one()
    loss_count = (
        await session.execute(
            select(func.count(Opportunity.id)).where(Opportunity.status == "loss")
        )
    ).scalar_one()
    settled = int(won_count) + int(loss_count)
    return {
        "won_count": int(won_count),
        "loss_count": int(loss_count),
        "win_rate": round(int(won_count) / settled, 4) if settled else 0,
        "loss_rate": round(int(loss_count) / settled, 4) if settled else 0,
        "funnel": await funnel(session, user),
    }


async def quote_stats(session: AsyncSession, user: CurrentUser) -> dict:
    quotes = (
        await session.execute(_scope_filter(select(Quote), user, Quote.owner_id))
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
            _scope_filter(
                select(Customer).where(Customer.deleted_at.is_(None)), user, Customer.owner_id
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
    month_start = datetime.now(UTC).date().replace(day=1)
    new_this_month = len(
        [c for c in customers if c.created_at and c.created_at.date() >= month_start]
    )
    return {
        "total": len(customers),
        "new_this_month": new_this_month,
        "by_source": [
            {"name": k, "value": v} for k, v in sorted(by_source.items(), key=lambda x: -x[1])
        ],
        "by_level": [{"name": k, "value": v} for k, v in sorted(by_level.items())],
    }


async def product_stats(session: AsyncSession, limit: int = 10) -> list[dict]:
    """产品表现：被报价次数与数量。"""
    rows = (
        await session.execute(
            select(
                Sku.sku_code,
                Sku.specification,
                Product.name,
                func.count(QuoteItem.id),
                func.coalesce(func.sum(QuoteItem.quantity), 0),
            )
            .join(QuoteItem, QuoteItem.sku_id == Sku.id)
            .join(Product, Product.id == Sku.product_id)
            .group_by(Sku.id, Sku.sku_code, Sku.specification, Product.name)
            .order_by(func.count(QuoteItem.id).desc())
            .limit(limit)
        )
    ).all()
    return [
        {
            "sku_code": code,
            "specification": spec,
            "product_name": product_name,
            "quote_times": int(count),
            "quote_quantity": _f(quantity),
        }
        for code, spec, product_name, count, quantity in rows
    ]


async def sales_user_stats(session: AsyncSession, limit: int = 20) -> list[dict]:
    rows = (
        await session.execute(
            select(
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
            )
            .where(User.status == "active")
            .order_by(User.id.asc())
            .limit(limit)
        )
    ).all()
    return [
        {
            "user_id": uid,
            "name": name,
            "customer_count": int(customers),
            "opportunity_count": int(opportunities),
            "quote_count": int(quotes),
            "order_amount": _f(order_amount),
        }
        for uid, name, customers, opportunities, quotes, order_amount in rows
    ]


async def receivable_stats(session: AsyncSession) -> dict:
    plans = (await session.execute(select(ReceivablePlan))).scalars().all()
    received = (
        await session.execute(
            select(func.coalesce(func.sum(PaymentRecord.received_amount), 0)).where(
                PaymentRecord.status == "confirmed"
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


async def loss_reason_stats(session: AsyncSession) -> list[dict]:
    rows = (
        await session.execute(
            select(LossReason.name, func.count(Opportunity.id))
            .join(Opportunity, Opportunity.loss_reason_id == LossReason.id)
            .where(Opportunity.status == "loss")
            .group_by(LossReason.name)
            .order_by(func.count(Opportunity.id).desc())
        )
    ).all()
    return [{"name": name, "value": int(count)} for name, count in rows]
