"""审批规则引擎（设计稿 `_6` 的国内业务版）。

设计稿画的是外贸口径（中信保、美元、OA 账期、Tier 客户）；本项目已确认
**只做国内**，所以条件字段全部换成国内业务的数据源，但"多条件免审 +
极速通道 + 异常路由加签 + 沙盒试算 + 版本管理"这套结构原样保留。

三类规则（kind）与生效语义：

=================  ===========================================================
kind               生效动作（条件**全部命中**时）
=================  ===========================================================
auto_pass          免审：提交即通过，不进审批流（留一条带规则痕迹的闭环审批单）
express            极速通道：跳过金额分档的高层级，一律由第一级（主管）审批
exception_route    异常加签：按金额分档走，末尾追加一个会签节点（一票否决）
=================  ===========================================================

多条规则按 `priority` 升序求值，**第一条命中的生效**（沙盒会把每条规则的
命中明细都列出来）。求值对象是「已发布且启用」的规则——草稿要发布才生效，
`enabled` 是立即生效的运维开关。
"""

import re
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timebase import today_business
from app.modules.approval.model import ApprovalRule
from app.modules.payment.model import ReceivablePlan
from app.modules.quote.model import Quote, QuoteItem, QuoteVersion
from app.modules.order.model import SalesOrder

# ---------------------------------------------------------------- 条件字段目录
# 前端编辑器的字段/操作符下拉、沙盒的展示文案都以这份目录为准（单一事实来源）。
CONTEXT_FIELDS = [
    {"field": "total_amount", "label": "报价总金额", "value_type": "number", "unit": "元",
     "ops": ["gte", "lte"], "hint": "人民币口径，外币按快照汇率折算"},
    {"field": "gross_margin", "label": "综合毛利率", "value_type": "number", "unit": "%",
     "ops": ["gte", "lte"], "hint": "整单（含运费）的加权毛利率"},
    {"field": "min_item_margin", "label": "最低明细毛利率", "value_type": "number", "unit": "%",
     "ops": ["gte", "lte"], "hint": "所有明细里最差一行的毛利率"},
    {"field": "customer_level", "label": "客户等级", "value_type": "string", "unit": "",
     "ops": ["in", "eq"], "hint": "客户档案上的等级（A/B/C/D）"},
    {"field": "customer_has_overdue", "label": "客户有逾期应收", "value_type": "bool", "unit": "",
     "ops": ["eq"], "hint": "该客户名下存在逾期状态的应收计划"},
    {"field": "concession_amount", "label": "较上一版让价金额", "value_type": "number", "unit": "元",
     "ops": ["gte", "lte"], "hint": "上一版本总额 − 本版本总额，无上一版按 0 算"},
    {"field": "payment_terms_days", "label": "账期天数", "value_type": "number", "unit": "天",
     "ops": ["gte", "lte"], "hint": "从付款条件里识别的天数，识别不到按 0（现款）"},
]

OPS_LABEL = {"gte": "≥", "lte": "≤", "eq": "=", "in": "属于"}

VALID_KINDS = ("auto_pass", "express", "exception_route")


@dataclass
class RuleDecision:
    """一次命中的完整痕迹：谁命中了、命中在哪些条件、上下文是什么。"""

    rule_id: int
    rule_name: str
    kind: str
    version_no: int
    action: dict
    context: dict = field(default_factory=dict)
    matched: list = field(default_factory=list)

    def trace(self) -> dict:
        """写进 approval_instances.summary 的痕迹（要能回答"当时按哪版规则走的"）。"""
        return {
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "kind": self.kind,
            "rule_version_no": self.version_no,
            "matched_conditions": self.matched,
            "context": {k: v for k, v in self.context.items() if not k.startswith("_")},
        }


def _fmt(value) -> str:
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        return f"{value:,.2f}"
    return str(value)


def evaluate_conditions(conditions: list[dict], ctx: dict) -> tuple[bool, list[dict]]:
    """纯函数求值：单条规则内条件 AND。返回 (是否全中, 每条条件的命中明细)。

    单独拆出来是为了沙盒与离线测试能直接调，不用碰数据库。
    """
    detail: list[dict] = []
    all_hit = True
    for cond in conditions or []:
        fld = cond.get("field")
        op = cond.get("op", "gte")
        expected = cond.get("value")
        actual = ctx.get(fld)
        meta = next((f for f in CONTEXT_FIELDS if f["field"] == fld), None)
        if meta is None or actual is None:
            all_hit = False
            detail.append({
                "field": fld, "label": (meta or {}).get("label", fld),
                "op": op, "value": expected, "actual": actual, "hit": False,
                "note": "条件字段不存在或该单缺少此数据" if meta is None else "该单此字段为空，视为未命中",
            })
            continue
        try:
            if op == "gte":
                hit = actual >= expected
            elif op == "lte":
                hit = actual <= expected
            elif op == "eq":
                hit = actual == expected
            elif op == "in":
                values = expected if isinstance(expected, list) else [expected]
                hit = actual in values
            else:
                hit = False
        except TypeError:
            hit = False
        all_hit = all_hit and hit
        detail.append({
            "field": fld, "label": meta["label"], "op": op, "value": expected,
            "actual": actual, "hit": hit,
        })
    return all_hit, detail


