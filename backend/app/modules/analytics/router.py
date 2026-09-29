"""工作台与数据分析接口。"""

from fastapi import APIRouter, Depends, Request
from fastapi import Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.response import ok
from app.modules.analytics import service as svc
from app.modules.analytics import targets as targets_svc

router = APIRouter(tags=["Analytics"])


class SalesTargetUpsert(BaseModel):
    period: str
    user_id: int | None = None
    # 团队目标（文档 §六 :121）：与 user_id 互斥——指定部门就是团队目标
    department_id: int | None = None
    new_customer_target: int = 0
    sales_target: float = 0
    remark: str | None = None


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
    不互相顶替（发货口径按首批实际发货日整单归月，不是拿签单额换个名字）。
    """
    from app.modules.analytics import target_bases

    return ok(await target_bases.annual_bases(session, user, year))


@router.post("/sales-targets/upsert")
async def upsert_sales_target(
    payload: SalesTargetUpsert,
    request: Request,
    user: CurrentUser = Depends(require_permission("settings:manage")),
    session: AsyncSession = Depends(get_db),
):
    row = await targets_svc.upsert_target(
        session,
        user=user,
        period=payload.period,
        user_id=payload.user_id,
        department_id=payload.department_id,
        new_customer_target=payload.new_customer_target,
        sales_target=payload.sales_target,
        remark=payload.remark,
    )
    await write_audit(
        session,
        operator_id=user.id,
        action="upsert",
        business_type="sales_target",
        business_id=row.id,
        after={
            "period": payload.period,
            "user_id": payload.user_id,
            "department_id": payload.department_id,
            "new_customer_target": payload.new_customer_target,
            "sales_target": payload.sales_target,
        },
        ip=client_ip(request),
    )
    await session.commit()
    return ok(
        {
            "target_id": row.id,
            "period": row.period,
            "user_id": row.user_id,
            "new_customer_target": row.new_customer_target,
            "sales_target": float(row.sales_target or 0),
        },
        "目标已保存",
    )


class SalesTargetUpsert(BaseModel):
    period: str
    user_id: int | None = None
    new_customer_target: int = 0
    sales_target: float = 0
    remark: str | None = None


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
