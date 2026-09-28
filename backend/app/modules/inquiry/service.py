"""定制询价库业务逻辑：列表（数据范围过滤）+ 状态流转。"""

from datetime import UTC, datetime

from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.core.errors import AppError, ErrorCode
from app.core.data_scope import scoped_owner_ids
from app.modules.customer.model import Customer
from app.modules.inquiry.model import STATUS_LABELS, CustomInquiry

VALID_STATUSES = set(STATUS_LABELS)


def not_deleted(stmt: Select) -> Select:
    return stmt.where(CustomInquiry.deleted_at.is_(None))


async def apply_scope(stmt: Select, user: CurrentUser, session: AsyncSession) -> Select:
    """数据范围：数据范围=all 看全部；否则本范围客户 + 自己创建的 + 未挂客户的记录。"""
    if user.data_scope == "all":
        return stmt
    stmt = stmt.outerjoin(Customer, Customer.id == CustomInquiry.customer_id)
    owner_ids = await scoped_owner_ids(session, user)
    return stmt.where(
        or_(
            CustomInquiry.created_by == user.id,
            Customer.owner_id.in_(owner_ids or [0]),
            Customer.owner_id.is_(None),
            CustomInquiry.customer_id.is_(None),
        )
    )


async def get_visible_or_404(
    session: AsyncSession, user: CurrentUser, inquiry_id: int
) -> CustomInquiry:
    row = await session.get(CustomInquiry, inquiry_id)
    if row is None or row.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "定制询价不存在", 404)
    if user.data_scope != "all":
        stmt = await apply_scope(
            select(CustomInquiry.id).where(CustomInquiry.id == inquiry_id), user, session
        )
        if (await session.execute(stmt)).scalar_one_or_none() is None:
            raise AppError(ErrorCode.DATA_SCOPE_DENIED, "无权查看该定制询价", 403)
    return row


def ensure_status(status: str) -> None:
    if status not in VALID_STATUSES:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"状态必须是 {'/'.join(VALID_STATUSES)}",
            422,
        )


def serialize(
    inquiry: CustomInquiry,
    *,
    customer_name: str | None = None,
    creator_name: str | None = None,
) -> dict:
    return {
        "id": inquiry.id,
        "title": inquiry.title,
        "description": inquiry.description,
        "customer_id": inquiry.customer_id,
        "customer_name": customer_name,
        "contact_id": inquiry.contact_id,
        "opportunity_id": inquiry.opportunity_id,
        "quantity": float(inquiry.quantity) if inquiry.quantity is not None else None,
        "target_price": float(inquiry.target_price) if inquiry.target_price is not None else None,
        "status": inquiry.status,
        "status_label": STATUS_LABELS.get(inquiry.status, inquiry.status),
        # 修订链（§3.3）：第几版、本版改了什么、链条首版、投产后关联的 SKU
        "version": inquiry.version or 1,
        "root_id": inquiry.root_id,
        "revision_note": inquiry.revision_note,
        "converted_sku_id": inquiry.converted_sku_id,
        "remark": inquiry.remark,
        "created_by": inquiry.created_by,
        "creator_name": creator_name,
        "created_at": inquiry.created_at,
        "updated_at": inquiry.updated_at,
    }


def now() -> datetime:
    return datetime.now(UTC)