def _cond_detail_pretty(detail: list[dict]) -> list[dict]:
    """给前端/留痕用的可读版本：≥ 25%、≤ 100000 元 这种。"""
    pretty = []
    for d in detail:
        meta = next((f for f in CONTEXT_FIELDS if f["field"] == d["field"]), None)
        unit = (meta or {}).get("unit", "")
        pretty.append({
            **d,
            "value_label": f"{OPS_LABEL.get(d['op'], d['op'])} {_fmt(d['value'])}{unit}",
            "actual_label": f"{_fmt(d['actual'])}{unit}" if d["actual"] is not None else "无数据",
        })
    return pretty


def payment_terms_days(text: str | None) -> int:
    """从付款条件文本里识别账期天数（如"账期60天"/"月结30"），识别不到按 0。"""
    if not text:
        return 0
    match = re.search(r"(\d{1,3})\s*天?", str(text))
    return int(match.group(1)) if match else 0


async def build_context(
    session: AsyncSession,
    *,
    quote: Quote,
    version: QuoteVersion,
    items: list[QuoteItem],
    fx: Decimal | None,
) -> dict:
    """把一张报价单翻译成条件求值可用的上下文（全部国内口径、人民币）。"""
    # 函数内导入：`quote.service` 在提交审批时会 import 本模块，
    # 模块级互相导入会成环。这里只要两个纯函数，没有初始化副作用。
    from app.modules.quote import service as quote_service

    foreign = (version.currency or "CNY").upper() != "CNY" and fx and fx > 0

    def to_cny(amount: Decimal) -> Decimal:
        """把**计价币种**的金额折成人民币。只用于报价侧的金额。"""
        return amount * fx if foreign else amount

    def cny_as_is(amount: Decimal) -> Decimal:
        """人民币金额原样返回。

        ⚠️ 成本类快照（`cost_snapshot` / `logistics_cost_snapshot`）**存的就是
        人民币**（见 `build_item_snapshot`：`cost_snapshot=result["cost"]["goods_cost"]`，
        从未折成计价币种）。从前这里对它们也套了 `to_cny`，外币单会把人民币成本
        **再乘一次汇率** —— 成本凭空放大，毛利率被算成一个毫无意义的数
        （2026-10-09 审查实测指出）。
        """
        return amount

    total_cny = float(to_cny(version.total_amount or Decimal(0)))
    prices = [to_cny(i.quoted_price) for i in items]
    # 产品核价成本逐版取口径（2026-10-09「产品价格与运费分离」）：
    # 新报价的运费由客户全额承担、公司原额代收代付，**不进产品利润**。
    # 判据只有 `item_unit_product_cost` 一份（统一口径后恒为"不含运费"）。
    includes_freight = quote_service.version_logistics_in_base_cost(version)
    unit_costs = [
        cny_as_is(quote_service.item_unit_product_cost(i, includes_freight=includes_freight))
        for i in items
    ]
    # ---- 整单毛利率：收入与成本**都必须按数量汇总**（2026-10-09 修）----
    #
    # 从前这里是 `sum(prices)` 与 `sum(costs)` —— 那是"**单价之和**"，却拿去减
    # "**整单**运费"，两个量纲混着算。审查实测：单价 100、成本 80、数量 100、
    # 运费 0 时毛利率 20%；只把运费改成 300，毛利率变成 **140%**
    # （内部是 200−300=−100，再与 80 比）。依赖毛利率的免审与审批规则会误判。
    #
    # 现在：成本 = Σ(单件成本 × 数量)；收入 = 整单总额 − 代收运费。
    # 用整单总额而不是 Σ(单价×数量)，是为了**保留折扣与其他费用**的处理
    # （`total_amount = subtotal + charge + discount`，只减运费即可得到非运费收入）。
    total_cost = sum(
        (c * (i.quantity or Decimal(0)) for c, i in zip(unit_costs, items)), Decimal(0)
    )
    # 运费是替客户转交给承运商的钱，不是公司的收入 —— 收入侧同样要剔除，
    # 否则"运费收得多"会被当成高毛利。
    revenue_for_margin = to_cny(version.total_amount or Decimal(0)) - to_cny(
        version.logistics_amount or Decimal(0)
    )
    gross_margin = (
        float((revenue_for_margin - total_cost) / revenue_for_margin * 100)
        if revenue_for_margin
        else 0.0
    )
    # 单件毛利率仍是"单件对单件"，不受上面的量纲修正影响
    item_margins = [
        float((p - c) / p * 100) for p, c in zip(prices, unit_costs) if p
    ]
    min_item_margin = min(item_margins) if item_margins else 0.0

    customer_id = quote.customer_id

    prev_version = (
        await session.execute(
            select(QuoteVersion)
            .where(
                QuoteVersion.quote_id == quote.id,
                QuoteVersion.version_no < version.version_no,
            )
            .order_by(QuoteVersion.version_no.desc())
            .limit(1)
        )
    ).scalars().first()
    concession = 0.0
    if prev_version is not None:
        concession = max(
            0.0, float((prev_version.total_amount or 0) - (version.total_amount or 0))
        )

    overdue_exists = (
        await session.execute(
            select(ReceivablePlan.id)
            .join(SalesOrder, SalesOrder.id == ReceivablePlan.order_id)
            .where(SalesOrder.customer_id == customer_id, ReceivablePlan.status == "overdue")
            .limit(1)
        )
    ).scalar_one_or_none()

    from app.modules.customer.model import Customer

    customer_row = await session.get(Customer, customer_id) if customer_id else None

    return {
        "total_amount": total_cny,
        "gross_margin": round(gross_margin, 2),
        "min_item_margin": round(min_item_margin, 2),
        "customer_level": (customer_row.level if customer_row else None) or "",
        "customer_has_overdue": overdue_exists is not None,
        "concession_amount": concession,
        "payment_terms_days": payment_terms_days(version.payment_terms),
        # 展示辅助（不参与求值，沙盒直接展示）
        "_quote_no": quote.quote_no,
        "_version_no": version.version_no,
        "_customer_name": customer_row.name if customer_row else None,
        # 展示辅助（不参与求值，沙盒直接展示）。原来用 `date.today()` —— 那跟着
        # **宿主机时区**走，同一套规则在 UTC 机器和北京机器上会显示不同的"今天"。
        "_today": today_business().isoformat(),
    }


