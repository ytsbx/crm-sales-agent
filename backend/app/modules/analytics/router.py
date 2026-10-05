"""工作台与数据分析接口。"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from fastapi import Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok
from app.modules.analytics import service as svc
from app.modules.analytics import targets as targets_svc
from app.modules.analytics import usage as usage_svc

router = APIRouter(tags=["Analytics"])


class TimingReport(BaseModel):
    """前端上报一次操作的耗时（场景18）。"""

    operation: str
    duration_ms: int
    business_type: str | None = None
    business_id: int | None = None
    typed_fields: int = 0
    rework_count: int = 0


@router.post("/usage/timings")
async def report_operation_timing(
    payload: TimingReport,
    user: CurrentUser = Depends(require_permission("customer:view")),
    session: AsyncSession = Depends(get_db),
):
    """记一条操作耗时。

    **只有前端知道用户真正花了多久**（服务端看到的只是单据落库时间，那是流程跨度）——
    所以这里由前端上报，服务端只做白名单与合理区间校验，不替用户猜时间。
    """
    row = await usage_svc.record_timing(
        session,
        user=user,
        operation=payload.operation,
        duration_ms=payload.duration_ms,
        business_type=payload.business_type,
        business_id=payload.business_id,
        typed_fields=payload.typed_fields,
        rework_count=payload.rework_count,
    )
    await session.commit()
    return ok(usage_svc.serialize_timing(row), "已记录")


@router.get("/usage/timings/summary")
async def operation_timing_summary(
    days: int = Query(30, ge=1, le=365),
    user: CurrentUser = Depends(require_permission("customer:view")),
    session: AsyncSession = Depends(get_db),
):
    """按流程聚合耗时：给"我们比 Excel 快多少"提供可核对的数字。"""
    return ok(await usage_svc.timing_summary(session, user=user, days=days))
class SalesTargetUpsert(BaseModel):
    period: str
    user_id: int | None = None
    # 团队目标（文档 §六 :121）：与 user_id 互斥——指定部门就是团队目标
    department_id: int | None = None
    new_customer_target: int = 0
    sales_target: float = 0
    # 复购（老客净额）目标：口径见 modules/analytics/target_bases.py，
    # 以前这个字段只存不算、也没有接口能写，等于设不了
    repeat_customer_target: float = 0
    remark: str | None = None
    #: 乐观并发（第三批 §4.1.2）：把列表里读到的 `updated_at` 带回来，
    #: 对不上说明这条目标中途被别人改过 → 409，不静默覆盖别人的改动。
    expected_updated_at: datetime | None = None


@router.get("/sales-targets")
async def list_sales_targets(
    year: int = Query(...),
    user: CurrentUser = Depends(require_permission("customer:view")),
    session: AsyncSession = Depends(get_db),
):
    """目标 vs 实际：非 admin 只看自己 + 全公司目标行。"""
    return ok(await targets_svc.targets_with_actuals(session, user, year))


@router.get("/sales-targets/bases")
async def sales_target_bases(
    year: int = Query(...),
    user: CurrentUser = Depends(require_permission("customer:view")),
    session: AsyncSession = Depends(get_db),
):
    """三种销售额口径 + 老客净额 + 两种新客口径（文档 §六 :121 / 场景17）。

    口径与数据来源**随结果一起返回**——文档要求"分别保存计算口径与数据来源"，
    业务要能回答"这个数字是怎么来的"。签单/发货/回款三个数刻意分开，
    不互相顶替（发货口径按**实际发货批次**分摊到各批次所在月，不是拿签单额换个名字）。
    """
    from app.modules.analytics import target_bases

    return ok(await target_bases.annual_bases(session, user, year))


@router.post("/sales-targets/bases/refreeze")
async def refreeze_sales_target_bases(
    request: Request,
    year: int = Query(...),
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    """重算某一年的口径基准（老客池 / 首次成交）并重新冻结（§4.1.5）。

    为什么需要这个入口：冻结的意义是"历史不被后来的订单变更改写"，但**确实存在
    需要重算的正当理由**（比如发现一批历史订单的状态当初录错了）。与其让每次读取
    都悄悄重算（那等于没冻结），不如给一个显式、可审计的口径重置动作。
    """
    from app.modules.analytics import target_bases

    if year >= datetime.now(UTC).year:
        raise AppError(
            ErrorCode.PARAM_ERROR, "当年数据仍在产生，实时计算即可，不需要冻结", 422
        )
    veteran_ids, first_deal_month, meta = await target_bases.basis_for(
        session, year, refreeze=True, operator_id=user.id
    )
    await write_audit(
        session,
        operator_id=user.id,
        action="refreeze",
        business_type="analytics_basis",
        business_id=year,
        after={
            "year": year,
            "veteran_count": len(veteran_ids),
            "first_deal_count": len(first_deal_month),
            "frozen_at": meta["frozen_at"],
            "version": meta["version"],
        },
        ip=client_ip(request),
    )
    await session.commit()
    return ok(
        {
            "year": year,
            "veteran_count": len(veteran_ids),
            "first_deal_count": len(first_deal_month),
            **meta,
        },
        "口径基准已重算并重新冻结",
    )


@router.post("/sales-targets/upsert")
async def upsert_sales_target(
    payload: SalesTargetUpsert,
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    row, created, before = await targets_svc.upsert_target(
        session,
        user=user,
        period=payload.period,
        user_id=payload.user_id,
        department_id=payload.department_id,
        new_customer_target=payload.new_customer_target,
        sales_target=payload.sales_target,
        repeat_customer_target=payload.repeat_customer_target,
        remark=payload.remark,
        expected_updated_at=payload.expected_updated_at,
    )
    await write_audit(
        session,
        operator_id=user.id,
        # 新建和更新分开记：审计里"这条目标是什么时候被谁建出来的"要能答
        action="create" if created else "update",
        business_type="sales_target",
        business_id=row.id,
        # 改前也要留（§4.1.2「完整审计」）：只记改后，事后看不出被谁改成了什么
        before=before,
        after=targets_svc.target_snapshot(row),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(
        {
            "target_id": row.id,
            "period": row.period,
            "user_id": row.user_id,
            "department_id": row.department_id,
            "created": created,
            "new_customer_target": row.new_customer_target,
            "sales_target": float(row.sales_target or 0),
            "repeat_customer_target": float(row.repeat_customer_target or 0),
            # 回给前端，下次编辑时原样带回来就是乐观并发
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        },
        "目标已保存",
    )

@router.get("/dashboard/summary")
async def dashboard_summary(
    user: CurrentUser = Depends(require_permission("customer:view")),
    session: AsyncSession = Depends(get_db),
):
    return ok(await svc.dashboard_summary(session, user))


@router.get("/dashboard/tasks")
async def dashboard_tasks(
    user: CurrentUser = Depends(require_permission("customer:view")),
    session: AsyncSession = Depends(get_db),
):
    return ok(await svc.my_tasks(session, user))


@router.get("/dashboard/risks")
async def dashboard_risks(
    user: CurrentUser = Depends(require_permission("customer:view")),
    session: AsyncSession = Depends(get_db),
):
    return ok(await svc.risk_opportunities(session, user))


@router.get("/dashboard/trend")
async def dashboard_trend(
    months: int = Query(6, ge=1, le=12),
    user: CurrentUser = Depends(require_permission("customer:view")),
    session: AsyncSession = Depends(get_db),
):
    return ok(await svc.order_payment_trend(session, user, months))


@router.get("/dashboard/activities")
async def dashboard_activities(
    limit: int = Query(8, ge=1, le=30),
    user: CurrentUser = Depends(require_permission("customer:view")),
    session: AsyncSession = Depends(get_db),
):
    return ok(await svc.recent_activities(session, user, limit))


@router.get("/dashboard/team")
async def dashboard_team(
    user: CurrentUser = Depends(require_permission("customer:view")),
    session: AsyncSession = Depends(get_db),
):
    """PRD §4.2 主管工作台汇总。

    数据范围是 `self` 的用户会拿到 `is_team_view: false` 与空指标，
    而不是全员数据——团队指标只对 `department` 及以上开放。
    """
    return ok(await svc.team_summary(session, user))


@router.get("/analytics/opportunities")
async def analytics_opportunities(
    user: CurrentUser = Depends(require_permission("opportunity:view")),
    session: AsyncSession = Depends(get_db),
):
    return ok(await svc.opportunity_stats(session, user))


@router.get("/analytics/quotes")
async def analytics_quotes(
    user: CurrentUser = Depends(require_permission("quote:view")),
    session: AsyncSession = Depends(get_db),
):
    return ok(await svc.quote_stats(session, user))


@router.get("/analytics/customers")
async def analytics_customers(
    user: CurrentUser = Depends(require_permission("customer:view")),
    session: AsyncSession = Depends(get_db),
):
    return ok(await svc.customer_stats(session, user))


@router.get("/analytics/products")
async def analytics_products(
    limit: int = Query(10, ge=1, le=50),
    user: CurrentUser = Depends(require_permission("product:view")),
    session: AsyncSession = Depends(get_db),
):
    return ok(await svc.product_stats(session, user, limit))


@router.get("/analytics/sales-users")
async def analytics_sales_users(
    limit: int = Query(20, ge=1, le=100),
    user: CurrentUser = Depends(require_permission("customer:view")),
    session: AsyncSession = Depends(get_db),
):
    return ok(await svc.sales_user_stats(session, user, limit))


@router.get("/analytics/receivables")
async def analytics_receivables(
    user: CurrentUser = Depends(require_permission("payment:view")),
    session: AsyncSession = Depends(get_db),
):
    return ok(await svc.receivable_stats(session, user))


@router.get("/analytics/losses")
async def analytics_losses(
    user: CurrentUser = Depends(require_permission("opportunity:view")),
    session: AsyncSession = Depends(get_db),
):
    return ok(await svc.loss_reason_stats(session, user))


@router.get("/analytics/funnel")
async def analytics_funnel(
    user: CurrentUser = Depends(require_permission("opportunity:view")),
    session: AsyncSession = Depends(get_db),
):
    return ok(await svc.funnel(session, user))


@router.get("/analytics/leads")
async def analytics_leads(
    user: CurrentUser = Depends(require_permission("lead:view")),
    session: AsyncSession = Depends(get_db),
):
    """线索分析：来源分布、状态分布、转化率与平均转化时长。"""
    return ok(await svc.lead_stats(session, user))


@router.get("/analytics/pricing")
async def analytics_pricing(
    user: CurrentUser = Depends(require_permission("quote:view")),
    session: AsyncSession = Depends(get_db),
):
    """价格与核价分析：低价审批率、让价分布、按客户等级的成交价对比。"""
    return ok(await svc.pricing_stats(session, user))


@router.get("/analytics/payments")
async def analytics_payments(
    user: CurrentUser = Depends(require_permission("payment:view")),
    session: AsyncSession = Depends(get_db),
):
    """回款分析：按期状态、逾期账龄分布、回款方式分布。"""
    return ok(await svc.payment_stats(session, user))


@router.get("/analytics/delivery")
async def analytics_delivery(
    risk_limit: int = Query(20, ge=1, le=100),
    user: CurrentUser = Depends(require_permission("order:view")),
    session: AsyncSession = Depends(get_db),
):
    """交期履约：准时交付率、延迟天数、逾期节点分布、在跟风险单。

    数据源是跟单里程碑与发货批次（此前只写不读）；看板里的"逾期节点"
    与销售每天收到的逾期提醒是同一口径。
    """
    return ok(await svc.delivery_stats(session, user, risk_limit))
