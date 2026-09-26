"""公海池接口（03-API §9，PRD §6.4）。

PRD 把「公海」明确为一项能力：放入公海 / 领取 / 分配 / 自动回收 / 回收规则。
回收规则（`public_pool_rules`）与自动回收（`run-recycle`）此前已在 settings 模块里，
客户侧的「放入公海 / 领取」也已有单条接口；缺的是**公海本身的视图与分配**，
以及线索侧的公海操作 —— 线索池在业务上就是"没有负责人的线索"。

这里补 §9 缺的 6 个接口：
  GET  /public-pool/customers                公海客户列表
  GET  /public-pool/leads                    公海线索列表
  POST /public-pool/customers/{id}/claim      领取
  POST /public-pool/leads/{id}/claim          领取
  POST /public-pool/customers/{id}/assign     管理员直接指派
  POST /public-pool/leads/{id}/assign         管理员直接指派

复用既有 service（`customer.transfer_customer` / `lead.assign_lead`），
所以分配历史、目标负责人校验、数据范围规则与单条接口完全一致。
"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok, page_data, paginate
from app.modules.customer import service as customer_service
from app.modules.customer.model import Customer
from app.modules.lead import service as lead_service
from app.modules.lead.model import Lead
from app.modules.lead.schema import LeadAssign
from app.modules.public_pool.schema import PoolAssignRequest

router = APIRouter(tags=["PublicPool"])


# ---------------------------------------------------------------- 公海客户

@router.get("/public-pool/customers")
async def list_public_customers(
    keyword: str | None = None,
    level: str | None = None,
    region: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    _: CurrentUser = Depends(require_permission("customer:view")),
    session: AsyncSession = Depends(get_db),
):
    """公海客户列表。

    公海对所有有 customer:view 的人可见（这正是公海的意义），
    所以这里**不套数据范围** —— 套上就没人看得到了。
    """
    stmt = select(Customer).where(
        Customer.deleted_at.is_(None), Customer.pool_status == "public"
    )
    if keyword:
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(Customer.name.ilike(like) | Customer.short_name.ilike(like))
    if level:
        stmt = stmt.where(Customer.level == level)
    if region:
        stmt = stmt.where(Customer.region == region)

    rows, total = await paginate(session, stmt.order_by(Customer.id.desc()), page, page_size)
    counts = await customer_service.contact_counts(session, [row.id for row in rows])
    return ok(
        page_data(
            [
                customer_service.serialize_customer(
                    row, contact_count=counts.get(row.id, 0)
                )
                for row in rows
            ],
            total,
            page,
            page_size,
        )
    )


@router.post("/public-pool/customers/{customer_id}/claim")
async def claim_public_customer(
    customer_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("customer:view")),
    session: AsyncSession = Depends(get_db),
):
    """领取公海客户到自己名下。"""
    customer = await customer_service.get_customer_or_404(session, customer_id)
    if customer.pool_status != "public":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "该客户不在公海，无法领取")
    if customer.owner_id is not None:
        # 数据不一致时明确报错，而不是悄悄抢走别人的客户
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"该客户当前负责人是 id={customer.owner_id}，不在公海",
        )
    await customer_service.transfer_customer(session, user, customer, user.id, "公海领取")
    await write_audit(
        session,
        operator_id=user.id,
        action="pool_claim",
        business_type="customer",
        business_id=customer.id,
        after={"owner_id": user.id},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(
        customer_service.serialize_customer(customer), f"已领取客户「{customer.name}」"
    )


@router.post("/public-pool/customers/{customer_id}/assign")
async def assign_public_customer(
    customer_id: int,
    payload: PoolAssignRequest,
    request: Request,
    user: CurrentUser = Depends(require_permission("customer:assign")),
    session: AsyncSession = Depends(get_db),
):
    """把公海客户指派给某人（不需要对方来领）。

    不清空负责人的前置校验是有意的取舍：
    - 把已有负责人的客户改派给别人，业务上是正常的（主管调度）；
    - 但"把已有负责人的客户放回公海"应当走客户模块的「放入公海」，
      那里会写客户负责人变更历史，语义更清楚。
    所以只有 **owner_id 为空且客户当前有负责人** 这一种组合才拦。

    这样也不会出现"前置检查把真正该报的错盖掉"：
    目标负责人不存在会在 `transfer_customer` 里报 404，不会被这里拦成 40002。
    """
    customer = await customer_service.get_customer_or_404(session, customer_id)
    if payload.owner_id is None and customer.owner_id is not None:
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            "该客户已有负责人，请用客户详情里的「放入公海」以留下变更历史",
        )
    await customer_service.transfer_customer(
        session, user, customer, payload.owner_id, payload.reason or "公海指派"
    )
    await write_audit(
        session,
        operator_id=user.id,
        action="pool_assign",
        business_type="customer",
        business_id=customer.id,
        after={"owner_id": payload.owner_id, "reason": payload.reason},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(
        customer_service.serialize_customer(customer),
        "已指派公海客户" if payload.owner_id else "已放回公海",
    )


# ---------------------------------------------------------------- 公海线索
#
# 线索没有独立的 pool_status：**没有负责人就是待分配（公海）**。
# 这与客户的建模不同，是有意的 —— 线索从来到走都很轻，
# 加一个状态字段只会多一处可能与 owner_id 不一致的地方。

@router.get("/public-pool/leads")
async def list_public_leads(
    keyword: str | None = None,
    source: str | None = None,
    region: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    _: CurrentUser = Depends(require_permission("lead:view")),
    session: AsyncSession = Depends(get_db),
):
    """公海线索列表：未分配（无负责人）且未被丢弃的线索。"""
    stmt = select(Lead).where(
        Lead.deleted_at.is_(None),
        Lead.owner_id.is_(None),
        Lead.status == "pending",
    )
    if keyword:
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(
            Lead.name.ilike(like)
            | Lead.company_name.ilike(like)
            | Lead.contact_name.ilike(like)
        )
    if source:
        stmt = stmt.where(Lead.source == source)
    if region:
        stmt = stmt.where(Lead.region == region)

    rows, total = await paginate(session, stmt.order_by(Lead.id.desc()), page, page_size)
    owners = await lead_service.owner_names(session, [row.owner_id for row in rows])
    return ok(
        page_data(
            [
                lead_service.serialize_lead(
                    row, owner_name=owners.get(row.owner_id) if row.owner_id else None
                )
                for row in rows
            ],
            total,
            page,
            page_size,
        )
    )


@router.post("/public-pool/leads/{lead_id}/claim")
async def claim_public_lead(
    lead_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("lead:view")),
    session: AsyncSession = Depends(get_db),
):
    """领取公海线索。

    与线索中心的 `/leads/{id}/claim` 落同一结果（都走 `assign_lead`），
    区别只是这个挂在公海路径下，供公海页面调用。
    """
    lead = await lead_service.get_lead_or_404(session, lead_id)
    if lead.owner_id is not None:
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED, "该线索已有负责人，请用「分配」而不是「领取」"
        )
    await lead_service.assign_lead(
        session, lead, to_user_id=user.id, operator_id=user.id, reason="公海领取"
    )
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="pool_claim",
        business_type="lead",
        business_id=lead.id,
        after={"owner_id": user.id},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(lead_service.serialize_lead(lead), f"已领取线索「{lead.name}」")


@router.post("/public-pool/leads/{lead_id}/assign")
async def assign_public_lead(
    lead_id: int,
    payload: LeadAssign,
    request: Request,
    user: CurrentUser = Depends(require_permission("lead:assign")),
    session: AsyncSession = Depends(get_db),
):
    """把公海线索指派给某人（`owner_id` 为空表示放回线索池）。"""
    lead = await lead_service.get_lead_or_404(session, lead_id)
    await lead_service.assign_lead(
        session,
        lead,
        to_user_id=payload.owner_id,
        operator_id=user.id,
        reason=payload.reason or "公海指派",
    )
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="pool_assign",
        business_type="lead",
        business_id=lead.id,
        after={"owner_id": payload.owner_id, "reason": payload.reason},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(
        lead_service.serialize_lead(lead),
        "已指派线索" if payload.owner_id else "已放回线索池",
    )


__all__ = ["router"]