async def load_active_rules(session: AsyncSession) -> list[tuple[ApprovalRule, dict]]:
    """取「已发布且启用」的规则及其**已发布版本快照**，按优先级排序。

    求值永远用发布时的快照，草稿改了没发布不影响线上。
    """
    rules = (
        await session.execute(
            select(ApprovalRule)
            .where(ApprovalRule.enabled.is_(True), ApprovalRule.published_version_no > 0)
            .order_by(ApprovalRule.priority.asc(), ApprovalRule.id.asc())
        )
    ).scalars().all()
    from app.modules.approval.model import ApprovalRuleVersion

    result: list[tuple[ApprovalRule, dict]] = []
    for rule in rules:
        snapshot = (
            await session.execute(
                select(ApprovalRuleVersion)
                .where(
                    ApprovalRuleVersion.rule_id == rule.id,
                    ApprovalRuleVersion.version_no == rule.published_version_no,
                )
                .limit(1)
            )
        ).scalars().first()
        if snapshot is not None:
            result.append((rule, snapshot.payload))
    return result


async def route(
    session: AsyncSession,
    *,
    quote: Quote,
    version: QuoteVersion,
    items: list[QuoteItem],
    fx: Decimal | None,
) -> RuleDecision | None:
    """提交审批时的路由判定：返回第一条命中的规则；没有规则命中返回 None（走原金额分档）。"""
    active = await load_active_rules(session)
    if not active:
        return None
    ctx = await build_context(session, quote=quote, version=version, items=items, fx=fx)
    for rule, payload in active:
        matched, detail = evaluate_conditions(payload.get("conditions") or [], ctx)
        if matched:
            return RuleDecision(
                rule_id=rule.id,
                rule_name=payload.get("name") or rule.name,
                kind=payload.get("kind") or rule.kind,
                version_no=rule.published_version_no,
                action=payload.get("action") or {},
                context=ctx,
                matched=_cond_detail_pretty(detail),
            )
    return None


def effect_summary(kind: str, action: dict) -> str:
    """一句话说明命中后果（审批记录/沙盒共用）。"""
    if kind == "auto_pass":
        return "免审：提交即通过，无需人工审批"
    if kind == "express":
        return "极速通道：跳过金额分档高层级，由第一级（主管）直接审批"
    if kind == "exception_route":
        label = action.get("add_node_label") or "财务会签"
        roles = "、".join(action.get("add_node_role_codes") or ["finance"])
        return f"异常加签：正常分档审批后，追加「{label}」（{roles}）会签，一票否决"
    return kind


def serialize_rule(rule: ApprovalRule, *, has_draft_changes: bool | None = None) -> dict:
    data = {
        "id": rule.id,
        "name": rule.name,
        "kind": rule.kind,
        "priority": rule.priority,
        "enabled": rule.enabled,
        "conditions": rule.conditions,
        "action": rule.action,
        "description": rule.description,
        "published_version_no": rule.published_version_no,
        "published_at": rule.published_at,
        "updated_at": rule.updated_at,
        "effect": effect_summary(rule.kind, rule.action),
        "conditions_pretty": _cond_detail_pretty(
            [{"field": c.get("field"), "op": c.get("op"), "value": c.get("value"), "actual": None}
             for c in rule.conditions or []]
        ),
    }
    if has_draft_changes is not None:
        data["has_draft_changes"] = has_draft_changes
    return data
