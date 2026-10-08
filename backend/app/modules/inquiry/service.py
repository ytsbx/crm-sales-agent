"""定制询价库业务逻辑：列表（数据范围过滤）+ 状态流转。"""

from datetime import UTC, datetime

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.core.errors import AppError, ErrorCode
from app.core.data_scope import scoped_owner_ids
from app.modules.customer.model import Customer
from app.modules.inquiry.model import ORIGIN_LABELS, STATUS_LABELS, CustomInquiry

VALID_STATUSES = set(STATUS_LABELS)


def not_deleted(stmt: Select) -> Select:
    return stmt.where(CustomInquiry.deleted_at.is_(None))


async def apply_scope(stmt: Select, user: CurrentUser, session: AsyncSession) -> Select:
    """数据范围：数据范围=all 看全部；否则本范围客户 + 自己创建的 + 未挂客户的记录。

    第五批（§6.1(4)）在这里修掉一处**权限扩大**：原来"未挂客户"是一律放行的
    （`customer_id IS NULL`），而洞察转出来的内部开发需求正好也没有客户——
    于是市场研究资料变成"仅本人"范围的同事也看得到。

    现在按来源分开：
    - **客户询价**（含"客户还没定、先把需求记下来"的正常单据）：维持原样，
      未挂客户仍然可见（那是有意为之，业务上确实需要同事之间能接续）；
    - **内部开发需求**：只归提出者、**持有 `product:review` 的评审岗**、以及
      数据范围=all 的人。它本来就是"没客户"的，不能再拿"没客户"当公开的理由。

    为什么给评审岗开这个口子（口径已确认 2026-10-06）：内部开发需求是产品/开发岗
    之间要接着做的活，只认提出者的话，**换个人或换岗之后这些需求就没人看得到了**
    （只剩管理员能翻）。它们通篇不含客户数据，多开的这个可见面比客户资料小得多。
    """
    if user.data_scope == "all":
        return stmt
    stmt = stmt.outerjoin(Customer, Customer.id == CustomInquiry.customer_id)
    owner_ids = await scoped_owner_ids(session, user)
    conditions = [
        CustomInquiry.created_by == user.id,
        Customer.owner_id.in_(owner_ids or [0]),
        and_(
            CustomInquiry.origin == "customer",
            or_(
                Customer.owner_id.is_(None),
                CustomInquiry.customer_id.is_(None),
            ),
        ),
    ]
    if user.has("product:review"):
        # 评审岗之间共享内部开发需求。**只放这一类**——客户询价仍按上面那三条，
        # 别把评审权当成"看更多客户数据的通行证"。
        conditions.append(CustomInquiry.origin == "internal_dev")
    return stmt.where(or_(*conditions))


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
        # 来源类型（第五批 §6.1(3)）：客户询价 / 内部开发需求。
        # 列表上要能一眼分辨——内部开发需求**不是**"客户已经提出采购需求"，
        # 拿它去报价或承诺交期就搞错了对象。
        "origin": inquiry.origin or "customer",
        "origin_label": ORIGIN_LABELS.get(inquiry.origin or "customer", inquiry.origin),
        "source_insight_id": inquiry.source_insight_id,
        # 结构化来源（洞察转过来的那些：insight_id / insight_source /
        # insight_target_customer）。
        # 为什么必须返回：`source_insight_id` 带部分唯一索引（一个洞察只能转出
        # 一条需求），所以**修订出的新版本不能再占一份**，它只挂在链条首版上。
        # 若这里不回 `extra`，V2 的回链就彻底看不出来了——而"这条需求是从哪条
        # 洞察转来的"正是内部开发需求唯一的价值锚点。`extra` 会随修订复制。
        "extra": inquiry.extra,
        # 修订链（§3.3）：第几版、本版改了什么、链条首版、投产后关联的 SKU
        "version": inquiry.version or 1,
        "root_id": inquiry.root_id,
        "revision_note": inquiry.revision_note,
        # 「已被新版取代」：取代是**版本属性**，不覆盖业务 status（见 model 注释）
        "superseded_at": inquiry.superseded_at,
        "is_superseded": inquiry.superseded_at is not None,
        # 三态，删除排最前（第十二批 12.6）：删掉的 V2 头上并没有"已被取代"
        # 标记（删它的时候它还是当前版），不加这一层会被显示成"当前版"。
        "version_state_label": (
            "已删除"
            if inquiry.deleted_at is not None
            else ("已被新版取代" if inquiry.superseded_at else "当前版")
        ),
        "converted_sku_id": inquiry.converted_sku_id,
        "remark": inquiry.remark,
        "created_by": inquiry.created_by,
        "creator_name": creator_name,
        "created_at": inquiry.created_at,
        "updated_at": inquiry.updated_at,
        # 软删标记（第十二批 12.6）：历史链要把**已删除的版本**也列出来并标出来。
        # 删掉 V2 之后页面上是 V1 → V3，中间少一版会让人以为系统吃了个号；
        # 列出 V2 并标"已删除"，"为什么复用不了 2 号"才一眼说得清。
        "deleted_at": inquiry.deleted_at,
        "is_deleted": inquiry.deleted_at is not None,
    }


def now() -> datetime:
    return datetime.now(UTC)


async def refresh_chain_current_state(
    session: AsyncSession, root_id: int, *, stamp: datetime
) -> None:
    """删掉某一版之后，重新点名"当前有效版"（第十二批 12.6）。

    一条需求的"当前版"= **还活着的版本里编号最大的那一个**：
    - 它不该带"已被新版取代"标记；
    - 其余活着的版本都是历史版，都该带上。

    为什么删一版就得重算：删掉 V2 之后，V1 头上"已被新版取代"这句就成了
    无主之词 —— 取代它的 V2 已经不在了，而"当前有效版是谁"也无从判断。
    重算之后 V1 变回当前版，"以 V1 为基础生成 V3"才名正言顺。

    只碰"活着的"行；已删除的版本原样保留（不恢复、不覆盖）。
    """
    rows = (
        await session.execute(
            not_deleted(select(CustomInquiry))
            .where((CustomInquiry.id == root_id) | (CustomInquiry.root_id == root_id))
            .order_by(CustomInquiry.version.asc())
            .execution_options(populate_existing=True)
        )
    ).scalars().all()
    if not rows:
        return
    current = max(rows, key=lambda r: r.version or 1)
    for row in rows:
        if row is current:
            row.superseded_at = None
        elif row.superseded_at is None:
            row.superseded_at = stamp


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
