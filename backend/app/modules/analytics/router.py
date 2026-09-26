"""工作台与数据分析接口。"""

from fastapi import APIRouter, Depends
from fastapi import Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import CurrentUser, require_permission
from app.core.response import ok
from app.modules.analytics import service as svc

router = APIRouter(tags=["Analytics"])


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
