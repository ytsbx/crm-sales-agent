"""线索业务逻辑。"""

from datetime import UTC, datetime

from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.data_scope import scoped_owner_ids
from app.core.deps import CurrentUser
from app.core.errors import AppError, ErrorCode
from app.modules.lead.model import Lead, LeadAssignment
from app.modules.user.model import User

STATUS_LABEL = {
    "pending": "待分配",
    "assigned": "已分配",
    "following": "跟进中",
    "converted": "已转客户",
    "invalid": "无效",
}


def serialize_lead(lead: Lead, *, owner_name: str | None = None) -> dict:
    return {
        "id": lead.id,
        "name": lead.name,
        "company_name": lead.company_name,
        "contact_name": lead.contact_name,
        "mobile": lead.mobile,
        "email": lead.email,
        "source": lead.source,
        "source_detail": lead.source_detail,
        "country": lead.country,
        "region": lead.region,
        "status": lead.status,
        "status_label": STATUS_LABEL.get(lead.status, lead.status),
        "owner_id": lead.owner_id,
        "owner_name": owner_name,
        "converted_customer_id": lead.converted_customer_id,
        "converted_contact_id": lead.converted_contact_id,
        "converted_opportunity_id": lead.converted_opportunity_id,
        "invalid_reason": lead.invalid_reason,
        "remark": lead.remark,
        "last_followup_at": lead.last_followup_at,
        "created_at": lead.created_at,
        "updated_at": lead.updated_at,
    }


def build_lead_stmt(
    keyword: str | None = None,
    status: str | None = None,
    source: str | None = None,
    owner_id: int | None = None,
    unassigned: bool = False,
) -> Select:
    stmt = select(Lead).where(Lead.deleted_at.is_(None))
    if keyword:
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(
            or_(
                Lead.name.ilike(like),
                Lead.company_name.ilike(like),
                Lead.contact_name.ilike(like),
                Lead.mobile.ilike(like),
            )
        )
    if status:
        stmt = stmt.where(Lead.status == status)
    if source:
        stmt = stmt.where(Lead.source == source)
    if owner_id is not None:
        stmt = stmt.where(Lead.owner_id == owner_id)
    if unassigned:
        stmt = stmt.where(Lead.owner_id.is_(None))
    return stmt.order_by(Lead.id.desc())


async def apply_data_scope(
    stmt: Select, user: CurrentUser, session: AsyncSession
) -> Select:
    """线索池里的未分配线索大家都能看到，已分配的按数据范围过滤。

    `department_and_sub` 取本部门及所有下级部门，见 app/core/data_scope.py。
    """
    owner_ids = await scoped_owner_ids(session, user)
    if owner_ids is None:
        return stmt
    return stmt.where(or_(Lead.owner_id.in_(owner_ids), Lead.owner_id.is_(None)))


async def get_lead_or_404(session: AsyncSession, lead_id: int) -> Lead:
    lead = await session.get(Lead, lead_id)
    if lead is None or lead.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "线索不存在", 404)
    return lead


async def owner_names(session: AsyncSession, owner_ids: list[int]) -> dict[int, str]:
    ids = [oid for oid in owner_ids if oid]
    if not ids:
        return {}
    rows = (await session.execute(select(User.id, User.name).where(User.id.in_(ids)))).all()
    return {int(uid): name for uid, name in rows}


def record_assignment(
    session: AsyncSession,
    *,
    lead: Lead,
    to_user_id: int | None,
    operator_id: int,
    reason: str | None,
) -> None:
    session.add(
        LeadAssignment(
            lead_id=lead.id,
            from_user_id=lead.owner_id,
            to_user_id=to_user_id,
            reason=reason,
            operator_id=operator_id,
        )
    )


async def assign_lead(
    session: AsyncSession,
    lead: Lead,
    *,
    to_user_id: int | None,
    operator_id: int,
    reason: str | None,
) -> None:
    record_assignment(
        session, lead=lead, to_user_id=to_user_id, operator_id=operator_id, reason=reason
    )
    lead.owner_id = to_user_id
    if to_user_id is None:
        lead.status = "pending" if lead.status != "converted" else lead.status
    elif lead.status in ("pending", "assigned"):
        lead.status = "assigned"


def mark_discarded(session: AsyncSession, lead: Lead, *, reason: str, operator_id: int) -> None:
    lead.status = "invalid"
    lead.invalid_reason = reason
    lead.deleted_at = datetime.now(UTC)
    record_assignment(
        session, lead=lead, to_user_id=None, operator_id=operator_id, reason=f"废弃：{reason}"
    )
