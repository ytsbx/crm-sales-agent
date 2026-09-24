"""商机中心接口（对齐 03-API §11 / §12 / §13）。"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok, page_data, paginate
from app.modules.customer.model import Customer
from app.modules.opportunity import service as svc
from app.modules.opportunity.model import (
    LossReason,
    Opportunity,
    OpportunityItem,
    OpportunityStage,
    OpportunityStageHistory,
)
from app.modules.opportunity.schema import (
    OpportunityCreate,
    OpportunityItemCreate,
    OpportunityItemUpdate,
    OpportunityLose,
    OpportunityUpdate,
    OpportunityWin,
    StageChange,
)

router = APIRouter(tags=["Opportunity"])


# ---------------------------------------------------------------- 元数据

@router.get("/opportunity-stages")
async def list_stages(
    _: CurrentUser = Depends(require_permission("opportunity:view")),
    session: AsyncSession = Depends(get_db),
):
    rows = (
        await session.execute(select(OpportunityStage).order_by(OpportunityStage.sequence.asc()))
    ).scalars().all()
    return ok([svc.serialize_stage(stage) for stage in rows])


@router.get("/loss-reasons")
async def list_loss_reasons(
    _: CurrentUser = Depends(require_permission("opportunity:view")),
    session: AsyncSession = Depends(get_db),
):
    return ok(await svc.list_loss_reasons(session))


@router.get("/opportunities/funnel")
async def funnel(
    user: CurrentUser = Depends(require_permission("opportunity:view")),
    session: AsyncSession = Depends(get_db),
):
    """销售漏斗：按阶段统计未成交商机的数量与金额。"""
    stages = (
        await session.execute(select(OpportunityStage).order_by(OpportunityStage.sequence.asc()))
    ).scalars().all()
    stmt = svc.apply_data_scope(
        select(
            Opportunity.stage_id,
            func.count(Opportunity.id),
            func.coalesce(func.sum(Opportunity.expected_amount), 0),
        )
        .where(Opportunity.deleted_at.is_(None), Opportunity.status == "open")
        .group_by(Opportunity.stage_id),
        user,
    )
    rows = (await session.execute(stmt)).all()
    stats = {int(sid): (int(count), float(amount or 0)) for sid, count, amount in rows}
    return ok(
        [
            {
                "stage_id": stage.id,
                "stage_name": stage.name,
                "sequence": stage.sequence,
                "count": stats.get(stage.id, (0, 0.0))[0],
                "amount": stats.get(stage.id, (0, 0.0))[1],
            }
            for stage in stages
        ]
    )


# ---------------------------------------------------------------- 商机

@router.get("/opportunities")
async def list_opportunities(
    keyword: str | None = None,
    stage_id: int | None = None,
    status: str | None = None,
    owner_id: int | None = None,
    customer_id: int | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user: CurrentUser = Depends(require_permission("opportunity:view")),
    session: AsyncSession = Depends(get_db),
):
    stmt = svc.apply_data_scope(
        svc.build_opportunity_stmt(
            keyword=keyword,
            stage_id=stage_id,
            status=status,
            owner_id=owner_id,
            customer_id=customer_id,
        ),
        user,
    )
    rows, total = await paginate(session, stmt, page, page_size)
    customers, owners, counts = await svc.enrichment(session, rows)
    stages = await svc.stage_map(session)
    items = [
        svc.serialize_opportunity(
            opportunity,
            stage=stages.get(opportunity.stage_id),
            customer_name=customers.get(opportunity.customer_id),
            owner_name=owners.get(opportunity.owner_id) if opportunity.owner_id else None,
            item_count=counts.get(opportunity.id, 0),
        )
        for opportunity in rows
    ]
    return ok(page_data(items, total, page, page_size))


@router.post("/opportunities")
async def create_opportunity(
    payload: OpportunityCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("opportunity:manage")),
    session: AsyncSession = Depends(get_db),
):
    customer = await session.get(Customer, payload.customer_id)
    if customer is None or customer.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "客户不存在", 404)

    if payload.stage_id:
        stage = await session.get(OpportunityStage, payload.stage_id)
        if stage is None:
            raise AppError(ErrorCode.NOT_FOUND, "商机阶段不存在", 404)
    else:
        stage = await svc.get_first_stage(session)

    data = payload.model_dump(exclude={"stage_id"})
    owner_id = data.pop("owner_id", None) or customer.owner_id or user.id
    opportunity = Opportunity(
        **data,
        stage_id=stage.id,
        owner_id=owner_id,
        status="open",
        created_by=user.id,
    )
    session.add(opportunity)
    await session.flush()
    session.add(
        OpportunityStageHistory(
            opportunity_id=opportunity.id,
            from_stage_id=None,
            to_stage_id=stage.id,
            operator_id=user.id,
            remark="创建商机",
            entered_at=datetime.now(UTC),
        )
    )
    await write_audit(
        session,
        operator_id=user.id,
        action="create",
        business_type="opportunity",
        business_id=opportunity.id,
        after=svc.serialize_opportunity(opportunity, stage=stage),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(
        svc.serialize_opportunity(
            opportunity, stage=stage, customer_name=customer.name, owner_name=user.name
        ),
        "商机已创建",
    )


@router.get("/opportunities/{opportunity_id}")
async def get_opportunity(
    opportunity_id: int,
    _: CurrentUser = Depends(require_permission("opportunity:view")),
    session: AsyncSession = Depends(get_db),
):
    opportunity = await svc.get_opportunity_or_404(session, opportunity_id)
    customers, owners, counts = await svc.enrichment(session, [opportunity])
    stages = await svc.stage_map(session)
    loss_reason_name = None
    if opportunity.loss_reason_id:
        reason = await session.get(LossReason, opportunity.loss_reason_id)
        loss_reason_name = reason.name if reason else None
    return ok(
        svc.serialize_opportunity(
            opportunity,
            stage=stages.get(opportunity.stage_id),
            customer_name=customers.get(opportunity.customer_id),
            owner_name=owners.get(opportunity.owner_id) if opportunity.owner_id else None,
            item_count=counts.get(opportunity.id, 0),
            loss_reason_name=loss_reason_name,
        )
    )


@router.patch("/opportunities/{opportunity_id}")
async def update_opportunity(
    opportunity_id: int,
    payload: OpportunityUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("opportunity:manage")),
    session: AsyncSession = Depends(get_db),
):
    opportunity = await svc.get_opportunity_or_404(session, opportunity_id)
    before = svc.serialize_opportunity(opportunity)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(opportunity, field, value)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="update",
        business_type="opportunity",
        business_id=opportunity.id,
        before=before,
        after=svc.serialize_opportunity(opportunity),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_opportunity(opportunity), "已保存")


@router.post("/opportunities/{opportunity_id}/change-stage")
async def change_stage(
    opportunity_id: int,
    payload: StageChange,
    request: Request,
    user: CurrentUser = Depends(require_permission("opportunity:manage")),
    session: AsyncSession = Depends(get_db),
):
    opportunity = await svc.get_opportunity_or_404(session, opportunity_id)
    if opportunity.status in ("win", "loss"):
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "已成交或已失单的商机不能改阶段")

    stage = None
    if payload.stage_id:
        stage = await session.get(OpportunityStage, payload.stage_id)
    elif payload.stage_code:
        stage = (
            await session.execute(
                select(OpportunityStage).where(OpportunityStage.code == payload.stage_code)
            )
        ).scalar_one_or_none()
    if stage is None:
        raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "请指定要推进到的阶段")

    await svc.change_stage(
        session, opportunity, to_stage=stage, operator_id=user.id, remark=payload.remark
    )
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="change_stage",
        business_type="opportunity",
        business_id=opportunity.id,
        after={"stage": stage.name, "remark": payload.remark},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_opportunity(opportunity, stage=stage), f"已推进到「{stage.name}」")


@router.post("/opportunities/{opportunity_id}/win")
async def win_opportunity(
    opportunity_id: int,
    payload: OpportunityWin,
    request: Request,
    user: CurrentUser = Depends(require_permission("opportunity:manage")),
    session: AsyncSession = Depends(get_db),
):
    opportunity = await svc.get_opportunity_or_404(session, opportunity_id)
    if opportunity.status == "win":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "该商机已经成交")
    stage = await svc.get_won_stage(session)
    if stage is None:
        raise AppError(ErrorCode.SYSTEM_ERROR, "未配置成交阶段", 500)

    await svc.change_stage(
        session, opportunity, to_stage=stage, operator_id=user.id, remark=payload.remark or "标记成交"
    )
    opportunity.status = "win"
    opportunity.win_quote_version_id = payload.win_quote_version_id
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="win",
        business_type="opportunity",
        business_id=opportunity.id,
        after={"quote_version_id": payload.win_quote_version_id},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_opportunity(opportunity, stage=stage), "商机已成交")


@router.post("/opportunities/{opportunity_id}/lose")
async def lose_opportunity(
    opportunity_id: int,
    payload: OpportunityLose,
    request: Request,
    user: CurrentUser = Depends(require_permission("opportunity:manage")),
    session: AsyncSession = Depends(get_db),
):
    opportunity = await svc.get_opportunity_or_404(session, opportunity_id)
    if opportunity.status == "loss":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "该商机已经失单")
    reason = await session.get(LossReason, payload.loss_reason_id)
    if reason is None:
        raise AppError(ErrorCode.NOT_FOUND, "失单原因不存在", 404)

    opportunity.status = "loss"
    opportunity.loss_reason_id = reason.id
    opportunity.loss_remark = payload.remark
    opportunity.reopen_at = payload.reopen_at
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="lose",
        business_type="opportunity",
        business_id=opportunity.id,
        after={"loss_reason": reason.name, "remark": payload.remark},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(
        svc.serialize_opportunity(opportunity, loss_reason_name=reason.name),
        f"商机已失单（{reason.name}）",
    )


@router.post("/opportunities/{opportunity_id}/reopen")
async def reopen_opportunity(
    opportunity_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("opportunity:manage")),
    session: AsyncSession = Depends(get_db),
):
    opportunity = await svc.get_opportunity_or_404(session, opportunity_id)
    if opportunity.status != "loss":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "只有失单的商机可以重新激活")
    stage = await svc.get_first_stage(session)
    opportunity.status = "open"
    opportunity.loss_reason_id = None
    opportunity.reopen_at = None
    await svc.change_stage(
        session, opportunity, to_stage=stage, operator_id=user.id, remark="失单后重新激活"
    )
    await write_audit(
        session,
        operator_id=user.id,
        action="reopen",
        business_type="opportunity",
        business_id=opportunity.id,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_opportunity(opportunity, stage=stage), "商机已重新激活")


@router.get("/opportunities/{opportunity_id}/stage-history")
async def stage_history(
    opportunity_id: int,
    _: CurrentUser = Depends(require_permission("opportunity:view")),
    session: AsyncSession = Depends(get_db),
):
    await svc.get_opportunity_or_404(session, opportunity_id)
    stages = await svc.stage_map(session)
    rows = (
        await session.execute(
            select(OpportunityStageHistory)
            .where(OpportunityStageHistory.opportunity_id == opportunity_id)
            .order_by(OpportunityStageHistory.id.desc())
        )
    ).scalars().all()
    return ok(
        [
            {
                "id": row.id,
                "from_stage": stages[row.from_stage_id].name if row.from_stage_id in stages else None,
                "to_stage": stages[row.to_stage_id].name if row.to_stage_id in stages else None,
                "remark": row.remark,
                "entered_at": row.entered_at,
                "left_at": row.left_at,
                "duration_seconds": row.duration_seconds,
            }
            for row in rows
        ]
    )


@router.delete("/opportunities/{opportunity_id}")
async def delete_opportunity(
    opportunity_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("opportunity:manage")),
    session: AsyncSession = Depends(get_db),
):
    opportunity = await svc.get_opportunity_or_404(session, opportunity_id)
    before = svc.serialize_opportunity(opportunity)
    opportunity.deleted_at = datetime.now(UTC)
    await write_audit(
        session,
        operator_id=user.id,
        action="delete",
        business_type="opportunity",
        business_id=opportunity.id,
        before=before,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(None, "商机已删除")


# ---------------------------------------------------------------- 需求明细

@router.get("/opportunities/{opportunity_id}/items")
async def list_items(
    opportunity_id: int,
    _: CurrentUser = Depends(require_permission("opportunity:view")),
    session: AsyncSession = Depends(get_db),
):
    await svc.get_opportunity_or_404(session, opportunity_id)
    return ok(await svc.list_items(session, opportunity_id))


@router.post("/opportunities/{opportunity_id}/items")
async def create_item(
    opportunity_id: int,
    payload: OpportunityItemCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("opportunity:manage")),
    session: AsyncSession = Depends(get_db),
):
    await svc.get_opportunity_or_404(session, opportunity_id)
    item = OpportunityItem(**payload.model_dump(), opportunity_id=opportunity_id)
    session.add(item)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="create_item",
        business_type="opportunity",
        business_id=opportunity_id,
        after=svc.serialize_item(item),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_item(item), "需求明细已添加")


@router.patch("/opportunity-items/{item_id}")
async def update_item(
    item_id: int,
    payload: OpportunityItemUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("opportunity:manage")),
    session: AsyncSession = Depends(get_db),
):
    item = await svc.get_item_or_404(session, item_id)
    before = svc.serialize_item(item)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, field, value)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="update_item",
        business_type="opportunity",
        business_id=item.opportunity_id,
        before=before,
        after=svc.serialize_item(item),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_item(item), "已保存")


@router.delete("/opportunity-items/{item_id}")
async def delete_item(
    item_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("opportunity:manage")),
    session: AsyncSession = Depends(get_db),
):
    item = await svc.get_item_or_404(session, item_id)
    before = svc.serialize_item(item)
    await session.delete(item)
    await write_audit(
        session,
        operator_id=user.id,
        action="delete_item",
        business_type="opportunity",
        business_id=item.opportunity_id,
        before=before,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(None, "需求明细已删除")
