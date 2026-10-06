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
    CustomInquiryQuoteRequest,
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
    opportunity_id: int | None = None,
    customer_id: int | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user: CurrentUser = Depends(require_permission("quote:view")),
    session: AsyncSession = Depends(get_db),
):
    stmt = svc.not_deleted(select(CustomInquiry))
    if opportunity_id is not None:
        stmt = stmt.where(CustomInquiry.opportunity_id == opportunity_id)
    if customer_id is not None:
        stmt = stmt.where(CustomInquiry.customer_id == customer_id)
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
    customer_id = await svc.validate_links(session, user, customer_id=payload.customer_id,
                                           opportunity_id=payload.opportunity_id,
                                           contact_id=payload.contact_id)
    inquiry = CustomInquiry(
        # 需求编号（场景09）：定制件在打样投产前没有 SKU，报价与打样要靠
        # 这个编号指向同一条需求，否则"这张报价是从哪来的"无从追溯
        inquiry_no=await svc.generate_inquiry_no(session),
        title=payload.title.strip(),
        description=payload.description,
        customer_id=customer_id,
        opportunity_id=payload.opportunity_id,
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


@router.post("/custom-inquiries/{inquiry_id}/create-quote")
async def create_quote_from_inquiry(
    inquiry_id: int,
    payload: CustomInquiryQuoteRequest,
    request: Request,
    user: CurrentUser = Depends(require_permission("quote:manage")),
    session: AsyncSession = Depends(get_db),
):
    """从定制需求直接发起报价（§3.1/场景09）。

    定制件投产前没有 SKU，报价中心按 SKU 选品选不到它；这个出口把
    需求 → 商机 → 报价 → 定制明细一步串起，销售只填核价成本与报价。
    """
    inquiry = await svc.get_visible_or_404(session, user, inquiry_id)
    created = await svc.create_quote_from_inquiry(
        session,
        inquiry=inquiry,
        user=user,
        unit_cost=payload.unit_cost,
        quoted_price=payload.quoted_price,
        quantity=payload.quantity,
        item_name=payload.item_name,
        valid_until=payload.valid_until,
    )
    quote, version, item = created["quote"], created["version"], created["item"]
    await write_audit(
        session,
        operator_id=user.id,
        action="create_quote",
        business_type="custom_inquiry",
        business_id=inquiry.id,
        after={
            "quote_id": quote.id,
            "quote_no": quote.quote_no,
            "version_id": version.id,
            "inquiry_no": inquiry.inquiry_no,
            "quoted_price": str(item.quoted_price),
        },
        ip=client_ip(request),
    )
    await session.commit()
    return ok(
        {
            "quote_id": quote.id,
            "quote_no": quote.quote_no,
            "version_id": version.id,
            "opportunity_id": created["opportunity"].id,
            "inquiry_no": inquiry.inquiry_no,
            # 直接把"这一价是不是低了"告诉界面，省得销售回报价页才发现要审批
            "quoted_price": float(item.quoted_price),
            "minimum_price": (
                float(item.minimum_price_snapshot)
                if item.minimum_price_snapshot is not None
                else None
            ),
            "approval_required": item.approval_required,
        },
        f"已按需求 {inquiry.inquiry_no or inquiry.id} 生成报价 {quote.quote_no}",
    )


@router.patch("/custom-inquiries/{inquiry_id}")
async def update_inquiry(
    inquiry_id: int,
    payload: CustomInquiryUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("quote:manage")),
    session: AsyncSession = Depends(get_db),
):
    inquiry = await svc.get_visible_or_404(session, user, inquiry_id)
    await session.execute(select(CustomInquiry).where(
        CustomInquiry.id == (inquiry.root_id or inquiry.id)
    ).with_for_update())
    await session.refresh(inquiry)
    data = payload.model_dump(exclude_unset=True)
    if "status" in data and data["status"] is not None:
        svc.ensure_status(data["status"])
    # 改归属同样要过数据范围：不然先把需求建在自己客户上、再 PATCH 客户/商机编号，
    # 一样能把别人的客户与商机挂进来（创建路径的校验挡不住这一步）
    if any(field in data for field in ("customer_id", "opportunity_id", "contact_id")):
        data["customer_id"] = await svc.validate_links(
            session, user, customer_id=data.get("customer_id", inquiry.customer_id),
            opportunity_id=data.get("opportunity_id", inquiry.opportunity_id),
            contact_id=data.get("contact_id", inquiry.contact_id),
        )
    # 字段归属（口径 2026-10-04）：链级字段（客户/联系人/商机/对接报价员）对整条
    # 需求生效，写到链条每一版；版本级字段（标题/描述/数量/目标价/状态/备注）只许
    # 改当前版——历史版本已被新版取代，直接改它等于"悄悄改历史"，要改请走修订。
    from app.modules.inquiry.model import CHAIN_FIELDS

    chain_data = {k: v for k, v in data.items() if k in CHAIN_FIELDS}
    version_data = {k: v for k, v in data.items() if k not in CHAIN_FIELDS}
    if version_data and inquiry.superseded_at is not None:
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            "这是已被新版取代的历史版本，内容不能直接改；请到最新版上改，"
            "或对最新版发起修订",
            409,
        )
    if chain_data:
        root_id = inquiry.root_id or inquiry.id
        chain_rows = (
            await session.execute(
                svc.not_deleted(select(CustomInquiry)).where(
                    (CustomInquiry.id == root_id) | (CustomInquiry.root_id == root_id)
                )
            )
        ).scalars().all()
        for row in chain_rows:
            for field, value in chain_data.items():
                setattr(row, field, value)
    for field, value in version_data.items():
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
    await session.execute(select(CustomInquiry).where(
        CustomInquiry.id == (old.root_id or old.id)
    ).with_for_update())
    await session.refresh(old)

    # 只能对**链条最新版**再修订：此前不校验，在 v1 上连点两次就生成两条 v2，
    # 历史视图里同版并列，分不清哪条才是当前要求。最新版 = 链条里 version 最大者。
    root_id = old.root_id or old.id
    latest_version = (
        await session.execute(
            svc.not_deleted(select(func.max(CustomInquiry.version))).where(
                (CustomInquiry.id == root_id) | (CustomInquiry.root_id == root_id)
            )
        )
    ).scalar_one_or_none()
    if latest_version is not None and (old.version or 1) < int(latest_version):
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"只能对最新版（v{int(latest_version)}）再修订；当前这条是 v{old.version or 1}，"
            f"版本只增不改，请到最新版上发起修订",
            409,
        )

    # 编号跟着**需求**走，不跟着版本走：v2 是同一需求的新一版，换号会让
    # 已发出的报价断在中间。历史数据没有 root 号时才补取一个（迁移前的行）。
    chain_no = old.inquiry_no
    if chain_no is None and old.root_id:
        root = await session.get(CustomInquiry, old.root_id)
        chain_no = root.inquiry_no if root else None
    if chain_no is None:
        chain_no = await svc.generate_inquiry_no(session)
        # 补号时把首版一起补上：否则首版停在 NULL，同一 root 会出现"两条各一套编号"。
        old.inquiry_no = chain_no

    new_version = CustomInquiry(
        inquiry_no=chain_no,
        title=payload.title or old.title,
        description=payload.description if payload.description is not None else old.description,
        customer_id=old.customer_id,
        contact_id=old.contact_id,
        opportunity_id=old.opportunity_id,
        quantity=payload.quantity if payload.quantity is not None else old.quantity,
        target_price=(
            payload.target_price if payload.target_price is not None else old.target_price
        ),
        # **来源必须跟着走**（2026-10-06 修）：这里原来三项都没复制，
        # 于是 `origin` 回落成模型默认的 `customer`——一条"内部开发需求"
        # 只要被修订一次，性质就变成了普通客户询价，而 `apply_scope` 对
        # "没挂客户的客户询价"是放行的，**等于修订一下就对全体同事开放了**。
        # 这是权限泄露，不是显示问题。
        origin=old.origin,
        # `extra` 里存的是结构化来源（insight_id / insight_source /
        # insight_target_customer）。不复制的话，修订后的版本看不出它是从哪条
        # 洞察转来的，而"这条需求怎么来的"正是内部开发需求唯一的价值锚点。
        extra=old.extra,
        # `source_insight_id` **刻意不复制**：它在迁移 a3f7c1e5b9d2 里带
        # 部分唯一索引（一个洞察只能转出一条需求），复制过来会直接撞唯一约束。
        # 它属于**链级**属性，留在链条首版上；回链要显示时通过 `extra.insight_id`
        # 或沿 root_id 找首版，两者都在（前者已随 extra 复制过来）。
        status="open",
        remark=payload.remark if payload.remark is not None else old.remark,
        version=(old.version or 1) + 1,
        root_id=old.root_id or old.id,
        revision_note=payload.revision_note,
        created_by=user.id,
    )
    session.add(new_version)
    await session.flush()
    # 旧版打上"已被新版取代"。历史版本永不覆盖，但要能一眼看出哪版是旧的。
    old.superseded_at = svc.now()
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
    """整条修订链：从 v1 到最新，按版本升序（先看最早的原始要求）。

    **逐版本按同一套可见性规则过滤**（2026-10-06 修）。原来只校验入口那一版的
    权限、随后把整条链原样返回——于是只要能看见链条上**任意一版**，就能把
    更早的受限版本一起读出来。典型的走法：内部开发需求的 V1 被某一版修成了
    普通客户询价（见 `revise_inquiry` 那个已修的 bug），拿可见的 V2 进来，
    整条链连同 V1 的内部资料一并返回。

    链条上各版本来就应该同源（`origin` 现在会跟着修订走），这里的过滤是
    **第二道防线**：即使数据因为历史原因同链不同源，也只返回看得到的那几版。
    """
    current = await svc.get_visible_or_404(session, user, inquiry_id)
    root_id = current.root_id or current.id
    stmt = svc.not_deleted(select(CustomInquiry)).where(
        (CustomInquiry.id == root_id) | (CustomInquiry.root_id == root_id)
    )
    stmt = await svc.apply_scope(stmt, user, session)
    rows = (
        await session.execute(
            stmt.order_by(CustomInquiry.version.asc(), CustomInquiry.id.asc())
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
