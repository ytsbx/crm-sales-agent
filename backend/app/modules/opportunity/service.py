"""商机业务逻辑。"""

from datetime import UTC, date, datetime

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.data_scope import scoped_owner_ids
from app.core.deps import CurrentUser
from app.core.errors import AppError, ErrorCode
from app.modules.customer.model import Customer
from app.modules.opportunity.model import (
    LossReason,
    Opportunity,
    OpportunityItem,
    OpportunityStage,
    OpportunityStageHistory,
)
from app.modules.product.model import Sku
from app.modules.user.model import User


def _number(value) -> float | None:
    return None if value is None else float(value)


async def get_first_stage(session: AsyncSession) -> OpportunityStage:
    stage = (
        await session.execute(
            select(OpportunityStage)
            .where(OpportunityStage.status == "active")
            .order_by(OpportunityStage.sequence.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if stage is None:
        raise AppError(ErrorCode.SYSTEM_ERROR, "商机阶段未初始化，请先执行种子数据", 500)
    return stage


async def get_won_stage(session: AsyncSession) -> OpportunityStage | None:
    return (
        await session.execute(select(OpportunityStage).where(OpportunityStage.is_win.is_(True)))
    ).scalar_one_or_none()


async def stage_map(session: AsyncSession) -> dict[int, OpportunityStage]:
    rows = (await session.execute(select(OpportunityStage))).scalars().all()
    return {stage.id: stage for stage in rows}


def serialize_stage(stage: OpportunityStage) -> dict:
    return {
        "id": stage.id,
        "code": stage.code,
        "name": stage.name,
        "sequence": stage.sequence,
        "is_win": stage.is_win,
        "is_loss": stage.is_loss,
        "status": stage.status,
    }


def serialize_opportunity(
    opportunity: Opportunity,
    *,
    stage: OpportunityStage | None = None,
    customer_name: str | None = None,
    owner_name: str | None = None,
    item_count: int = 0,
    loss_reason_name: str | None = None,
) -> dict:
    return {
        "id": opportunity.id,
        "customer_id": opportunity.customer_id,
        "customer_name": customer_name,
        "primary_contact_id": opportunity.primary_contact_id,
        "title": opportunity.title,
        "source": opportunity.source,
        "stage_id": opportunity.stage_id,
        "stage_name": stage.name if stage else None,
        "stage_code": stage.code if stage else None,
        "expected_amount": _number(opportunity.expected_amount),
        "currency": opportunity.currency,
        "expected_close_date": opportunity.expected_close_date,
        "owner_id": opportunity.owner_id,
        "owner_name": owner_name,
        "competitor": opportunity.competitor,
        "risk_level": opportunity.risk_level,
        "next_action": opportunity.next_action,
        "status": opportunity.status,
        "loss_reason_id": opportunity.loss_reason_id,
        "loss_reason_name": loss_reason_name,
        "loss_remark": opportunity.loss_remark,
        "reopen_at": opportunity.reopen_at,
        "item_count": item_count,
        "created_at": opportunity.created_at,
        "updated_at": opportunity.updated_at,
    }


def serialize_item(item: OpportunityItem, sku: Sku | None = None) -> dict:
    return {
        "id": item.id,
        "opportunity_id": item.opportunity_id,
        "sku_id": item.sku_id,
        "sku_code": sku.sku_code if sku else None,
        "sku_name": sku.name if sku else None,
        "product_id": sku.product_id if sku else None,
        "specification": item.specification or (sku.specification if sku else None),
        "color": item.color or (sku.color if sku else None),
        "quantity": _number(item.quantity),
        "target_price": _number(item.target_price),
        "currency": item.currency,
        "package_requirement": item.package_requirement,
        "delivery_date": item.delivery_date,
        "destination": item.destination,
        "remark": item.remark,
        "moq": sku.moq if sku else None,
        "unit": sku.unit if sku else None,
    }


def build_opportunity_stmt(
    keyword: str | None = None,
    stage_id: int | None = None,
    status: str | None = None,
    owner_id: int | None = None,
    customer_id: int | None = None,
) -> Select:
    stmt = select(Opportunity).where(Opportunity.deleted_at.is_(None))
    if keyword:
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(Opportunity.title.ilike(like))
    if stage_id:
        stmt = stmt.where(Opportunity.stage_id == stage_id)
    if status:
        stmt = stmt.where(Opportunity.status == status)
    if owner_id is not None:
        stmt = stmt.where(Opportunity.owner_id == owner_id)
    if customer_id:
        stmt = stmt.where(Opportunity.customer_id == customer_id)
    return stmt.order_by(Opportunity.id.desc())


async def apply_data_scope(
    stmt: Select, user: CurrentUser, session: AsyncSession
) -> Select:
    """`department_and_sub` 取本部门及所有下级部门，见 app/core/data_scope.py。"""
    owner_ids = await scoped_owner_ids(session, user)
    if owner_ids is None:
        return stmt
    return stmt.where(Opportunity.owner_id.in_(owner_ids))


async def get_opportunity_or_404(session: AsyncSession, opportunity_id: int) -> Opportunity:
    opportunity = await session.get(Opportunity, opportunity_id)
    if opportunity is None or opportunity.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "商机不存在", 404)
    return opportunity


async def enrichment(
    session: AsyncSession, opportunities: list[Opportunity]
) -> tuple[dict[int, str], dict[int, str], dict[int, int]]:
    """批量取客户名、负责人名、需求条数，避免列表页 N+1 查询。"""
    customer_ids = {o.customer_id for o in opportunities}
    owner_ids = {o.owner_id for o in opportunities if o.owner_id}
    opp_ids = [o.id for o in opportunities]

    customers: dict[int, str] = {}
    if customer_ids:
        rows = (
            await session.execute(
                select(Customer.id, Customer.name).where(Customer.id.in_(customer_ids))
            )
        ).all()
        customers = {int(cid): name for cid, name in rows}

    owners: dict[int, str] = {}
    if owner_ids:
        rows = (
            await session.execute(select(User.id, User.name).where(User.id.in_(owner_ids)))
        ).all()
        owners = {int(uid): name for uid, name in rows}

    counts: dict[int, int] = {}
    if opp_ids:
        rows = (
            await session.execute(
                select(OpportunityItem.opportunity_id, func.count(OpportunityItem.id))
                .where(OpportunityItem.opportunity_id.in_(opp_ids))
                .group_by(OpportunityItem.opportunity_id)
            )
        ).all()
        counts = {int(oid): int(count) for oid, count in rows}

    return customers, owners, counts


async def create_opportunity_from_lead(
    session: AsyncSession,
    *,
    customer_id: int,
    primary_contact_id: int | None,
    title: str,
    owner_id: int | None,
    created_by: int | None,
    expected_amount: float | None = None,
) -> Opportunity:
    stage = await get_first_stage(session)
    opportunity = Opportunity(
        customer_id=customer_id,
        primary_contact_id=primary_contact_id,
        title=title,
        source="线索转化",
        stage_id=stage.id,
        owner_id=owner_id,
        status="open",
        expected_amount=expected_amount,
        created_by=created_by,
    )
    session.add(opportunity)
    await session.flush()
    session.add(
        OpportunityStageHistory(
            opportunity_id=opportunity.id,
            from_stage_id=None,
            to_stage_id=stage.id,
            operator_id=created_by,
            remark="创建商机",
            entered_at=datetime.now(UTC),
        )
    )
    return opportunity


async def change_stage(
    session: AsyncSession,
    opportunity: Opportunity,
    *,
    to_stage: OpportunityStage,
    operator_id: int,
    remark: str | None = None,
) -> None:
    """切换阶段：先关闭当前停留记录，再开一条新的，保证停留时长可统计。"""
    now = datetime.now(UTC)
    current = (
        await session.execute(
            select(OpportunityStageHistory)
            .where(
                OpportunityStageHistory.opportunity_id == opportunity.id,
                OpportunityStageHistory.left_at.is_(None),
            )
            .order_by(OpportunityStageHistory.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if current is not None:
        current.left_at = now
        entered = current.entered_at
        if entered.tzinfo is None:
            entered = entered.replace(tzinfo=UTC)
        current.duration_seconds = int((now - entered).total_seconds())

    session.add(
        OpportunityStageHistory(
            opportunity_id=opportunity.id,
            from_stage_id=opportunity.stage_id,
            to_stage_id=to_stage.id,
            operator_id=operator_id,
            remark=remark,
            entered_at=now,
        )
    )
    opportunity.stage_id = to_stage.id
    if to_stage.is_win:
        opportunity.status = "win"
    elif to_stage.is_loss:
        opportunity.status = "loss"
    else:
        opportunity.status = "open"


async def list_items(session: AsyncSession, opportunity_id: int) -> list[dict]:
    stmt = (
        select(OpportunityItem, Sku)
        .join(Sku, Sku.id == OpportunityItem.sku_id)
        .where(OpportunityItem.opportunity_id == opportunity_id)
        .order_by(OpportunityItem.id.asc())
    )
    rows = (await session.execute(stmt)).all()
    return [serialize_item(item, sku) for item, sku in rows]


async def get_item_or_404(session: AsyncSession, item_id: int) -> OpportunityItem:
    item = await session.get(OpportunityItem, item_id)
    if item is None:
        raise AppError(ErrorCode.NOT_FOUND, "需求明细不存在", 404)
    return item


async def list_loss_reasons(session: AsyncSession) -> list[dict]:
    rows = (
        await session.execute(
            select(LossReason).where(LossReason.status == "active").order_by(LossReason.id.asc())
        )
    ).scalars().all()
    return [
        {"id": r.id, "code": r.code, "name": r.name, "category": r.category} for r in rows
    ]


async def touch_last_followup(session: AsyncSession, opportunity: Opportunity) -> None:
    """记录最近跟进时间，供「多久没跟」这类规则使用。"""
    opportunity.updated_at = datetime.now(UTC)


def today() -> date:
    return datetime.now(UTC).date()


__all__ = [
    "apply_data_scope",
    "build_opportunity_stmt",
    "change_stage",
    "create_opportunity_from_lead",
    "enrichment",
    "get_first_stage",
    "get_item_or_404",
    "get_opportunity_or_404",
    "get_won_stage",
    "list_items",
    "list_loss_reasons",
    "serialize_item",
    "serialize_opportunity",
    "serialize_stage",
    "stage_map",
    "today",
    "touch_last_followup",
]
