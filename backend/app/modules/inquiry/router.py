"""定制询价库接口（领导模块③：产品知识库 · 定制询价类）。"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok, page_data, paginate
from app.modules.customer.model import Customer
from app.modules.inquiry import service as svc
from app.modules.inquiry.model import CustomInquiry
from app.modules.inquiry.schema import (
    CustomInquiryCreate,
    CustomInquiryRevise,
    CustomInquiryUpdate,
)
from app.modules.user.model import User

router = APIRouter(tags=["CustomInquiry"])


async def _ctx_names(session: AsyncSession, rows: list[CustomInquiry]) -> dict[int, dict]:
    customer_ids = [r.customer_id for r in rows if r.customer_id]
    creator_ids = [r.created_by for r in rows if r.created_by]
    customer_names: dict[int, str] = {}
    if customer_ids:
        rows_ = await session.execute(
            select(Customer.id, Customer.name).where(Customer.id.in_(customer_ids))
        )
        customer_names = {cid: name for cid, name in rows_.all()}
    creator_names: dict[int, str] = {}
    if creator_ids:
        rows_ = await session.execute(select(User.id, User.name).where(User.id.in_(creator_ids)))
        creator_names = {uid: name for uid, name in rows_.all()}
    return {
        r.id: {
            "customer_name": customer_names.get(r.customer_id),
            "creator_name": creator_names.get(r.created_by),
        }
        for r in rows
    }


@router.get("/custom-inquiries")
async def list_inquiries(
    status: str | None = None,
    keyword: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user: CurrentUser = Depends(require_permission("quote:view")),
    session: AsyncSession = Depends(get_db),
):
    stmt = svc.not_deleted(select(CustomInquiry))
    if status:
        svc.ensure_status(status)
        stmt = stmt.where(CustomInquiry.status == status)
    if keyword:
        stmt = stmt.where(CustomInquiry.title.ilike(f"%{keyword}%"))
    stmt = await svc.apply_scope(stmt, user, session)
    stmt = stmt.order_by(CustomInquiry.id.desc())
    rows, total = await paginate(session, stmt, page, page_size)
    ctx = await _ctx_names(session, rows)
    return ok(
        page_data(
            [svc.serialize(r, **ctx.get(r.id, {})) for r in rows],
            total,
            page,
            page_size,
        )
    )


@router.get("/custom-inquiries/status-summary")
async def status_summary(
    user: CurrentUser = Depends(require_permission("quote:view")),
    session: AsyncSession = Depends(get_db),
):
    """各状态条数（待评估/开发中/已转商机/已归档），页面顶部一排徽章。"""
    stmt = svc.not_deleted(
        select(CustomInquiry.status, func.count()).group_by(CustomInquiry.status)
    )
    stmt = await svc.apply_scope(stmt, user, session)
    counts = {status: int(n) for status, n in (await session.execute(stmt)).all()}
    return ok(
        [
            {"status": status, "label": label, "count": counts.get(status, 0)}
            for status, label in svc.STATUS_LABELS.items()
        ]
    )


@router.post("/custom-inquiries")
async def create_inquiry(
    payload: CustomInquiryCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("quote:manage")),
    session: AsyncSession = Depends(get_db),
):
    if payload.customer_id:
        if await session.get(Customer, payload.customer_id) is None:
            raise AppError(ErrorCode.NOT_FOUND, "客户不存在", 404)
    inquiry = CustomInquiry(
        title=payload.title.strip(),
        description=payload.description,
        customer_id=payload.customer_id,
        contact_id=payload.contact_id,
        quantity=payload.quantity,
        target_price=payload.target_price,
        remark=payload.remark,
        status="open",
        created_by=user.id,
    )
    session.add(inquiry)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="create",
        business_type="custom_inquiry",
        business_id=inquiry.id,
        after=svc.serialize(inquiry),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize(inquiry), "定制询价已记录")


@router.patch("/custom-inquiries/{inquiry_id}")
async def update_inquiry(
    inquiry_id: int,
    payload: CustomInquiryUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("quote:manage")),
    session: AsyncSession = Depends(get_db),
):
    inquiry = await svc.get_visible_or_404(session, user, inquiry_id)
    data = payload.model_dump(exclude_unset=True)
    if "status" in data and data["status"] is not None:
        svc.ensure_status(data["status"])
    for field, value in data.items():
        setattr(inquiry, field, value)
    await session.flush()
    await session.refresh(inquiry)  # updated_at 是 onupdate 服务端值，flush 后已过期，先刷新再序列化
    await write_audit(
        session,
        operator_id=user.id,
        action="update",
        business_type="custom_inquiry",
        business_id=inquiry.id,
        after=svc.serialize(inquiry),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize(inquiry), "已保存")


@router.delete("/custom-inquiries/{inquiry_id}")
async def delete_inquiry(
    inquiry_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("quote:manage")),
    session: AsyncSession = Depends(get_db),
):
    inquiry = await svc.get_visible_or_404(session, user, inquiry_id)
    inquiry.deleted_at = svc.now()
    await write_audit(
        session,
        operator_id=user.id,
        action="delete",
        business_type="custom_inquiry",
        business_id=inquiry.id,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(None, "已删除")


@router.post("/custom-inquiries/{inquiry_id}/revise")
async def revise_inquiry(
    inquiry_id: int,
    payload: CustomInquiryRevise,
    request: Request,
    user: CurrentUser = Depends(require_permission("quote:manage")),
    session: AsyncSession = Depends(get_db),
):
    """客户改了要求 → 新增一版（版本号 +1、留修订说明），旧版原样保留。

    §3.3："客户改了三次要求，系统只留最新一版，看不出怎么变的"——这一把修的就是它。
    """
    old = await svc.get_visible_or_404(session, user, inquiry_id)

    new_version = CustomInquiry(
        title=payload.title or old.title,
        description=payload.description if payload.description is not None else old.description,
        customer_id=old.customer_id,
        contact_id=old.contact_id,
        opportunity_id=old.opportunity_id,
        quantity=payload.quantity if payload.quantity is not None else old.quantity,
        target_price=(
            payload.target_price if payload.target_price is not None else old.target_price
        ),
        # 新一版回到"待评估"：改过要求就得重新看
        status="open",
        remark=payload.remark if payload.remark is not None else old.remark,
        version=(old.version or 1) + 1,
        root_id=old.root_id or old.id,
        revision_note=payload.revision_note,
        created_by=user.id,
    )
    session.add(new_version)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="revise",
        business_type="custom_inquiry",
        business_id=new_version.id,
        after={"from_id": old.id, "version": new_version.version,
               "revision_note": payload.revision_note},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize(new_version), f"已生成 v{new_version.version}，旧版保留")


@router.get("/custom-inquiries/{inquiry_id}/history")
async def inquiry_history(
    inquiry_id: int,
    user: CurrentUser = Depends(require_permission("quote:view")),
    session: AsyncSession = Depends(get_db),
):
    """整条修订链：从 v1 到最新，按版本升序（先看最早的原始要求）。"""
    current = await svc.get_visible_or_404(session, user, inquiry_id)
    root_id = current.root_id or current.id
    rows = (
        await session.execute(
            svc.not_deleted(select(CustomInquiry))
            .where((CustomInquiry.id == root_id) | (CustomInquiry.root_id == root_id))
            .order_by(CustomInquiry.version.asc(), CustomInquiry.id.asc())
        )
    ).scalars().all()
    ctx = await _ctx_names(session, rows)
    return ok([svc.serialize(r, **ctx.get(r.id, {})) for r in rows])


@router.get("/custom-inquiries/{inquiry_id}")
async def get_inquiry(
    inquiry_id: int,
    user: CurrentUser = Depends(require_permission("quote:view")),
    session: AsyncSession = Depends(get_db),
):
    """单条详情（前端修订表单用它回填当前版内容）。"""
    inquiry = await svc.get_visible_or_404(session, user, inquiry_id)
    ctx = await _ctx_names(session, [inquiry])
    return ok(svc.serialize(inquiry, **ctx.get(inquiry.id, {})))
