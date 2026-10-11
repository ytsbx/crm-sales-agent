"""客户阶段自动推导（领导六阶段口径）：字段是**算出来的**，不是让人填的。

设计原则（需求对齐时定下的）：
- 阶段 = 该客户目前推进到的最深处，由客观事实（订单/打样/报价的数量）推导；
- 规则集中在 `derive_stage` 一个纯函数里，列表/分布统计共用同一份，不会漂移；
- 阈值想调（比如"3 单才算稳定复购"）只改下面两个常量；
- 客户身上的阶段永远不在表单里出现——满足"不要重复做同一件事"的验收标准。

事实口径：
- 订单：status != cancelled（取消单不算成交）
- 稳定复购：只看最近 12 个月；同一商机下拆成多张订单只算一个采购项目，
  没有关联商机的订单才按订单本身计数
- 打样：sample_requests.status != rejected（被驳回的打样不算推进）
- 报价：未删除，且有正式发送/客户接受事实；草稿与内部审批不算对客报价

六阶段从浅到深：了解 → 报价 → 打样 → 首单 → 返单 → 稳定复购。
"""

from datetime import timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timebase import now_business

STAGE_UNDERSTANDING = "understanding"
STAGE_QUOTE = "quote"
STAGE_SAMPLE = "sample"
STAGE_FIRST_ORDER = "first_order"
STAGE_REPEAT = "repeat"
STAGE_STABLE = "stable"

#: 从浅到深，分布统计按这个顺序输出
STAGE_LABELS: dict[str, str] = {
    STAGE_UNDERSTANDING: "了解",
    STAGE_QUOTE: "报价",
    STAGE_SAMPLE: "打样",
    STAGE_FIRST_ORDER: "首单",
    STAGE_REPEAT: "返单",
    STAGE_STABLE: "稳定复购",
}

#: 第 2 单算"返单"，第 3 单起算"稳定复购"——业务口径变了改这里
REPEAT_ORDER_THRESHOLD = 2
STABLE_ORDER_THRESHOLD = 3
REPURCHASE_LOOKBACK_DAYS = 365


def repurchase_project_key(opportunity_id: int | None, order_id: int) -> str:
    """Return the stable key used to count one purchasing project once.

    Split orders keep the same opportunity key; legacy orders without an
    opportunity remain individually countable.
    """
    return f"opportunity:{opportunity_id}" if opportunity_id else f"order:{order_id}"


def derive_stage(order_count: int, sample_count: int, quote_count: int) -> str:
    """由事实数量推导客户阶段。纯函数，不查库。"""
    if order_count >= STABLE_ORDER_THRESHOLD:
        return STAGE_STABLE
    if order_count >= REPEAT_ORDER_THRESHOLD:
        return STAGE_REPEAT
    if order_count >= 1:
        return STAGE_FIRST_ORDER
    if sample_count >= 1:
        return STAGE_SAMPLE
    if quote_count >= 1:
        return STAGE_QUOTE
    return STAGE_UNDERSTANDING


async def stage_counts_map(
    session: AsyncSession, customer_ids: list[int]
) -> dict[int, tuple[int, int, int]]:
    """一批客户的三项事实计数（订单/打样/报价），返回 {customer_id: (单, 样, 报)}。

    三条分组查询代替逐客户子查询：列表页一页 20 个客户也只花 3 条 SQL。
    """
    if not customer_ids:
        return {}

    from app.modules.order.model import SalesOrder
    from app.modules.quote.model import Quote, QuoteVersion
    from app.modules.sample.model import SampleRequest

    counts: dict[int, list[int]] = {cid: [0, 0, 0] for cid in customer_ids}

    # 不能直接 count(*)：同一采购项目可能拆成多张订单。优先用商机作为
    # 项目标识；没有商机的历史订单才退回订单自身。时间窗口与客户阶段的
    # “最近 12 个月”口径集中在这里，列表和分布统计共用。
    order_rows = await session.execute(
        select(SalesOrder.customer_id, SalesOrder.opportunity_id, SalesOrder.id)
        .where(
            SalesOrder.customer_id.in_(customer_ids),
            SalesOrder.status != "cancelled",
            SalesOrder.created_at >= now_business() - timedelta(days=REPURCHASE_LOOKBACK_DAYS),
        )
    )
    project_ids: dict[int, set[str]] = {cid: set() for cid in customer_ids}
    for cid, opportunity_id, order_id in order_rows:
        project_ids[cid].add(repurchase_project_key(opportunity_id, order_id))
    for cid, projects in project_ids.items():
        counts[cid][0] = len(projects)

    sample_rows = await session.execute(
        select(SampleRequest.customer_id, func.count())
        .where(
            SampleRequest.customer_id.in_(customer_ids),
            SampleRequest.status != "rejected",
        )
        .group_by(SampleRequest.customer_id)
    )
    for cid, n in sample_rows:
        counts[cid][1] = int(n)

    quote_rows = await session.execute(
        select(Quote.customer_id, func.count())
        .where(
            Quote.customer_id.in_(customer_ids),
            Quote.deleted_at.is_(None),
            or_(Quote.status.in_(("sent", "accepted")),
                select(QuoteVersion.id).where(QuoteVersion.quote_id == Quote.id,
                                              QuoteVersion.sent_at.is_not(None)).exists()),
        )
        .group_by(Quote.customer_id)
    )
    for cid, n in quote_rows:
        counts[cid][2] = int(n)

    return {cid: (v[0], v[1], v[2]) for cid, v in counts.items()}


async def historical_order_counts_map(
    session: AsyncSession, customer_ids: list[int]
) -> dict[int, int]:
    """客户历史有效订单数，用来单独展示合作历史，不参与最近复购阶段。"""
    if not customer_ids:
        return {}
    from app.modules.order.model import SalesOrder

    rows = await session.execute(
        select(SalesOrder.customer_id, func.count())
        .where(
            SalesOrder.customer_id.in_(customer_ids),
            SalesOrder.status != "cancelled",
        )
        .group_by(SalesOrder.customer_id)
    )
    return {int(cid): int(count) for cid, count in rows}


async def current_opportunity_counts_map(
    session: AsyncSession, customer_ids: list[int]
) -> dict[int, int]:
    """当前开放商机数，单独展示，避免和客户合作阶段混成一个字段。"""
    if not customer_ids:
        return {}
    from app.modules.opportunity.model import Opportunity

    rows = await session.execute(
        select(Opportunity.customer_id, func.count())
        .where(
            Opportunity.customer_id.in_(customer_ids),
            Opportunity.status == "open",
            Opportunity.deleted_at.is_(None),
        )
        .group_by(Opportunity.customer_id)
    )
    return {int(cid): int(count) for cid, count in rows}


async def stage_distribution(
    session: AsyncSession, customer_ids: list[int]
) -> list[dict]:
    """六阶段分布统计：[{stage, label, count}]，顺序从了解到稳定复购。"""
    from collections import Counter

    counts = await stage_counts_map(session, customer_ids)
    dist = Counter(derive_stage(*v) for v in counts.values())
    return [
        {"stage": stage, "label": label, "count": dist.get(stage, 0)}
        for stage, label in STAGE_LABELS.items()
    ]
