"""客户中心与联系人接口（对齐 03-API §7 / §8）。"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.errors import ErrorCode, AppError
from app.core.response import ok, page_data, paginate
from app.modules.customer import service as svc
from app.modules.customer import tags as tag_svc
from app.modules.customer.model import Contact, Customer
from app.modules.customer.schema import (
    ContactCreate,
    ContactUpdate,
    CustomerCreate,
    CustomerTransfer,
    CustomerUpdate,
)

router = APIRouter(tags=["Customer"])


# ---------------------------------------------------------------- 客户

@router.get("/customers")
async def list_customers(
    keyword: str | None = None,
    level: str | None = None,
    status: str | None = None,
    source: str | None = None,
    owner_id: int | None = None,
    pool_status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user: CurrentUser = Depends(require_permission("customer:view")),
    session: AsyncSession = Depends(get_db),
):
    stmt = await svc.apply_data_scope(svc.not_deleted(svc.build_list_stmt(
        keyword=keyword,
        level=level,
        status=status,
        source=source,
        owner_id=owner_id,
        pool_status=pool_status,
    )), user, session)
    rows, total = await paginate(session, stmt, page, page_size)

    counts = await svc.contact_counts(session, [c.id for c in rows])
    owners = await svc.owner_names(session, [c.owner_id for c in rows])
    tag_map = await tag_svc.tags_of_customers(session, [c.id for c in rows])
    items = [
        svc.serialize_customer(
            c,
            owner_name=owners.get(c.owner_id) if c.owner_id else None,
            contact_count=counts.get(c.id, 0),
            tags=tag_map.get(c.id, []),
        )
        for c in rows
    ]
    return ok(page_data(items, total, page, page_size))


@router.post("/customers")
async def create_customer(
    payload: CustomerCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("customer:create")),
    session: AsyncSession = Depends(get_db),
):
    data = payload.model_dump()
    customer = await svc.create_customer(session, user, data)
    await write_audit(
        session,
        operator_id=user.id,
        action="create",
        business_type="customer",
        business_id=customer.id,
        after=svc.serialize_customer(customer),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_customer(customer), "客户已创建")


@router.get("/customers/{customer_id}")
async def get_customer(
    customer_id: int,
    user: CurrentUser = Depends(require_permission("customer:view")),
    session: AsyncSession = Depends(get_db),
):
    customer = await svc.get_customer_or_404(session, customer_id)
    owners = await svc.owner_names(session, [customer.owner_id])
    counts = await svc.contact_counts(session, [customer.id])
    tag_map = await tag_svc.tags_of_customers(session, [customer.id])
    return ok(
        svc.serialize_customer(
            customer,
            owner_name=owners.get(customer.owner_id) if customer.owner_id else None,
            contact_count=counts.get(customer.id, 0),
            tags=tag_map.get(customer.id, []),
        )
    )


@router.patch("/customers/{customer_id}")
async def update_customer(
    customer_id: int,
    payload: CustomerUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("customer:update")),
    session: AsyncSession = Depends(get_db),
):
    customer = await svc.get_customer_or_404(session, customer_id)
    before = svc.serialize_customer(customer)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(customer, field, value)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="update",
        business_type="customer",
        business_id=customer.id,
        before=before,
        after=svc.serialize_customer(customer),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_customer(customer), "已保存")


@router.delete("/customers/{customer_id}")
async def delete_customer(
    customer_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("customer:delete")),
    session: AsyncSession = Depends(get_db),
):
    customer = await svc.get_customer_or_404(session, customer_id)
    before = svc.serialize_customer(customer)
    await svc.delete_customer(session, customer)
    await write_audit(
        session,
        operator_id=user.id,
        action="delete",
        business_type="customer",
        business_id=customer.id,
        before=before,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(None, "客户已删除")


@router.post("/customers/{customer_id}/transfer")
async def transfer_customer(
    customer_id: int,
    payload: CustomerTransfer,
    request: Request,
    user: CurrentUser = Depends(require_permission("customer:assign")),
    session: AsyncSession = Depends(get_db),
):
    customer = await svc.get_customer_or_404(session, customer_id)
    before = svc.serialize_customer(customer)
    await svc.transfer_customer(session, user, customer, payload.owner_id, payload.reason)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="transfer",
        business_type="customer",
        business_id=customer.id,
        before=before,
        after=svc.serialize_customer(customer),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_customer(customer), "负责人已变更")


@router.post("/customers/{customer_id}/release-to-pool")
async def release_to_pool(
    customer_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("customer:assign")),
    session: AsyncSession = Depends(get_db),
):
    customer = await svc.get_customer_or_404(session, customer_id)
    await svc.transfer_customer(session, user, customer, None, "放入公海")
    await write_audit(
        session,
        operator_id=user.id,
        action="release_to_pool",
        business_type="customer",
        business_id=customer.id,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_customer(customer), "已放入公海")


@router.post("/customers/{customer_id}/claim")
async def claim_customer(
    customer_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("customer:view")),
    session: AsyncSession = Depends(get_db),
):
    customer = await svc.get_customer_or_404(session, customer_id)
    if customer.pool_status != "public":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "该客户不在公海，无法领取")
    await svc.transfer_customer(session, user, customer, user.id, "公海领取")
    await write_audit(
        session,
        operator_id=user.id,
        action="claim",
        business_type="customer",
        business_id=customer.id,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_customer(customer), "领取成功")


# ---------------------------------------------------------------- 联系人

@router.get("/customers/{customer_id}/contacts")
async def list_contacts(
    customer_id: int,
    user: CurrentUser = Depends(require_permission("customer:view")),
    session: AsyncSession = Depends(get_db),
):
    await svc.get_customer_or_404(session, customer_id)
    stmt = (
        select(Contact)
        .where(Contact.customer_id == customer_id, Contact.deleted_at.is_(None))
        .order_by(Contact.is_primary.desc(), Contact.id.asc())
    )
    rows = (await session.execute(stmt)).scalars().all()
    return ok([svc.serialize_contact(c) for c in rows])


@router.post("/customers/{customer_id}/contacts")
async def create_contact(
    customer_id: int,
    payload: ContactCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("customer:update")),
    session: AsyncSession = Depends(get_db),
):
    customer = await svc.get_customer_or_404(session, customer_id)
    contact = Contact(
        **payload.model_dump(),
        customer_id=customer.id,
        owner_id=customer.owner_id,
        source="手工录入",
    )
    session.add(contact)
    await session.flush()
    if contact.is_primary:
        await svc.set_primary_contact(session, contact)
    await write_audit(
        session,
        operator_id=user.id,
        action="create",
        business_type="contact",
        business_id=contact.id,
        after=svc.serialize_contact(contact),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_contact(contact), "联系人已创建")


@router.patch("/contacts/{contact_id}")
async def update_contact(
    contact_id: int,
    payload: ContactUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("customer:update")),
    session: AsyncSession = Depends(get_db),
):
    contact = await svc.get_contact_or_404(session, contact_id)
    before = svc.serialize_contact(contact)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(contact, field, value)
    await session.flush()
    if contact.is_primary:
        await svc.set_primary_contact(session, contact)
    await write_audit(
        session,
        operator_id=user.id,
        action="update",
        business_type="contact",
        business_id=contact.id,
        before=before,
        after=svc.serialize_contact(contact),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_contact(contact), "已保存")


@router.delete("/contacts/{contact_id}")
async def delete_contact(
    contact_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("customer:update")),
    session: AsyncSession = Depends(get_db),
):
    from datetime import UTC, datetime

    contact = await svc.get_contact_or_404(session, contact_id)
    before = svc.serialize_contact(contact)
    contact.deleted_at = datetime.now(UTC)
    await write_audit(
        session,
        operator_id=user.id,
        action="delete",
        business_type="contact",
        business_id=contact.id,
        before=before,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(None, "联系人已删除")
