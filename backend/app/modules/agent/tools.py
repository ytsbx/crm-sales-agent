"""Agent 工具集。

规则（05-TECH §19、§20）：
- 工具不直接被大模型调用数据库；大模型只能"点菜"，由 Action Gateway 执行；
- 每个工具都标了风险等级：L1 自动执行、L2 用户确认后执行、L3 转审批；
- 写动作统一走 audit，保证"AI 建议了什么、谁确认、改了什么"可追溯。
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.data_scope import scoped_owner_ids
from app.core.deps import CurrentUser
from app.core.errors import AppError, ErrorCode
from app.modules.customer.model import Contact, Customer
from app.modules.followup.model import FollowUp
from app.modules.opportunity.model import Opportunity, OpportunityItem, OpportunityStage
from app.modules.order.model import ORDER_STATUS_LABEL, SalesOrder, SalesOrderItem
from app.modules.payment.model import PaymentRecord, ReceivablePlan
from app.modules.pricing import service as pricing_service
from app.modules.product.model import Product, Sku
from app.modules.quote.model import QUOTE_STATUS_LABEL, Quote, QuoteVersion
from app.modules.task.model import Task


@dataclass
class ToolContext:
    session: AsyncSession
    user: CurrentUser
    agent_session_id: int


@dataclass
class ToolSpec:
    name: str
    label: str
    description: str
    parameters: dict
    risk: str
    handler: Callable[..., Awaitable[dict[str, Any]]]
    business_type: str | None = None


TOOLS: dict[str, ToolSpec] = {}

# 工具的中文名。
# 注意：函数名必须保持 ASCII —— OpenAI / DeepSeek 的函数调用规范要求名字匹配
# ^[a-zA-Z0-9_-]{1,64}$，中文名会被接口直接拒绝。所以英文名只做技术标识，
# 凡是给用户看的地方（界面、对话、动作卡片）一律走这张对照表。
TOOL_LABELS: dict[str, str] = {
    "search_customers": "查客户",
    "get_customer_overview": "看客户全貌",
    "list_opportunities": "列商机",
    "get_opportunity_detail": "看商机详情",
    "list_my_tasks": "查我的待办",
    "list_sku_options": "查产品 SKU",
    "calculate_price": "核价",
    "get_receivables_summary": "查应收与回款",
    "create_followup": "记录跟进",
    "create_task": "创建任务",
    "update_opportunity_next_action": "更新商机下一步动作",
    "request_quote_approval": "提交报价审批",
    # 03-API §38 补齐的 8 个
    "search_leads": "查线索",
    "get_contact": "看联系人",
    "get_product": "看产品",
    "search_skus": "搜 SKU",
    "calculate_logistics": "物流试算",
    "create_quote_draft": "生成报价草稿",
    "create_quote_version": "新建报价版本",
    "get_order": "看订单",
}


def tool_label(name: str) -> str:
    """给用户看的工具名一律用中文；函数名只在协议层使用。"""
    return TOOL_LABELS.get(name, name)


def tool(name: str, description: str, parameters: dict, risk: str, business_type: str | None = None):
    def decorator(handler):
        TOOLS[name] = ToolSpec(
            name=name,
            label=tool_label(name),
            description=description,
            parameters=parameters,
            risk=risk,
            handler=handler,
            business_type=business_type,
        )
        return handler

    return decorator


def openai_tools() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.parameters,
            },
        }
        for spec in TOOLS.values()
    ]


def _money(value) -> float | None:
    return None if value is None else round(float(value), 2)


async def _scope(stmt, ctx: ToolContext, column):
    """与业务模块一致的数据范围过滤：Agent 不能绕过权限看数据。

    `department_and_sub` 会递归到下级部门，见 app/core/data_scope.py。
    """
    owner_ids = await scoped_owner_ids(ctx.session, ctx.user)
    if owner_ids is None:
        return stmt
    return stmt.where(column.in_(owner_ids))


# ------------------------------------------------------------------ L1 只读

@tool(
    "search_customers",
    "按关键词搜索客户，返回客户 id、名称、等级、负责人、最近跟进时间。",
    {
        "type": "object",
        "properties": {"keyword": {"type": "string", "description": "客户名称关键词，可留空"}},
    },
    "L1",
)
async def search_customers(ctx: ToolContext, keyword: str = "") -> dict:
    stmt = select(Customer).where(Customer.deleted_at.is_(None))
    if keyword:
        stmt = stmt.where(Customer.name.ilike(f"%{keyword}%"))
    stmt = await _scope(stmt.order_by(Customer.id.desc()).limit(10), ctx, Customer.owner_id)
    rows = (await ctx.session.execute(stmt)).scalars().all()
    return {
        "count": len(rows),
        "customers": [
            {
                "id": c.id,
                "name": c.name,
                "level": c.level,
                "region": c.region,
                "pool_status": c.pool_status,
                "last_followup_at": c.last_followup_at.isoformat() if c.last_followup_at else None,
            }
            for c in rows
        ],
    }


@tool(
    "get_customer_overview",
    "获取一个客户的完整画像：基本信息、联系人、商机、报价、订单与待回款。",
    {"type": "object", "properties": {"customer_id": {"type": "integer"}}, "required": ["customer_id"]},
    "L1",
)
async def get_customer_overview(ctx: ToolContext, customer_id: int) -> dict:
    customer = await ctx.session.get(Customer, customer_id)
    if customer is None or customer.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "客户不存在", 404)
    contacts = (
        await ctx.session.execute(
            select(Contact).where(Contact.customer_id == customer_id, Contact.deleted_at.is_(None))
        )
    ).scalars().all()
    opportunities = (
        await ctx.session.execute(
            select(Opportunity, OpportunityStage.name)
            .join(OpportunityStage, OpportunityStage.id == Opportunity.stage_id)
            .where(Opportunity.customer_id == customer_id, Opportunity.deleted_at.is_(None))
        )
    ).all()
    quotes = (
        await ctx.session.execute(
            select(Quote).where(Quote.customer_id == customer_id, Quote.deleted_at.is_(None))
        )
    ).scalars().all()
    orders = (
        await ctx.session.execute(
            select(SalesOrder).where(SalesOrder.customer_id == customer_id)
        )
    ).scalars().all()
    pending_receivable = 0.0
    for order in orders:
        received = (
            await ctx.session.execute(
                select(func.coalesce(func.sum(PaymentRecord.received_amount), 0)).where(
                    PaymentRecord.order_id == order.id, PaymentRecord.status == "confirmed"
                )
            )
        ).scalar_one()
        pending_receivable += float(order.total_amount or 0) - float(received)
    return {
        "customer": {
            "id": customer.id,
            "name": customer.name,
            "level": customer.level,
            "source": customer.source,
            "region": customer.region,
            "address": customer.address,
            "pool_status": customer.pool_status,
            "last_followup_at": customer.last_followup_at.isoformat()
            if customer.last_followup_at
            else None,
        },
        "contacts": [
            {"id": c.id, "name": c.name, "title": c.title, "mobile": c.mobile, "is_primary": c.is_primary}
            for c in contacts
        ],
        "opportunities": [
            {
                "id": opp.id,
                "title": opp.title,
                "stage": stage_name,
                "status": opp.status,
                "expected_amount": _money(opp.expected_amount),
                "expected_close_date": opp.expected_close_date.isoformat()
                if opp.expected_close_date
                else None,
            }
            for opp, stage_name in opportunities
        ],
        "quotes": [
            {
                "id": q.id,
                "quote_no": q.quote_no,
                "status": QUOTE_STATUS_LABEL.get(q.status, q.status),
                "valid_until": q.valid_until.isoformat() if q.valid_until else None,
            }
            for q in quotes
        ],
        "orders": [
            {
                "id": o.id,
                "order_no": o.order_no,
                "status": ORDER_STATUS_LABEL.get(o.status, o.status),
                "total_amount": _money(o.total_amount),
                "delivery_date": o.delivery_date.isoformat() if o.delivery_date else None,
            }
            for o in orders
        ],
        "pending_receivable_amount": round(pending_receivable, 2),
    }


@tool(
    "list_opportunities",
    "列出商机，可按状态过滤（open 进行中 / win 已成交 / loss 已失单）。",
    {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["open", "win", "loss"]},
            "keyword": {"type": "string"},
        },
    },
    "L1",
)
async def list_opportunities(ctx: ToolContext, status: str = "open", keyword: str = "") -> dict:
    stmt = (
        select(Opportunity, OpportunityStage.name, Customer.name)
        .join(OpportunityStage, OpportunityStage.id == Opportunity.stage_id)
        .join(Customer, Customer.id == Opportunity.customer_id)
        .where(Opportunity.deleted_at.is_(None), Opportunity.status == status)
    )
    if keyword:
        stmt = stmt.where(Opportunity.title.ilike(f"%{keyword}%"))
    stmt = await _scope(stmt.order_by(Opportunity.id.desc()).limit(15), ctx, Opportunity.owner_id)
    rows = (await ctx.session.execute(stmt)).all()
    return {
        "count": len(rows),
        "opportunities": [
            {
                "id": opp.id,
                "title": opp.title,
                "customer": customer_name,
                "stage": stage_name,
                "expected_amount": _money(opp.expected_amount),
                "expected_close_date": opp.expected_close_date.isoformat()
                if opp.expected_close_date
                else None,
                "next_action": opp.next_action,
            }
            for opp, stage_name, customer_name in rows
        ],
    }


@tool(
    "get_opportunity_detail",
    "获取商机详情：客户需求明细、最近报价版本、阶段流转与最近跟进。",
    {"type": "object", "properties": {"opportunity_id": {"type": "integer"}}, "required": ["opportunity_id"]},
    "L1",
)
async def get_opportunity_detail(ctx: ToolContext, opportunity_id: int) -> dict:
    opportunity = await ctx.session.get(Opportunity, opportunity_id)
    if opportunity is None or opportunity.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "商机不存在", 404)
    items = (
        await ctx.session.execute(
            select(OpportunityItem, Sku.sku_code, Sku.specification)
            .join(Sku, Sku.id == OpportunityItem.sku_id)
            .where(OpportunityItem.opportunity_id == opportunity_id)
        )
    ).all()
    quotes = (
        await ctx.session.execute(
            select(Quote).where(Quote.opportunity_id == opportunity_id, Quote.deleted_at.is_(None))
        )
    ).scalars().all()
    version_info = []
    for quote in quotes:
        versions = (
            await ctx.session.execute(
                select(QuoteVersion)
                .where(QuoteVersion.quote_id == quote.id)
                .order_by(QuoteVersion.version_no.desc())
            )
        ).scalars().all()
        version_info.append(
            {
                "quote_id": quote.id,
                "quote_no": quote.quote_no,
                "status": QUOTE_STATUS_LABEL.get(quote.status, quote.status),
                "versions": [
                    {
                        "version_id": v.id,
                        "version_no": v.version_no,
                        "total_amount": _money(v.total_amount),
                        "approval_status": v.approval_status,
                    }
                    for v in versions
                ],
            }
        )
    followups = (
        await ctx.session.execute(
            select(FollowUp)
            .where(FollowUp.opportunity_id == opportunity_id)
            .order_by(FollowUp.id.desc())
            .limit(5)
        )
    ).scalars().all()
    return {
        "opportunity": {
            "id": opportunity.id,
            "title": opportunity.title,
            "status": opportunity.status,
            "expected_amount": _money(opportunity.expected_amount),
            "expected_close_date": opportunity.expected_close_date.isoformat()
            if opportunity.expected_close_date
            else None,
            "competitor": opportunity.competitor,
            "next_action": opportunity.next_action,
            "risk_level": opportunity.risk_level,
        },
        "items": [
            {
                "id": item.id,
                "sku_code": code,
                "specification": item.specification or spec,
                "quantity": float(item.quantity),
                "target_price": _money(item.target_price),
                "destination": item.destination,
            }
            for item, code, spec in items
        ],
        "quotes": version_info,
        "recent_followups": [
            {
                "content": f.content,
                "feedback": f.customer_feedback,
                "next_action": f.next_action,
                "at": f.created_at.isoformat(),
            }
            for f in followups
        ],
    }


@tool(
    "list_my_tasks",
    "列出当前用户的待办任务。",
    {"type": "object", "properties": {}},
    "L1",
)
async def list_my_tasks(ctx: ToolContext) -> dict:
    rows = (
        await ctx.session.execute(
            select(Task)
            .where(Task.owner_id == ctx.user.id, Task.status.in_(["pending", "doing"]))
            # 可移植的 NULLS LAST 写法（.nullslast() 是 PG 专有）
            .order_by(Task.due_at.is_(None).asc(), Task.due_at.asc())
            .limit(15)
        )
    ).scalars().all()
    now = datetime.now(UTC)
    return {
        "count": len(rows),
        "tasks": [
            {
                "id": t.id,
                "title": t.title,
                "due_at": t.due_at.isoformat() if t.due_at else None,
                "overdue": bool(
                    t.due_at and (t.due_at if t.due_at.tzinfo else t.due_at.replace(tzinfo=UTC)) < now
                ),
                "opportunity_id": t.opportunity_id,
                "customer_id": t.customer_id,
            }
            for t in rows
        ],
    }


@tool(
    "list_sku_options",
    "列出可报价的 SKU（含 id、编码、规格、起订量）。核价前先用它拿到 sku_id。",
    {"type": "object", "properties": {"keyword": {"type": "string"}}},
    "L1",
)
async def list_sku_options(ctx: ToolContext, keyword: str = "") -> dict:
    stmt = (
        select(Sku, Product.name)
        .join(Product, Product.id == Sku.product_id)
        .where(Sku.deleted_at.is_(None), Sku.status == "active")
    )
    if keyword:
        stmt = stmt.where(Sku.sku_code.ilike(f"%{keyword}%") | Sku.specification.ilike(f"%{keyword}%"))
    rows = (await ctx.session.execute(stmt.limit(30))).all()
    return {
        "count": len(rows),
        "skus": [
            {
                "id": sku.id,
                "product": product_name,
                "sku_code": sku.sku_code,
                "specification": sku.specification,
                "moq": sku.moq,
                "unit": sku.unit,
            }
            for sku, product_name in rows
        ],
    }


@tool(
    "calculate_price",
    "调用核价引擎：给客户与数量算成本、建议价、最低允许价、利润率，并判断是否需要审批。",
    {
        "type": "object",
        "properties": {
            "sku_id": {"type": "integer"},
            "quantity": {"type": "number"},
            "customer_id": {"type": "integer"},
            "quoted_price": {"type": "number", "description": "想报的价格，填了就判断是否需要审批"},
        },
        "required": ["sku_id", "quantity"],
    },
    "L1",
)
async def calculate_price(
    ctx: ToolContext,
    sku_id: int,
    quantity: float,
    customer_id: int | None = None,
    quoted_price: float | None = None,
) -> dict:
    result = await pricing_service.calculate_price(
        ctx.session,
        sku_id=sku_id,
        quantity=quantity,
        customer_id=customer_id,
        quoted_price=quoted_price,
        role_codes=ctx.user.roles,
    )
    return {
        "sku": result["sku"]["sku_code"],
        "base_cost": result["cost"]["base_cost"],
        "standard_price": result["standard_price"],
        "recommended_price": result["recommended_price"],
        "recommended_range": result["recommended_range"],
        "minimum_price": result["minimum_price"],
        "quoted_price": result["quoted_price"],
        "profit": result["profit"],
        "profit_rate": result["profit_rate"],
        "authorized_min_margin": result["authorized_min_margin"],
        "approval_required": result["approval_required"],
        "warnings": result["warnings"],
    }


@tool(
    "get_receivables_summary",
    "汇总应收与回款情况：应收合计、已收、未收、逾期节点。",
    {"type": "object", "properties": {"order_id": {"type": "integer"}}},
    "L1",
)
async def get_receivables_summary(ctx: ToolContext, order_id: int | None = None) -> dict:
    stmt = select(ReceivablePlan)
    if order_id:
        stmt = stmt.where(ReceivablePlan.order_id == order_id)
    plans = (await ctx.session.execute(stmt)).scalars().all()
    received = (
        await ctx.session.execute(
            select(func.coalesce(func.sum(PaymentRecord.received_amount), 0)).where(
                PaymentRecord.status == "confirmed",
                PaymentRecord.order_id == order_id if order_id else True,
            )
        )
    ).scalar_one()
    plan_amount = sum((float(p.amount or 0) for p in plans), 0.0)
    return {
        "plan_count": len(plans),
        "plan_amount": round(plan_amount, 2),
        "received_amount": round(float(received), 2),
        "unreceived_amount": round(plan_amount - float(received), 2),
        "overdue": [
            {"order_id": p.order_id, "plan_name": p.plan_name, "due_date": p.due_date.isoformat()}
            for p in plans
            if p.status == "overdue"
        ],
    }


# ------------------------------------------------------------------ L2 确认后执行

@tool(
    "create_followup",
    "记录一条跟进（已经发生的销售行为）。需要用户确认后才会写入。",
    {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "跟进内容"},
            "customer_id": {"type": "integer"},
            "opportunity_id": {"type": "integer"},
            "followup_type": {"type": "string", "description": "电话 / 微信 / 拜访 / 邮件"},
            "customer_feedback": {"type": "string"},
            "next_action": {"type": "string"},
        },
        "required": ["content"],
    },
    "L2",
    "customer",
)
async def create_followup(ctx: ToolContext, **kwargs) -> dict:
    payload = {k: v for k, v in kwargs.items() if v is not None}
    followup = FollowUp(**payload, owner_id=ctx.user.id)
    ctx.session.add(followup)
    await ctx.session.flush()
    if payload.get("customer_id"):
        customer = await ctx.session.get(Customer, payload["customer_id"])
        if customer:
            customer.last_followup_at = datetime.now(UTC)
    await write_audit(
        ctx.session,
        operator_id=ctx.user.id,
        action="create",
        business_type="followup",
        business_id=followup.id,
        after=payload,
        source="AGENT",
    )
    await ctx.session.commit()
    return {"followup_id": followup.id, "message": "跟进已记录"}


@tool(
    "create_task",
    "创建一条待办任务（未来要执行的动作）。需要用户确认后才会写入。",
    {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "due_at": {"type": "string", "description": "ISO 时间，例如 2026-09-30T10:00:00+08:00"},
            "priority": {"type": "string", "enum": ["high", "normal", "low"]},
            "customer_id": {"type": "integer"},
            "opportunity_id": {"type": "integer"},
        },
        "required": ["title"],
    },
    "L2",
    "task",
)
async def create_task(ctx: ToolContext, **kwargs) -> dict:
    payload = {k: v for k, v in kwargs.items() if v is not None}
    due_at = payload.get("due_at")
    parsed_due = None
    if due_at:
        try:
            parsed_due = datetime.fromisoformat(str(due_at).replace("Z", "+00:00"))
        except ValueError as exc:
            raise AppError(ErrorCode.PARAM_ERROR, f"时间格式不对：{due_at}") from exc
    task = Task(
        title=payload["title"],
        task_type="agent",
        customer_id=payload.get("customer_id"),
        opportunity_id=payload.get("opportunity_id"),
        owner_id=ctx.user.id,
        priority=payload.get("priority", "normal"),
        status="pending",
        due_at=parsed_due,
        source="agent",
    )
    ctx.session.add(task)
    await ctx.session.flush()
    await write_audit(
        ctx.session,
        operator_id=ctx.user.id,
        action="create",
        business_type="task",
        business_id=task.id,
        after={"title": task.title, "due_at": due_at},
        source="AGENT",
    )
    await ctx.session.commit()
    return {"task_id": task.id, "message": "任务已创建"}


@tool(
    "update_opportunity_next_action",
    "更新商机的「下一步动作」。需要用户确认后才会写入。",
    {
        "type": "object",
        "properties": {
            "opportunity_id": {"type": "integer"},
            "next_action": {"type": "string"},
        },
        "required": ["opportunity_id", "next_action"],
    },
    "L2",
    "opportunity",
)
async def update_opportunity_next_action(
    ctx: ToolContext, opportunity_id: int, next_action: str
) -> dict:
    opportunity = await ctx.session.get(Opportunity, opportunity_id)
    if opportunity is None or opportunity.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "商机不存在", 404)
    before = opportunity.next_action
    opportunity.next_action = next_action
    await write_audit(
        ctx.session,
        operator_id=ctx.user.id,
        action="update",
        business_type="opportunity",
        business_id=opportunity.id,
        before={"next_action": before},
        after={"next_action": next_action},
        source="AGENT",
    )
    await ctx.session.commit()
    return {"opportunity_id": opportunity.id, "next_action": next_action}


# ------------------------------------------------------------------ L3 审批后执行

@tool(
    "request_quote_approval",
    "把某个报价版本提交审批（用于低于权限的报价）。这是 L3 动作，需要用户确认后转审批流程。",
    {
        "type": "object",
        "properties": {
            "quote_version_id": {"type": "integer"},
            "reason": {"type": "string"},
        },
        "required": ["quote_version_id"],
    },
    "L3",
    "quote",
)
async def request_quote_approval(
    ctx: ToolContext, quote_version_id: int, reason: str | None = None
) -> dict:
    from app.modules.quote import service as quote_service

    version = await ctx.session.get(QuoteVersion, quote_version_id)
    if version is None:
        raise AppError(ErrorCode.NOT_FOUND, "报价版本不存在", 404)
    quote = await quote_service.get_quote_or_404(ctx.session, version.quote_id)
    instance, required = await quote_service.submit_for_approval(
        ctx.session,
        quote=quote,
        version=version,
        applicant_id=ctx.user.id,
        user_roles=ctx.user.roles,
        reason=reason,
    )
    await write_audit(
        ctx.session,
        operator_id=ctx.user.id,
        action="submit_approval",
        business_type="quote",
        business_id=quote.id,
        after={"approval_required": required, "source": "AGENT"},
        source="AGENT",
    )
    await ctx.session.commit()
    return {
        "approval_required": required,
        "approval_id": instance.id if instance else None,
        "message": "已提交审批" if required else "价格在权限内，报价已通过",
    }


# ==================================================================
# 03-API §38 补齐的 8 个工具
# 只读的走 L1（自动执行），会写数据的一律 L2（用户确认后执行）。
# 所有读取都走 _scope，Agent 不能绕过数据范围。
# ==================================================================


@tool(
    "search_leads",
    "按关键词搜索线索，可按状态过滤（pending 待分配 / assigned 已分配 / following 跟进中 / converted 已转客户 / invalid 无效）。",
    {
        "type": "object",
        "properties": {
            "keyword": {"type": "string"},
            "status": {"type": "string"},
        },
    },
    "L1",
)
async def search_leads(ctx: ToolContext, keyword: str = "", status: str = "") -> dict:
    from app.modules.lead.model import Lead

    stmt = select(Lead).where(Lead.deleted_at.is_(None))
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(
            Lead.name.ilike(like) | Lead.company_name.ilike(like) | Lead.mobile.ilike(like)
        )
    if status:
        stmt = stmt.where(Lead.status == status)
    # 线索池里未分配的线索人人可见，已分配的按数据范围过滤（与业务模块一致）
    stmt = await _scope(stmt.order_by(Lead.id.desc()).limit(15), ctx, Lead.owner_id)

    from app.modules.lead.service import STATUS_LABEL

    rows = (await ctx.session.execute(stmt)).scalars().all()
    return {
        "count": len(rows),
        "leads": [
            {
                "id": lead.id,
                "name": lead.name,
                "company_name": lead.company_name,
                "mobile": lead.mobile,
                "status": lead.status,
                "status_label": STATUS_LABEL.get(lead.status, lead.status),
                "source": lead.source,
            }
            for lead in rows
        ],
    }


@tool(
    "get_contact",
    "查看联系人详情（职位、手机、邮箱、是否主要联系人），用于确认对接人。",
    {"type": "object", "properties": {"contact_id": {"type": "integer"}}, "required": ["contact_id"]},
    "L1",
)
async def get_contact(ctx: ToolContext, contact_id: int) -> dict:
    contact = await ctx.session.get(Contact, contact_id)
    if contact is None or contact.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "联系人不存在", 404)
    return {
        "id": contact.id,
        "name": contact.name,
        "customer_id": contact.customer_id,
        "title": contact.title,
        "department": contact.department,
        "mobile": contact.mobile,
        "phone": contact.phone,
        "email": contact.email,
        "wechat": contact.wechat,
        "is_primary": contact.is_primary,
    }


@tool(
    "get_product",
    "查看产品详情及其 SKU 列表（规格、箱规、MOQ、单位）。",
    {"type": "object", "properties": {"product_id": {"type": "integer"}}, "required": ["product_id"]},
    "L1",
)
async def get_product(ctx: ToolContext, product_id: int) -> dict:
    product = await ctx.session.get(Product, product_id)
    if product is None or product.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "产品不存在", 404)
    skus = (
        await ctx.session.execute(
            select(Sku).where(Sku.product_id == product_id, Sku.deleted_at.is_(None))
        )
    ).scalars().all()
    return {
        "id": product.id,
        "name": product.name,
        "product_line": product.product_line,
        "category": product.category,
        "brand": product.brand,
        "description": product.description,
        "skus": [
            {
                "id": sku.id,
                "sku_code": sku.sku_code,
                "specification": sku.specification,
                "color": sku.color,
                "material": sku.material,
                "weight": _money(sku.weight),
                "carton_qty": sku.carton_qty,
                "moq": sku.moq,
                "unit": sku.unit,
                "status": sku.status,
            }
            for sku in skus
        ],
    }


@tool(
    "search_skus",
    "按关键词搜索 SKU（编码 / 规格 / 产品名），适合「客户问的是某个规格」这种场景。",
    {
        "type": "object",
        "properties": {"keyword": {"type": "string"}, "limit": {"type": "integer"}},
        "required": ["keyword"],
    },
    "L1",
)
async def search_skus(ctx: ToolContext, keyword: str, limit: int = 15) -> dict:
    like = f"%{keyword.strip()}%"
    rows = (
        await ctx.session.execute(
            select(Sku, Product.name)
            .join(Product, Product.id == Sku.product_id)
            .where(
                Sku.deleted_at.is_(None),
                Sku.status == "active",
                or_(
                    Sku.sku_code.ilike(like),
                    Sku.specification.ilike(like),
                    Sku.name.ilike(like),
                    Product.name.ilike(like),
                ),
            )
            .limit(max(1, min(limit, 50)))
        )
    ).all()
    return {
        "keyword": keyword,
        "count": len(rows),
        "skus": [
            {
                "id": sku.id,
                "product": product_name,
                "sku_code": sku.sku_code,
                "specification": sku.specification,
                "moq": sku.moq,
                "unit": sku.unit,
            }
            for sku, product_name in rows
        ],
    }


@tool(
    "calculate_logistics",
    "物流试算：给 SKU 与数量算计费重、运费与时效，可指定起运地、目的地、运输方式。",
    {
        "type": "object",
        "properties": {
            "sku_id": {"type": "integer"},
            "quantity": {"type": "number"},
            "origin": {"type": "string"},
            "destination": {"type": "string"},
            "shipping_method": {"type": "string"},
        },
        "required": ["sku_id", "quantity"],
    },
    "L1",
)
async def calculate_logistics(
    ctx: ToolContext,
    sku_id: int,
    quantity: float,
    origin: str | None = None,
    destination: str | None = None,
    shipping_method: str | None = None,
) -> dict:
    from app.modules.pricing import logistics as logistics_service

    prepared = await logistics_service.prepare(
        ctx.session,
        sku_id=sku_id,
        quantity=quantity,
        origin=origin,
        destination=destination,
        shipping_method=shipping_method,
    )
    options = prepared["options"]
    return {
        "sku": prepared["sku"]["sku_code"],
        "quantity": prepared["quantity"],
        "measures": {
            "actual_weight": _money(prepared["measures"]["actual_weight"]),
            "volume": _money(prepared["measures"]["volume"]),
            "chargeable_weight": _money(prepared["measures"]["chargeable_weight"]),
            "chargeable_basis": prepared["measures"]["chargeable_basis"],
        },
        "options": options[:5],
        "warnings": prepared["warnings"],
    }


@tool(
    "get_order",
    "查看订单详情：金额、履约状态、明细、应收与已回款情况。",
    {"type": "object", "properties": {"order_id": {"type": "integer"}}, "required": ["order_id"]},
    "L1",
)
async def get_order(ctx: ToolContext, order_id: int) -> dict:
    from app.modules.order import service as order_service
    from app.modules.payment import service as payment_service

    order = await ctx.session.get(SalesOrder, order_id)
    if order is None:
        raise AppError(ErrorCode.NOT_FOUND, "订单不存在", 404)

    items = (
        await ctx.session.execute(
            select(SalesOrderItem).where(SalesOrderItem.order_id == order_id)
        )
    ).scalars().all()
    plans = (
        await ctx.session.execute(
            select(ReceivablePlan).where(ReceivablePlan.order_id == order_id)
        )
    ).scalars().all()
    scope = await order_service.order_context(ctx.session, [order])
    return {
        "id": order.id,
        "order_no": order.order_no,
        "customer_name": scope["customers"].get(order.customer_id),
        "total_amount": _money(order.total_amount),
        "status": order.status,
        "status_label": ORDER_STATUS_LABEL.get(order.status, order.status),
        "delivery_date": order.delivery_date.isoformat() if order.delivery_date else None,
        "items": [
            {
                # 订单明细用的是 sku_snapshot（快照字符串），不是 quote_items 的 sku_code_snapshot
                "sku": item.sku_snapshot,
                "specification": item.specification,
                "quantity": _money(item.quantity),
                "unit_price": _money(item.unit_price),
                "amount": _money(item.amount),
            }
            for item in items
        ],
        "receivables": [
            {
                "plan_name": plan.plan_name,
                "due_date": plan.due_date.isoformat() if plan.due_date else None,
                "amount": _money(plan.amount),
                "status": plan.status,
            }
            for plan in plans
        ],
        "received_amount": _money(scope["received"].get(order.id, 0)),
        "finance": await payment_service.order_finance_summary(ctx.session, order.id),
    }


@tool(
    "create_quote_draft",
    "从商机生成报价草稿（按核价建议价自动带入明细）。生成的是**草稿**，不会自动发送或提交审批。需要用户确认后才会写入。",
    {
        "type": "object",
        "properties": {
            "opportunity_id": {"type": "integer"},
            "currency": {"type": "string", "description": "默认 CNY（内贸）"},
        },
        "required": ["opportunity_id"],
    },
    "L2",
    "quote",
)
async def create_quote_draft(
    ctx: ToolContext, opportunity_id: int, currency: str = "CNY"
) -> dict:
    from app.modules.quote import service as quote_service

    opportunity = await ctx.session.get(Opportunity, opportunity_id)
    if opportunity is None or opportunity.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "商机不存在", 404)

    quote = await quote_service.create_quote(
        ctx.session,
        user=ctx.user,
        opportunity=opportunity,
        currency=currency or "CNY",
    )
    # 只把标量写进审计与返回值。`_quote` / `_version` 是 ORM 对象，
    # 混进来会让 JSON 列写入直接报 "not JSON serializable"。
    payload = {
        key: value for key, value in quote.items() if not key.startswith("_")
    }
    await write_audit(
        ctx.session,
        operator_id=ctx.user.id,
        action="create",
        business_type="quote",
        business_id=payload["quote_id"],
        after={**payload, "source": "AGENT"},
        source="AGENT",
    )
    await ctx.session.commit()
    payload["message"] = "报价草稿已生成（未发送、未提交审批）"
    return payload


@tool(
    "create_quote_version",
    "在已有报价单上新建一个版本（复制上一版明细，用于改价后再谈）。旧版本不会被覆盖。需要用户确认后才会写入。",
    {"type": "object", "properties": {"quote_id": {"type": "integer"}}, "required": ["quote_id"]},
    "L2",
    "quote",
)
async def create_quote_version(ctx: ToolContext, quote_id: int) -> dict:
    from app.modules.quote import service as quote_service

    quote = await quote_service.get_quote_or_404(ctx.session, quote_id)
    version = await quote_service.create_version(ctx.session, quote=quote, user=ctx.user)
    await write_audit(
        ctx.session,
        operator_id=ctx.user.id,
        action="create_version",
        business_type="quote",
        business_id=quote_id,
        after={"version_id": version.id, "version_no": version.version_no, "source": "AGENT"},
        source="AGENT",
    )
    await ctx.session.commit()
    return {
        "quote_id": quote_id,
        "version_id": version.id,
        "version_no": version.version_no,
        "total_amount": _money(version.total_amount),
        "message": f"已新建 V{version.version_no}，明细复制自上一版",
    }
