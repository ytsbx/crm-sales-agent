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


async def validate_links(session: AsyncSession, user: CurrentUser, *, customer_id,
                         opportunity_id, contact_id=None):
    """需求的客户、商机、联系人必须指向同一笔客户业务，并校验数据范围。"""
    from app.modules.customer import service as customer_service
    from app.modules.customer.model import Contact
    from app.modules.opportunity import service as opportunity_service

    if opportunity_id:
        opportunity = await opportunity_service.get_visible_opportunity(session, user, opportunity_id)
        if customer_id is not None and customer_id != opportunity.customer_id:
            raise AppError(ErrorCode.PARAM_ERROR, "关联商机不属于该客户", 422)
        customer_id = opportunity.customer_id
    if customer_id:
        await customer_service.get_visible_customer(session, user, customer_id)
    if contact_id:
        contact = await session.get(Contact, contact_id)
        if contact is None or contact.deleted_at is not None:
            raise AppError(ErrorCode.NOT_FOUND, "联系人不存在", 404)
        if contact.customer_id != customer_id:
            raise AppError(ErrorCode.PARAM_ERROR, "联系人不属于该客户", 422)
    return customer_id


def serialize(
    inquiry: CustomInquiry,
    *,
    customer_name: str | None = None,
    creator_name: str | None = None,
) -> dict:
    return {
        "id": inquiry.id,
        # 需求编号：报价/打样明细上要能对回这条需求（场景09）
        "inquiry_no": inquiry.inquiry_no,
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
        # 「已被新版取代」：取代是**版本属性**，不覆盖业务 status（见 model 注释）
        "superseded_at": inquiry.superseded_at,
        "is_superseded": inquiry.superseded_at is not None,
        "version_state_label": "已被新版取代" if inquiry.superseded_at else "当前版",
        "converted_sku_id": inquiry.converted_sku_id,
        "remark": inquiry.remark,
        "created_by": inquiry.created_by,
        "creator_name": creator_name,
        "created_at": inquiry.created_at,
        "updated_at": inquiry.updated_at,
    }


def now() -> datetime:
    return datetime.now(UTC)


async def generate_inquiry_no(session: AsyncSession) -> str:
    """取需求编号（场景09：尚无正式 SKU 时，报价/打样靠它溯源）。

    复用 settings/numbering 的取号器（行锁计数器 + 播种 + 撞号跳过），
    与报价单号/订单号同一套机制——不另写一份 count(*)+1，那种写法会重号。
    """
    from app.modules.settings import numbering

    return await numbering.generate_for(
        session, "inquiry", model=CustomInquiry, column=CustomInquiry.inquiry_no
    )


async def create_quote_from_inquiry(
    session: AsyncSession,
    *,
    inquiry: CustomInquiry,
    user,
    unit_cost,
    quoted_price,
    quantity=None,
    item_name: str | None = None,
    valid_until=None,
) -> dict:
    """从定制需求直接发起报价（文档 §3.1「从需求页可发起询价、报价、打样」/场景09）。

    为什么需要这个出口：定制件在投产前没有 SKU，报价中心的「选品下单」
    按 SKU 选品，选不到它。这里把 需求 → 商机 → 报价 → 定制明细 串成一步，
    销售只需要填核价成本与报价两个数。

    没有商机时顺手建一个（标题用需求名）并回记到需求上——"报价必须关联商机"
    是既有口径，不该让销售先去别处建一遍再回来。
    """
    from app.modules.opportunity import service as opportunity_service
    from app.modules.opportunity.model import Opportunity, OpportunityStageHistory
    from app.modules.quote import service as quote_service

    # 同一修订链同时转报价，只能创建一条归属商机；链级关联对所有版本一致。
    root_id = inquiry.root_id or inquiry.id
    root = (await session.execute(select(CustomInquiry).where(CustomInquiry.id == root_id)
            .with_for_update().execution_options(populate_existing=True))).scalar_one()
    await session.refresh(inquiry)
    chain = (await session.execute(not_deleted(select(CustomInquiry)).where(
        (CustomInquiry.id == root_id) | (CustomInquiry.root_id == root_id)
    ).execution_options(populate_existing=True))).scalars().all()
    existing_ids = {row.opportunity_id for row in chain if row.opportunity_id}
    if len(existing_ids) > 1:
        raise AppError(ErrorCode.PARAM_ERROR, "该修订链关联了多个商机，请先统一关联商机再报价", 422)
    # 兼容旧数据：以前只回写被报价引用的那一版，不丢掉它已有的商机关联。
    inquiry.opportunity_id = next(iter(existing_ids), root.opportunity_id)
    await validate_links(session, user, customer_id=inquiry.customer_id,
                         opportunity_id=inquiry.opportunity_id, contact_id=inquiry.contact_id)
    if not inquiry.customer_id:
        raise AppError(ErrorCode.PARAM_ERROR, "需求还没关联客户，先补客户再报价", 422)
    customer = await session.get(Customer, inquiry.customer_id)
    if customer is None or customer.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "需求关联的客户不存在", 404)

    opportunity = (await opportunity_service.get_visible_opportunity(session, user, inquiry.opportunity_id)
                   if inquiry.opportunity_id else None)
    if opportunity is None:
        stage = await opportunity_service.get_first_stage(session)
        opportunity = Opportunity(
            customer_id=customer.id,
            primary_contact_id=inquiry.contact_id,
            title=inquiry.title,
            stage_id=stage.id,
            owner_id=customer.owner_id or user.id,
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
                remark="定制需求转报价",
                entered_at=now(),
            )
        )
    for row in chain:
        row.opportunity_id = opportunity.id

    created = await quote_service.create_quote(
        session,
        user=user,
        opportunity=opportunity,
        customer_id=customer.id,
        contact_id=inquiry.contact_id,
        valid_until=valid_until,
    )
    quote, version = created["_quote"], created["_version"]
    item = await quote_service.build_item_snapshot(
        session,
        version=version,
        sku_id=None,  # 定制项：无 SKU，靠需求编号 + 人工核价
        quantity=quantity if quantity is not None else (inquiry.quantity or 1),
        customer_id=customer.id,
        quoted_price=quoted_price,
        logistics_cost=None,
        opportunity_item_id=None,
        spec_snapshot=None,
        remark=inquiry.description,
        role_codes=list(user.roles),
        inquiry_id=inquiry.id,
        item_name=item_name,
        unit_cost=unit_cost,
    )
    session.add(item)
    await session.flush()
    await quote_service.recalc_version(session, version)
    await session.flush()
    # 业务进展时钟（§2.3）：从需求发报价同样是客户在推进
    from app.modules.customer import service as customer_service

    await customer_service.touch_progress(session, customer.id)
    return {"quote": quote, "version": version, "item": item, "opportunity": opportunity}
