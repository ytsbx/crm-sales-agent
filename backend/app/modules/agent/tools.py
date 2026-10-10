"""Agent 工具集。

规则（05-TECH §19、§20）：
- 工具不直接被大模型调用数据库；大模型只能"点菜"，由 Action Gateway 执行；
- 每个工具都标了风险等级：L1 自动执行、L2 用户确认后执行、L3 转审批；
- 写动作统一走 audit，保证"AI 建议了什么、谁确认、改了什么"可追溯。
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Awaitable, Callable

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.data_scope import ensure_in_scope, scoped_owner_ids
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
    action_id: int | None = None


@dataclass
class ToolSpec:
    name: str
    label: str
    description: str
    parameters: dict
    risk: str
    handler: Callable[..., Awaitable[dict[str, Any]]]
    business_type: str | None = None
    #: 执行该工具所需的**模块查看/管理权限码**（与各业务模块 `router.py` 上的
    #: 原字符串逐字一致）。空元组 = 只要求 `agent:use`。
    #:
    #: 为什么必须声明，而不是只靠数据范围：数据范围回答"能看谁的数据"，
    #: 权限回答"这个模块的入口能不能进"。只有 `agent:use` 的人此前能通过
    #: Agent 读到财务汇总与成本价——因为工具层此前只做了范围过滤（§8.3）。
    permissions: tuple[str, ...] = ()


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


def tool(
    name: str,
    description: str,
    parameters: dict,
    risk: str,
    business_type: str | None = None,
    permissions: tuple[str, ...] = (),
):
    def decorator(handler):
        TOOLS[name] = ToolSpec(
            name=name,
            label=tool_label(name),
            description=description,
            parameters=parameters,
            risk=risk,
            handler=handler,
            business_type=business_type,
            permissions=permissions,
        )
        return handler

    return decorator


def missing_permissions(spec: ToolSpec, user: CurrentUser) -> list[str]:
    """当前用户缺哪些权限码（管理员与 `require_permission` 同一口径：默认放行）。"""
    if "admin" in user.roles:
        return []
    return [code for code in spec.permissions if not user.has(code)]


def ensure_tool_permission(spec: ToolSpec, user: CurrentUser) -> None:
    """执行网关的权限闸门：无权就抛 403，文案带上缺的权限码。

    文案与 `core/deps.require_permission` 同风格——"无操作权限：需要
    payment:view"远比"没有权限"能定位问题：看到的人知道该给谁配哪个权限。

    **三处入口都要调它**（`runtime.run_turn_events` / `execute_action` /
    `retry_tool_call`）：只把住流式那一处，等于用"重试"或"确认卡"绕过去。
    """
    missing = missing_permissions(spec, user)
    if missing:
        raise AppError(
            ErrorCode.FORBIDDEN,
            f"无操作权限：需要 {' / '.join(missing)}（工具「{spec.label}」）",
            403,
        )


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


async def _may_see_full_contact(ctx: ToolContext, customer: Customer) -> bool:
    """能否看到该客户联系人的**完整**联系方式。

    转调 `contact_util.can_view_full_contact`（用户 2026-10-06 确认的统一口径）：
    负责人本客户 / 主管本团队 / 管理员或全量范围 / 显式授权 `customer:contact_full`；
    其余（含公海客户的所有可见者）脱敏。

    为什么不再在这里自己判：客户详情、联系人列表、搜索、AI、导出是**五个**入口，
    各写一份"谁算自己人"的规则，迟早有一份漏掉 —— 而漏掉的那份就是泄漏面。
    """
    from app.modules.contact_util import can_view_full_contact

    return await can_view_full_contact(
        ctx.session, ctx.user, customer_id=customer.id, owner_id=customer.owner_id
    )


def _mask_mobile(mobile: str | None) -> str | None:
    """手机号脱敏。格式统一交给 `contact_util.mask_contact_value`，
    避免 AI 回答与页面上显示成两种样子。"""
    from app.modules.contact_util import mask_contact_value

    return mask_contact_value(mobile, "phone")


def _mask_email(email: str | None) -> str | None:
    from app.modules.contact_util import mask_contact_value

    return mask_contact_value(email, "email")


async def _scope(stmt, ctx: ToolContext, column, *, allow_unowned: bool = False):
    """与业务模块一致的数据范围过滤：Agent 不能绕过权限看数据。

    `department_and_sub` 会递归到下级部门，见 app/core/data_scope.py。

    `allow_unowned`：客户/线索的公海（无负责人）要显式放行——否则"问 Agent 找
    这个公海客户"搜不到，而业务接口按 id 又能看，用户会以为系统丢了数据。
    """
    owner_ids = await scoped_owner_ids(ctx.session, ctx.user)
    if owner_ids is None:
        return stmt
    cond = column.in_(owner_ids)
    if allow_unowned:
        cond = or_(cond, column.is_(None))
    return stmt.where(cond)


async def _ensure_in_scope(
    ctx: ToolContext,
    owner_column,
    pk_column,
    entity_id: int,
    label: str,
    *,
    allow_unowned: bool = False,
) -> None:
    """按 id 取详情的工具同样要过数据范围（与业务接口同一套纪律）。

    只给列表工具加过滤是不够的：改个 id 就能看别人的客户/订单/财务，
    等于数据范围形同虚设。越权与不存在返回同一种口径的错误，
    避免"探测 id"侧信道——不存在已在调用点先按 404 处理，走到这里
    还查不到就只剩越权一种可能。

    `allow_unowned`：客户/线索允许无负责人（公海 / 线索池），
    与业务接口同一口径——否则"问 Agent 查这个公海客户"和"自己在列表里翻"
    会得出两个答案，用户会以为系统丢了数据。**其余模块保持 False**：
    无归属的商机/订单/单据属于脏数据，不该因为"查不到负责人"就放行。
    """
    owner_ids = await scoped_owner_ids(ctx.session, ctx.user)
    if owner_ids is None:
        return
    cond = owner_column.in_(owner_ids)
    if allow_unowned:
        cond = or_(cond, owner_column.is_(None))
    stmt = select(pk_column).where(pk_column == entity_id, cond)
    hit = (await ctx.session.execute(stmt)).scalar_one_or_none()
    if hit is None:
        raise AppError(ErrorCode.DATA_SCOPE_DENIED, f"没有权限查看该{label}", 403)


# ------------------------------------------------------------------ L1 只读

@tool(
    "search_customers",
    "按关键词搜索客户，返回客户 id、名称、等级、负责人、最近跟进时间。",
    {
        "type": "object",
        "properties": {"keyword": {"type": "string", "description": "客户名称关键词，可留空"}},
    },
    "L1",
    permissions=('customer:view',),
)
async def search_customers(ctx: ToolContext, keyword: str = "") -> dict:
    stmt = select(Customer).where(Customer.deleted_at.is_(None))
    if keyword:
        stmt = stmt.where(Customer.name.ilike(f"%{keyword}%"))
    stmt = await _scope(
        stmt.order_by(Customer.id.desc()).limit(10),
        ctx,
        Customer.owner_id,
        allow_unowned=True,
    )
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
    permissions=('customer:view',),
)
async def get_customer_overview(ctx: ToolContext, customer_id: int) -> dict:
    """客户全貌：**逐模块**授权 + 逐块取数。

    修前的三处问题（§8.3）：
    1. 只验了"客户在不在我的数据范围"，随后按 customer_id 把联系人/商机/报价/
       订单**全取**——只有 `agent:use` 的人也能从 Agent 读到财务口径；
    2. 完整手机号直接进返回值，模型和确认卡都拿得到；
    3. 待回款用 `整单金额 − 已确认回款` 的 float 累加，还包含已取消订单。

    修法：
    - **只要求 `customer:view` 作为工具门槛**（网关那一层按 `ToolSpec.permissions`
      已经把住），块级权限在函数内部再各自判定——因为一个工具对应多个模块，
      网关只能回答"能不能用这个工具"，回答不了"块能不能给"；
    - **无权块一次查询都不发**，键根本不出现，只在 `unavailable_blocks` 里留说明，
      避免"空数组"被误读成"这个客户没有商机"；
    - 联系方式按可见性脱敏（见 `_may_see_full_contact`）。

    范围口径（2026-10-07 业务拍板后**已与页面完全对齐**）：逐块判对应模块的
    查看权限，再按**每个对象自己的负责人**收数据范围（下文的 `_scope`）——
    与 `/customers/{id}/orders` 等子资源接口、以及客户全貌页
    （`customer/router.py::customer_overview`）用的是同一套判据。

    为什么此前这里**刻意**不同（记下来，免得以后又改回去）：当时的顾虑是
    "客户交接之后，单据的负责人可能还是原来那个人，机械按 owner 过滤会把接手人的
    合法历史全挡掉"。这个顾虑**是真的**，但正确的修法不是"Agent 放宽可见性"，
    而是**交接时把单据搬干净** —— 2026-10-07 已实现（`customer/documents.py`）：
    日常转移、主管分配、批量转移、撞单裁定、公海领取都会把原负责人名下的
    商机／打样／报价／订单草稿／订单以及生成的文件一并改到新负责人名下。
    搬干净之后，"按单据负责人过滤"就不再挡历史了，两条路自然同口径，
    也不需要 Agent 开特例。

    （这也回答了审查 §8.3 那句"不能机械要求全部对象旧 owner 等于接手人而把合法
    历史资料全挡住"：现在不再挡，是因为**交接把 owner 改对了**，
    而不是读侧不看 owner。）
    """
    customer = await ctx.session.get(Customer, customer_id)
    if customer is None or customer.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "客户不存在", 404)
    # 公海客户（无负责人）可见：与业务接口同一口径
    await _ensure_in_scope(
        ctx, Customer.owner_id, Customer.id, customer_id, "客户", allow_unowned=True
    )

    unavailable: list[str] = []

    # ---- 联系人（customer:view；工具门槛已含，这里再做字段级判断）----
    # 联系人手机号此前无条件返回。规则尚未最终拍板（24-号文档 §0.3 第 3 条），
    # 这里先落**保守侧**：只有管理员/本客户负责人/该负责人的主管看得到完整值，
    # 其余可见人员只看脱敏值。
    contacts = (
        await ctx.session.execute(
            select(Contact).where(Contact.customer_id == customer_id, Contact.deleted_at.is_(None))
        )
    ).scalars().all()
    full_contact = await _may_see_full_contact(ctx, customer)

    result: dict = {
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
            {
                "id": c.id,
                "name": c.name,
                "title": c.title,
                "mobile": c.mobile if full_contact else _mask_mobile(c.mobile),
                "email": c.email if full_contact else _mask_email(c.email),
                "is_primary": c.is_primary,
            }
            for c in contacts
        ],
        "contact_masked": not full_contact,
    }

    # ---- 商机（opportunity:view）----
    if ctx.user.has("opportunity:view"):
        # 与客户页的"商机"标签同一口径：先判模块权限，再按**商机自己的负责人**
        # 收数据范围（见本函数开头关于"两条路口径"的说明）。
        opp_stmt = await _scope(
            select(Opportunity, OpportunityStage.name)
            .join(OpportunityStage, OpportunityStage.id == Opportunity.stage_id)
            .where(Opportunity.customer_id == customer_id, Opportunity.deleted_at.is_(None)),
            ctx,
            Opportunity.owner_id,
        )
        opportunities = (await ctx.session.execute(opp_stmt)).all()
        result["opportunities"] = [
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
        ]
    else:
        unavailable.append("商机（需要 opportunity:view）")

    # ---- 报价（quote:view）----
    if ctx.user.has("quote:view"):
        # 同商机：按报价自己的负责人收范围（与客户页的"报价"标签一致）
        quote_stmt = await _scope(
            select(Quote).where(Quote.customer_id == customer_id, Quote.deleted_at.is_(None)),
            ctx,
            Quote.owner_id,
        )
        quotes = (await ctx.session.execute(quote_stmt)).scalars().all()
        result["quotes"] = [
            {
                "id": q.id,
                "quote_no": q.quote_no,
                "status": QUOTE_STATUS_LABEL.get(q.status, q.status),
                "valid_until": q.valid_until.isoformat() if q.valid_until else None,
            }
            for q in quotes
        ]
    else:
        unavailable.append("报价（需要 quote:view）")

    # ---- 订单（order:view）----
    orders = []
    if ctx.user.has("order:view"):
        # 同商机/报价：按订单自己的负责人收范围（与客户页的"订单"标签一致）
        order_stmt = await _scope(
            select(SalesOrder).where(SalesOrder.customer_id == customer_id),
            ctx,
            SalesOrder.owner_id,
        )
        orders = (await ctx.session.execute(order_stmt)).scalars().all()
        result["orders"] = [
            {
                "id": o.id,
                "order_no": o.order_no,
                "status": ORDER_STATUS_LABEL.get(o.status, o.status),
                "total_amount": _money(o.total_amount),
                "delivery_date": o.delivery_date.isoformat() if o.delivery_date else None,
            }
            for o in orders
        ]
    else:
        unavailable.append("订单（需要 order:view）")

    # ---- 待回款（payment:view）----
    # 原实现无条件算，且用 float 逐单累加、把已取消订单也算进去。
    # 这里①没有 payment:view 就不算（连数字都不出现）；②**复用
    # `_receivables_by_currency`**（与应收汇总同一套 Decimal + 币种分组口径）；
    # ③订单集合走 `_active_order_ids`（排除取消单、按订单当前负责人过滤）。
    #
    # ⚠️ 这个字段仍然是**参考口径**（未取消订单的整单金额 − 已确认回款），
    # 不等于正式应收：正式应收以应收计划为准。口径混用正是 §8.4 点名的风险，
    # 所以把差别写进 `receivable_note` 一起交给模型。
    if ctx.user.has("payment:view"):
        # 按币种分别累加"未取消订单的整单金额"与"已确认回款"，**不跨币种相加**
        order_total: dict[str, Decimal] = {}
        for order in orders:
            if order.status == "cancelled":
                continue
            code = _currency_of(order.currency)
            order_total[code] = order_total.get(code, Decimal(0)) + Decimal(
                order.total_amount or 0
            )
        by_currency, _plans = await _receivables_by_currency(
            ctx, await _active_order_ids(ctx, customer_id=customer_id)
        )
        pending = {
            code: order_total.get(code, Decimal(0))
            - Decimal(str(by_currency.get(code, {}).get("received_amount", 0)))
            for code in sorted(set(order_total) | set(by_currency))
        }
        # 单币种给标量（既有调用方读的就是这个键）；多币种只给分组列表——
        # 合计数在多币种下必然错，宁可不给。
        if len(pending) <= 1:
            only_code = next(iter(pending), "CNY")
            result["pending_receivable_amount"] = float(round(pending.get(only_code, Decimal(0)), 2))
            result["pending_receivable_currency"] = only_code
        else:
            result["pending_receivable_by_currency"] = [
                {
                    "currency": code,
                    "order_total_amount": float(round(order_total.get(code, Decimal(0)), 2)),
                    "received_amount": float(
                        round(Decimal(str(by_currency.get(code, {}).get("received_amount", 0))), 2)
                    ),
                    "pending_amount": float(round(pending[code], 2)),
                }
                for code in sorted(pending)
            ]
        result["receivable_note"] = (
            "待回款为「未取消订单的整单金额 − 已确认回款」的参考值，不等于正式应收；"
            "正式应收以应收计划为准（可让「查应收与回款」按计划口径再看一次）"
        )
    else:
        unavailable.append("应收与回款（需要 payment:view）")

    if unavailable:
        result["unavailable_blocks"] = unavailable
    return result


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
    permissions=('opportunity:view',),
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
    permissions=('opportunity:view',),
)
async def get_opportunity_detail(ctx: ToolContext, opportunity_id: int) -> dict:
    opportunity = await ctx.session.get(Opportunity, opportunity_id)
    if opportunity is None or opportunity.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "商机不存在", 404)
    await _ensure_in_scope(ctx, Opportunity.owner_id, Opportunity.id, opportunity_id, "商机")
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
    permissions=('followup:view',),
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
    permissions=('product:view',),
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
    permissions=('product:view',),
)
async def calculate_price(
    ctx: ToolContext,
    sku_id: int,
    quantity: float,
    customer_id: int | None = None,
    quoted_price: float | None = None,
) -> dict:
    # ⚠️ **先过一遍普通接口那一份入参校验**（issue #14）。
    #
    # 从前这里把模型给的参数**直接**传给 `pricing_service.calculate_price`，
    # 于是同一个数字在两条路径上结果不同 —— 实测：
    #   数量 0  → 工具**放行**（算出 0 元的"建议价"，看着像正常结果）
    #   数量 -1 → 工具**放行**
    #   数量 1.5 / 拟报价 100.0 → `Decimal × float` 抛 **TypeError**（冒成 500）
    # 而普通接口 `/pricing/calculate` 对同样输入一律 40001「数量必须大于 0」。
    #
    # JSON Schema 与 Python 类型注解**都不能代替运行时校验**：
    # schema 里写的是 `"type": "number"`，模型完全可能给 0、负数或小数。
    # 复用 `PricingRequest` 才是"两条路径同一套边界"的唯一可靠做法 ——
    # 以后接口那边加了新约束，工具自动跟上，不会再各写一遍。
    from pydantic import ValidationError

    from app.modules.pricing.schema import PricingRequest

    try:
        payload = PricingRequest(
            sku_id=sku_id,
            quantity=quantity,
            customer_id=customer_id,
            quoted_price=quoted_price,
        )
    except ValidationError as exc:
        # Pydantic 的英文报错不能原样回给用户（Agent 会把这句话转述出去）
        bad = (exc.errors() or [{}])[0]
        field = str(bad.get("loc", ("",))[-1]) if bad.get("loc") else ""
        label = {"quantity": "数量", "quoted_price": "拟报价", "sku_id": "SKU"}.get(
            field, field or "入参"
        )
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"核价入参不合法：{label} 需要"
            + ("大于 0 的数字" if field in ("quantity", "quoted_price") else "合法的值"),
            422,
        ) from exc

    # 带客户核价时，客户必须在该用户数据范围内（A11：与普通界面同一纪律）
    if payload.customer_id is not None:
        await _ensure_in_scope(
            ctx, Customer.owner_id, Customer.id, payload.customer_id, "客户", allow_unowned=True
        )
    result = await pricing_service.calculate_price(
        ctx.session,
        sku_id=payload.sku_id,
        quantity=payload.quantity,
        customer_id=payload.customer_id,
        quoted_price=payload.quoted_price,
        role_codes=ctx.user.roles,
    )
    data = {
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
        "below_hard_floor": result["approval_triggers"].get("below_hard_floor", False),
        "warnings": result["warnings"],
    }
    # A11：Agent 与普通界面同一套脱敏——成本/保护价/利润只给价格管理员
    if not ctx.user.has("price:manage"):
        for key in ("base_cost", "minimum_price", "profit", "profit_rate"):
            data[key] = None
        data["warnings"] = [*data["warnings"], "成本、保护价与利润仅价格管理员可见"]
    return data


async def _active_order_ids(
    ctx: ToolContext, *, customer_id: int | None = None, order_id: int | None = None
) -> list[int]:
    """当前用户责任范围内、**未取消**的订单 id。

    口径与 `payment/service.visible_order_ids_stmt`（应收与回款的唯一范围来源）
    和 `analytics.receivable_stats`（排除取消单）保持一致：
    "计划与实收同范围、取消单不计"。三个取数入口（应收汇总、客户全貌待回款、
    订单详情财务块）共用它，避免又写出第四套范围判断。
    """
    from app.modules.payment import service as payment_service

    stmt = select(SalesOrder.id).where(SalesOrder.status != "cancelled")
    if customer_id is not None:
        stmt = stmt.where(SalesOrder.customer_id == customer_id)
    if order_id is not None:
        stmt = stmt.where(SalesOrder.id == order_id)
    visible = await payment_service.visible_order_ids_stmt(ctx.session, ctx.user)
    if visible is not None:
        stmt = stmt.where(SalesOrder.id.in_(visible))
    return list((await ctx.session.execute(stmt)).scalars().all())


def _currency_of(value: str | None) -> str:
    """币种归一：空值按人民币兜底（库里历史行的默认值就是 CNY）。"""
    return (value or "CNY").upper()


async def _receivable_scope_stmt(ctx: ToolContext, order_id: int | None = None):
    """应收范围过滤——**复用 payment 模块已有的责任范围子查询**。

    `payment/service.visible_order_ids_stmt` 就是"当前用户可见订单"的唯一实现
    （列表、详情、回款确认都用它），这里直接调用而不是再写一遍
    `SalesOrder.owner_id.in_(scoped_owner_ids)`：口径只有一份，才不会出现
    "应收页看不到、AI 却报得出来"这种两套真相。

    顺带修掉原先的 `SalesOrder.id.in_(owner_ids)`——它把**员工编号**当成了
    **订单编号**（§8.4：员工 10 自己的订单 700 应收 100 被漏掉，别人 id=10 的
    订单 500 被算进来）。
    """
    from app.modules.payment import service as payment_service

    stmt = select(ReceivablePlan).join(SalesOrder, SalesOrder.id == ReceivablePlan.order_id)
    # 已取消订单不产生正式应收（与 analytics.receivable_stats / payment_stats
    # 的既有规则一致：`SalesOrder.status != "cancelled"`）
    stmt = stmt.where(SalesOrder.status != "cancelled")
    visible = await payment_service.visible_order_ids_stmt(ctx.session, ctx.user)
    if visible is not None:
        stmt = stmt.where(ReceivablePlan.order_id.in_(visible))
    if order_id is not None:
        stmt = stmt.where(ReceivablePlan.order_id == order_id)
    return stmt


async def _receivables_by_currency(
    ctx: ToolContext, order_ids: list[int]
) -> tuple[dict[str, dict], list[ReceivablePlan]]:
    """按订单集合汇总应收与已确认回款，**按币种分组**。

    口径与 `analytics.service.receivable_stats` 完全一致（那页是责任口径：
    计划与实收都按订单当前负责人），差别只有两点，都是为了 AI 场景更安全：

    1. **按币种分组**：计划/回款的 `currency` 各自分组。直接把 USD 与 CNY
       相加会得出"200 元"这种没有币种的假数字，所以这里不提供跨币种合计；
    2. **Decimal 计算**：不经过 float，避免累加误差。
    """
    groups: dict[str, dict] = {}
    if not order_ids:
        return groups, []

    plans = (
        await ctx.session.execute(
            select(ReceivablePlan).where(
                ReceivablePlan.order_id.in_(order_ids),
                ReceivablePlan.status != "cancelled",
            )
        )
    ).scalars().all()

    plan_amount: dict[str, Decimal] = {}
    plan_count: dict[str, int] = {}
    for plan in plans:
        code = _currency_of(plan.currency)
        plan_amount[code] = plan_amount.get(code, Decimal(0)) + Decimal(plan.amount or 0)
        plan_count[code] = plan_count.get(code, 0) + 1

    received_rows = (
        await ctx.session.execute(
            select(
                PaymentRecord.currency,
                func.coalesce(func.sum(PaymentRecord.received_amount), 0),
            )
            .where(
                PaymentRecord.status == "confirmed",
                PaymentRecord.order_id.in_(order_ids),
            )
            .group_by(PaymentRecord.currency)
        )
    ).all()
    received_amount: dict[str, Decimal] = {}
    for code, total in received_rows:
        key = _currency_of(code)
        received_amount[key] = received_amount.get(key, Decimal(0)) + Decimal(total or 0)

    for code in sorted(set(plan_amount) | set(received_amount)):
        planned = plan_amount.get(code, Decimal(0))
        got = received_amount.get(code, Decimal(0))
        groups[code] = {
            "currency": code,
            "plan_count": plan_count.get(code, 0),
            "plan_amount": float(round(planned, 2)),
            "received_amount": float(round(got, 2)),
            "unreceived_amount": float(round(planned - got, 2)),
        }
    return groups, list(plans)


@tool(
    "get_receivables_summary",
    "汇总应收与回款情况：应收合计、已收、未收、逾期节点（按币种分组）。",
    {"type": "object", "properties": {"order_id": {"type": "integer"}}},
    "L1",
    permissions=('payment:view',),
)
async def get_receivables_summary(ctx: ToolContext, order_id: int | None = None) -> dict:
    """应收与回款汇总（责任口径：按订单**当前负责人**）。

    修前的两个错（§8.4）：
    1. `SalesOrder.id.in_(owner_ids)` 把员工编号当订单编号——既漏掉自己的应收，
       又读到"订单 id 恰好等于某员工编号"的别人的金额；
    2. 计划额与实收额之间没有币种分组，100 USD 与 100 CNY 会被相加成"200"。

    修法：范围走 `payment.visible_order_ids_stmt`（唯一口径）、排除取消订单、
    Decimal 计算、按币种分组。指定 `order_id` 时**先校验订单在不在责任范围内**，
    范围外返回受控的"无权/不可见"响应（不泄漏该订单是否存在之外的信息）。
    """
    from app.modules.payment import service as payment_service

    if order_id is not None:
        order = await ctx.session.get(SalesOrder, order_id)
        if order is None:
            raise AppError(ErrorCode.NOT_FOUND, "订单不存在", 404)
        # `allow_unowned=False`：无负责人的订单属于脏数据，不放行
        # （与 `payment.assert_order_visible` / `insights.receivable_risk` 同口径）
        await ensure_in_scope(
            ctx.session, ctx.user, owner_id=order.owner_id, label="订单"
        )
        if order.status == "cancelled":
            # 取消单没有正式应收。明确说出来，而不是回一堆 0 让人以为"收齐了"。
            return {
                "scope": {"order_id": order_id},
                "order_status": "cancelled",
                "order_status_label": ORDER_STATUS_LABEL.get(order.status, order.status),
                "groups": [],
                "overdue": [],
                "notice": "该订单已取消，按现有规则不产生正式应收",
            }
        order_ids = [order_id]
    else:
        visible = await payment_service.visible_order_ids_stmt(ctx.session, ctx.user)
        if visible is None:
            order_ids = list(
                (
                    await ctx.session.execute(
                        select(SalesOrder.id).where(SalesOrder.status != "cancelled")
                    )
                ).scalars().all()
            )
        else:
            order_ids = list(
                (
                    await ctx.session.execute(
                        select(SalesOrder.id).where(
                            SalesOrder.status != "cancelled",
                            SalesOrder.id.in_(visible),
                        )
                    )
                ).scalars().all()
            )

    groups, plans = await _receivables_by_currency(ctx, order_ids)

    # 逐节点未收金额：已确认回款要按**节点的**币种抵消，不能跨币种相减
    received_by_plan: dict[int, Decimal] = {}
    if plans:
        rows = (
            await ctx.session.execute(
                select(
                    PaymentRecord.receivable_plan_id,
                    func.coalesce(func.sum(PaymentRecord.received_amount), 0),
                )
                .where(
                    PaymentRecord.status == "confirmed",
                    PaymentRecord.receivable_plan_id.in_([p.id for p in plans]),
                )
                .group_by(PaymentRecord.receivable_plan_id)
            )
        ).all()
        received_by_plan = {int(pid): Decimal(total or 0) for pid, total in rows}

    overdue = [
        {
            "order_id": plan.order_id,
            "plan_name": plan.plan_name,
            "due_date": plan.due_date.isoformat() if plan.due_date else None,
            "currency": _currency_of(plan.currency),
            "remaining_amount": float(
                round(Decimal(plan.amount or 0) - received_by_plan.get(plan.id, Decimal(0)), 2)
            ),
        }
        for plan in plans
        if plan.status == "overdue"
    ]

    # 单币种时补一组顶层标量，方便模型直接引用与既有调用方复用；
    # 多币种时**故意不给**顶层合计——给了必然被误当成"总金额"相加。
    result: dict = {
        "scope": {"order_id": order_id},
        "currency_count": len(groups),
        "by_currency": list(groups.values()),
        "overdue": overdue,
        "overdue_count": len(overdue),
    }
    if len(groups) == 1:
        only = next(iter(groups.values()))
        result.update(
            {
                "currency": only["currency"],
                "plan_count": only["plan_count"],
                "plan_amount": only["plan_amount"],
                "received_amount": only["received_amount"],
                "unreceived_amount": only["unreceived_amount"],
            }
        )
    elif not groups:
        result.update(
            {
                "currency": None,
                "plan_count": 0,
                "plan_amount": 0.0,
                "received_amount": 0.0,
                "unreceived_amount": 0.0,
            }
        )
    else:
        result["notice"] = "存在多个币种的应收，已按币种分组；不同币种不做合计"
    return result


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
            "next_action": {"type": "string", "description": "下一动作；普通跟进必填"},
            "task_due_at": {"type": "string", "description": "下次跟进时间，含时区的 ISO 8601；普通跟进必填"},
            "exemption_reason": {"type": "string", "enum": ["customer_declined", "business_closed", "waiting_external"], "description": "免填原因：客户明确拒绝、业务关闭、等待外部固定节点。选择后不填下一动作和时间"},
        },
        "required": ["content"],
    },
    "L2",
    "customer",
    ("followup:create",),
)
async def create_followup(ctx: ToolContext, **kwargs) -> dict:
    from pydantic import ValidationError
    from app.modules.followup.schema import FollowUpCreate
    from app.modules.followup.mutations import create_manual_followup

    try:
        payload = FollowUpCreate(**{**kwargs, "request_key": f"agent:{ctx.action_id}" if ctx.action_id else None})
    except ValidationError as exc:
        raise AppError(ErrorCode.PARAM_ERROR, "请补齐下一动作和含时区的下次时间，或选择免填原因", 422) from exc
    followup, replayed = await create_manual_followup(ctx.session, ctx.user, payload, source="AGENT")
    # 与 AgentAction 执行状态一起提交，避免工具先提交导致确认重试重复写入。
    return {"followup_id": followup.id, "task_id": followup.next_task_id,
            "replayed": replayed, "message": "跟进已记录"}


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
    ("task:manage",),
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
    # 业务关联必须走**与普通建任务同一份**校验（C5-03，2026-10-10 修）。
    #
    # 此前这里直接 `Task(...)`，完全绕过 `normalize_task_refs`。实测同一个用户
    # （张三，数据范围 self）用这个工具能建出：
    #   - 关联**不存在**的客户/商机 id（返回 200，库里留下悬空引用）
    #   - 关联**别人业务员**的客户（越权）
    #   - 客户与商机**不是同一家**
    # 而同一批输入走 `POST /tasks` 分别被拒为 404 / 403 / 403。
    # AI 不该比人少一道关：它写的是同一张表、同一批字段。
    #
    # `normalize_task_refs` 只读写 `<kind>_id` 这几个键，所以把净化后的引用合并回
    # payload 即可（其余字段保持工具自己的语义：title / due_at / priority / task_type）。
    from app.modules.task.refs import normalize_task_refs

    ref_keys = ("customer_id", "lead_id", "opportunity_id", "quote_id", "order_id", "sample_id")
    refs = {k: payload[k] for k in ref_keys if payload.get(k) is not None}
    if refs:
        normalized, _ = await normalize_task_refs(ctx.session, ctx.user, refs, strict=True)
        payload = {**payload, **{k: normalized.get(k) for k in ref_keys}}

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
    # 只 flush 不 commit（C5-04，2026-10-10 修）。
    #
    # 原来这里 `await ctx.session.commit()` —— 它会把 Gateway 在
    # `execute_action` 里持有的**动作行锁提前释放**，而此刻动作状态还没改成
    # `executed`。受控并发交错下两个请求都读到「待确认」，各自执行一次写动作
    # （同一张确认卡建出两条任务）。工具成功后如果执行记录那一步再失败，
    # 还多一个「业务已提交、动作未完成」的窗口。
    #
    # 现在业务数据、动作状态、审计、执行记录**由 Gateway 一次提交**：
    # 要么全成、要么全不成。这也与项目既有约定一致 ——
    # `write_audit` 的文档就写着「不 commit，由调用方的事务统一提交」，
    # 这 5 个工具自己 commit 才是异类。
    await ctx.session.flush()
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
    ("opportunity:manage",),
)
async def update_opportunity_next_action(
    ctx: ToolContext, opportunity_id: int, next_action: str
) -> dict:
    opportunity = await ctx.session.get(Opportunity, opportunity_id)
    if opportunity is None or opportunity.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "商机不存在", 404)
    # 与同文件其它商机工具同一纪律（get_opportunity_detail 就是这么做的）：
    # 只判存在的话，改个 id 就能把别人商机的"下一步动作"改掉。
    await _ensure_in_scope(ctx, Opportunity.owner_id, Opportunity.id, opportunity_id, "商机")
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
    # 只 flush 不 commit（C5-04，2026-10-10 修）。
    #
    # 原来这里 `await ctx.session.commit()` —— 它会把 Gateway 在
    # `execute_action` 里持有的**动作行锁提前释放**，而此刻动作状态还没改成
    # `executed`。受控并发交错下两个请求都读到「待确认」，各自执行一次写动作
    # （同一张确认卡建出两条任务）。工具成功后如果执行记录那一步再失败，
    # 还多一个「业务已提交、动作未完成」的窗口。
    #
    # 现在业务数据、动作状态、审计、执行记录**由 Gateway 一次提交**：
    # 要么全成、要么全不成。这也与项目既有约定一致 ——
    # `write_audit` 的文档就写着「不 commit，由调用方的事务统一提交」，
    # 这 5 个工具自己 commit 才是异类。
    await ctx.session.flush()
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
    ("quote:manage",),
)
async def request_quote_approval(
    ctx: ToolContext, quote_version_id: int, reason: str | None = None
) -> dict:
    from app.modules.quote import service as quote_service

    # 与 HTTP 入口（报价的提交审批）同一口径：下面的 submit_for_approval 不带范围参数，
    # 范围只能在这一层把——否则改个 id 就能把别人的报价版本提交审批（还会触发审批通知）。
    version = await quote_service.get_visible_version(ctx.session, ctx.user, quote_version_id)
    quote = await quote_service.get_visible_quote(ctx.session, ctx.user, version.quote_id)
    instance, required = await quote_service.submit_for_approval(
        ctx.session,
        quote=quote,
        version=version,
        applicant_id=ctx.user.id,
        user_roles=ctx.user.roles,
        reason=reason,
        can_see_floor=ctx.user.has("price:manage"),
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
    # 只 flush 不 commit（C5-04，2026-10-10 修）。
    #
    # 原来这里 `await ctx.session.commit()` —— 它会把 Gateway 在
    # `execute_action` 里持有的**动作行锁提前释放**，而此刻动作状态还没改成
    # `executed`。受控并发交错下两个请求都读到「待确认」，各自执行一次写动作
    # （同一张确认卡建出两条任务）。工具成功后如果执行记录那一步再失败，
    # 还多一个「业务已提交、动作未完成」的窗口。
    #
    # 现在业务数据、动作状态、审计、执行记录**由 Gateway 一次提交**：
    # 要么全成、要么全不成。这也与项目既有约定一致 ——
    # `write_audit` 的文档就写着「不 commit，由调用方的事务统一提交」，
    # 这 5 个工具自己 commit 才是异类。
    await ctx.session.flush()
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
    permissions=('lead:view',),
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
    stmt = await _scope(
        stmt.order_by(Lead.id.desc()).limit(15),
        ctx,
        Lead.owner_id,
        allow_unowned=True,
    )

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
    permissions=('customer:view',),
)
async def get_contact(ctx: ToolContext, contact_id: int) -> dict:
    contact = await ctx.session.get(Contact, contact_id)
    if contact is None or contact.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "联系人不存在", 404)
    # 联系人没有 owner，范围跟所属客户走
    await _ensure_in_scope(
        ctx,
        Customer.owner_id,
        Customer.id,
        contact.customer_id,
        "客户",
        allow_unowned=True,
    )
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
    permissions=('product:view',),
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
    permissions=('product:view',),
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
    permissions=('product:view',),
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
    permissions=('order:view',),
)
async def get_order(ctx: ToolContext, order_id: int) -> dict:
    from app.modules.order import service as order_service
    from app.modules.payment import service as payment_service

    order = await ctx.session.get(SalesOrder, order_id)
    if order is None:
        raise AppError(ErrorCode.NOT_FOUND, "订单不存在", 404)
    await _ensure_in_scope(ctx, SalesOrder.owner_id, SalesOrder.id, order_id, "订单")

    items = (
        await ctx.session.execute(
            select(SalesOrderItem).where(SalesOrderItem.order_id == order_id)
        )
    ).scalars().all()
    # 应收/回款块要 `payment:view`：`order:view` 回答的是"能不能看这张单"，
    # 回答不了"能不能看这家公司的钱"。财务块的取数也要一起省掉——
    # 无权还去查一遍，等于把"有没有这笔钱"通过耗时/日志漏出去（§8.3 同一纪律）。
    finance_ok = ctx.user.has("payment:view")
    plans = []
    if finance_ok:
        plans = (
            await ctx.session.execute(
                select(ReceivablePlan).where(
                    ReceivablePlan.order_id == order_id,
                    ReceivablePlan.status != "cancelled",
                )
            )
        ).scalars().all()
    scope = await order_service.order_context(ctx.session, [order])
    result = {
        "id": order.id,
        "order_no": order.order_no,
        "customer_name": scope["customers"].get(order.customer_id),
        "total_amount": _money(order.total_amount),
        "currency": _currency_of(order.currency),
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
    }
    if finance_ok:
        result["receivables"] = [
            {
                "plan_name": plan.plan_name,
                "due_date": plan.due_date.isoformat() if plan.due_date else None,
                "amount": _money(plan.amount),
                "currency": _currency_of(plan.currency),
                "status": plan.status,
            }
            for plan in plans
        ]
        result["received_amount"] = _money(scope["received"].get(order.id, 0))
        result["received_currency"] = _currency_of(order.currency)
        result["finance"] = await payment_service.order_finance_summary(ctx.session, order.id)
    else:
        result["unavailable_blocks"] = ["应收与回款（需要 payment:view）"]
    return result


@tool(
    "create_quote_draft",
    "从商机生成报价草稿（按核价建议价自动带入明细）。生成的是**草稿**，不会自动发送或提交审批。需要用户确认后才会写入。opportunity_id 必填（D8 口径：报价必须挂商机）：用户没指明商机时，先向用户确认用哪个商机，不要猜测商机 id。",
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
    ("quote:manage",),
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
    # 只 flush 不 commit（C5-04，2026-10-10 修）。
    #
    # 原来这里 `await ctx.session.commit()` —— 它会把 Gateway 在
    # `execute_action` 里持有的**动作行锁提前释放**，而此刻动作状态还没改成
    # `executed`。受控并发交错下两个请求都读到「待确认」，各自执行一次写动作
    # （同一张确认卡建出两条任务）。工具成功后如果执行记录那一步再失败，
    # 还多一个「业务已提交、动作未完成」的窗口。
    #
    # 现在业务数据、动作状态、审计、执行记录**由 Gateway 一次提交**：
    # 要么全成、要么全不成。这也与项目既有约定一致 ——
    # `write_audit` 的文档就写着「不 commit，由调用方的事务统一提交」，
    # 这 5 个工具自己 commit 才是异类。
    await ctx.session.flush()
    payload["message"] = "报价草稿已生成（未发送、未提交审批）"
    return payload


@tool(
    "create_quote_version",
    "在已有报价单上新建一个版本（复制上一版明细，用于改价后再谈）。旧版本不会被覆盖。需要用户确认后才会写入。",
    {"type": "object", "properties": {"quote_id": {"type": "integer"}}, "required": ["quote_id"]},
    "L2",
    "quote",
    ("quote:manage",),
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
    # 只 flush 不 commit（C5-04，2026-10-10 修）。
    #
    # 原来这里 `await ctx.session.commit()` —— 它会把 Gateway 在
    # `execute_action` 里持有的**动作行锁提前释放**，而此刻动作状态还没改成
    # `executed`。受控并发交错下两个请求都读到「待确认」，各自执行一次写动作
    # （同一张确认卡建出两条任务）。工具成功后如果执行记录那一步再失败，
    # 还多一个「业务已提交、动作未完成」的窗口。
    #
    # 现在业务数据、动作状态、审计、执行记录**由 Gateway 一次提交**：
    # 要么全成、要么全不成。这也与项目既有约定一致 ——
    # `write_audit` 的文档就写着「不 commit，由调用方的事务统一提交」，
    # 这 5 个工具自己 commit 才是异类。
    await ctx.session.flush()
    return {
        "quote_id": quote_id,
        "version_id": version.id,
        "version_no": version.version_no,
        "total_amount": _money(version.total_amount),
        "message": f"已新建 V{version.version_no}，明细复制自上一版",
    }
