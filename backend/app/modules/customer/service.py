"""客户与联系人业务逻辑。"""

from datetime import UTC, datetime

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.core.errors import AppError, ErrorCode
from app.modules.customer.model import Contact, Customer, CustomerOwnerHistory
from app.modules.user.model import User


# ---------------------------------------------------------------- 查询条件

def apply_data_scope(stmt: Select, user: CurrentUser) -> Select:
    """按数据范围过滤客户（05-TECH §24：禁止只靠前端隐藏）。"""
    if user.data_scope == "all":
        return stmt
    if user.data_scope in ("department", "department_and_sub"):
        sub = select(User.id).where(User.department_id == user.department_id)
        return stmt.where(or_(Customer.owner_id.in_(sub), Customer.owner_id.is_(None)))
    return stmt.where(or_(Customer.owner_id == user.id, Customer.owner_id.is_(None)))


def not_deleted(stmt: Select) -> Select:
    return stmt.where(Customer.deleted_at.is_(None))


def build_list_stmt(
    keyword: str | None = None,
    level: str | None = None,
    status: str | None = None,
    source: str | None = None,
    owner_id: int | None = None,
    pool_status: str | None = None,
) -> Select:
    stmt = select(Customer)
    if keyword:
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(
            or_(
                Customer.name.ilike(like),
                Customer.short_name.ilike(like),
                Customer.domain.ilike(like),
                Customer.tax_no.ilike(like),
                Customer.address.ilike(like),
            )
        )
    if level:
        stmt = stmt.where(Customer.level == level)
    if status:
        stmt = stmt.where(Customer.status == status)
    if source:
        stmt = stmt.where(Customer.source == source)
    if owner_id is not None:
        stmt = stmt.where(Customer.owner_id == owner_id)
    if pool_status:
        stmt = stmt.where(Customer.pool_status == pool_status)
    return stmt.order_by(Customer.id.desc())


async def contact_counts(session: AsyncSession, customer_ids: list[int]) -> dict[int, int]:
    if not customer_ids:
        return {}
    stmt = (
        select(Contact.customer_id, func.count(Contact.id))
        .where(Contact.customer_id.in_(customer_ids), Contact.deleted_at.is_(None))
        .group_by(Contact.customer_id)
    )
    rows = (await session.execute(stmt)).all()
    return {int(cid): int(cnt) for cid, cnt in rows}


async def owner_names(session: AsyncSession, owner_ids: list[int]) -> dict[int, str]:
    ids = [oid for oid in owner_ids if oid]
    if not ids:
        return {}
    stmt = select(User.id, User.name).where(User.id.in_(ids))
    rows = (await session.execute(stmt)).all()
    return {int(uid): name for uid, name in rows}


def serialize_customer(
    customer: Customer,
    *,
    owner_name: str | None = None,
    contact_count: int = 0,
) -> dict:
    return {
        "id": customer.id,
        "name": customer.name,
        "short_name": customer.short_name,
        "customer_type": customer.customer_type,
        "country": customer.country,
        "region": customer.region,
        "address": customer.address,
        "domain": customer.domain,
        "tax_no": customer.tax_no,
        "source": customer.source,
        "level": customer.level,
        "status": customer.status,
        "pool_status": customer.pool_status,
        "owner_id": customer.owner_id,
        "owner_name": owner_name,
        "contact_count": contact_count,
        "remark": customer.remark,
        "last_followup_at": customer.last_followup_at,
        "next_followup_at": customer.next_followup_at,
        "created_at": customer.created_at,
        "updated_at": customer.updated_at,
    }


def serialize_contact(contact: Contact) -> dict:
    return {
        "id": contact.id,
        "customer_id": contact.customer_id,
        "name": contact.name,
        "title": contact.title,
        "department": contact.department,
        "mobile": contact.mobile,
        "phone": contact.phone,
        "email": contact.email,
        "wechat": contact.wechat,
        "is_primary": contact.is_primary,
        "owner_id": contact.owner_id,
        "source": contact.source,
        "remark": contact.remark,
        "created_at": contact.created_at,
    }


# ---------------------------------------------------------------- 客户操作

async def get_customer_or_404(session: AsyncSession, customer_id: int) -> Customer:
    customer = await session.get(Customer, customer_id)
    if customer is None or customer.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "客户不存在", 404)
    return customer


async def create_customer(
    session: AsyncSession,
    user: CurrentUser,
    payload: dict,
) -> Customer:
    payload = dict(payload)
    # 默认负责人是自己；管理员可以指定他人
    payload.setdefault("owner_id", user.id)
    customer = Customer(**payload, created_by=user.id)
    session.add(customer)
    await session.flush()
    return customer


async def transfer_customer(
    session: AsyncSession,
    user: CurrentUser,
    customer: Customer,
    new_owner_id: int | None,
    reason: str | None,
) -> None:
    old_owner_id = customer.owner_id
    customer.owner_id = new_owner_id
    customer.pool_status = "public" if new_owner_id is None else "private"
    session.add(
        CustomerOwnerHistory(
            customer_id=customer.id,
            old_owner_id=old_owner_id,
            new_owner_id=new_owner_id,
            reason=reason,
            operator_id=user.id,
            created_at=datetime.now(UTC),
        )
    )


async def delete_customer(session: AsyncSession, customer: Customer) -> None:
    customer.deleted_at = datetime.now(UTC)


# ---------------------------------------------------------------- 联系人操作

async def get_contact_or_404(session: AsyncSession, contact_id: int) -> Contact:
    contact = await session.get(Contact, contact_id)
    if contact is None or contact.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "联系人不存在", 404)
    return contact


async def set_primary_contact(session: AsyncSession, contact: Contact) -> None:
    """主要联系人唯一：先把同客户下其它联系人取消标记。"""
    if contact.customer_id is None:
        return
    await session.execute(
        Contact.__table__.update()
        .where(Contact.customer_id == contact.customer_id, Contact.id != contact.id)
        .values(is_primary=False)
    )
    contact.is_primary = True


__all__ = [
    "apply_data_scope",
    "build_list_stmt",
    "contact_counts",
    "create_customer",
    "delete_customer",
    "get_contact_or_404",
    "get_customer_or_404",
    "not_deleted",
    "owner_names",
    "serialize_contact",
    "serialize_customer",
    "set_primary_contact",
    "transfer_customer",
]
