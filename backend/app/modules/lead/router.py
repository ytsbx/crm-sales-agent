"""线索中心接口（对齐 03-API §6）。"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok, page_data, paginate
from app.modules.contact_util import create_contact_for_customer, find_duplicate_customers
from app.modules.customer.model import Customer
from app.modules.lead import service as svc
from app.modules.lead.model import Lead
from app.modules.lead.schema import (
    LeadAssign,
    LeadBatchAssign,
    LeadConvert,
    LeadCreate,
    LeadDiscard,
    LeadUpdate,
)

router = APIRouter(tags=["Lead"])


@router.get("/leads")
async def list_leads(
    keyword: str | None = None,
    status: str | None = None,
    source: str | None = None,
    owner_id: int | None = None,
    unassigned: bool = False,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user: CurrentUser = Depends(require_permission("lead:view")),
    session: AsyncSession = Depends(get_db),
):
    stmt = await svc.apply_data_scope(
        svc.build_lead_stmt(
            keyword=keyword, status=status, source=source, owner_id=owner_id, unassigned=unassigned
        ),
        user,
        session,
    )
    rows, total = await paginate(session, stmt, page, page_size)
    owners = await svc.owner_names(session, [lead.owner_id for lead in rows])
    items = [
        svc.serialize_lead(lead, owner_name=owners.get(lead.owner_id) if lead.owner_id else None)
        for lead in rows
    ]
    return ok(page_data(items, total, page, page_size))


@router.post("/leads")
async def create_lead(
    payload: LeadCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("lead:create")),
    session: AsyncSession = Depends(get_db),
):
    data = payload.model_dump()
    if data.get("owner_id"):
        # 指定负责人是**归属类动作**：不校验范围，业务员就能把线索直接挂到别人
        # （甚至别的部门）名下。业务员是 self 范围，只能挂自己；主管可在本部门内分配。
        from app.core.data_scope import ensure_in_scope

        await ensure_in_scope(session, user, owner_id=data["owner_id"], label="负责人")
        data["status"] = "assigned"
    else:
        data["status"] = "pending"
    lead = Lead(**data, created_by=user.id)
    session.add(lead)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="create",
        business_type="lead",
        business_id=lead.id,
        after=svc.serialize_lead(lead),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_lead(lead), "线索已创建")


@router.get("/leads/{lead_id}")
async def get_lead(
    lead_id: int,
    user: CurrentUser = Depends(require_permission("lead:view")),
    session: AsyncSession = Depends(get_db),
):
    lead = await svc.get_visible_lead(session, user, lead_id)
    owners = await svc.owner_names(session, [lead.owner_id])
    return ok(svc.serialize_lead(lead, owner_name=owners.get(lead.owner_id) if lead.owner_id else None))


@router.patch("/leads/{lead_id}")
async def update_lead(
    lead_id: int,
    payload: LeadUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("lead:create")),
    session: AsyncSession = Depends(get_db),
):
    lead = await svc.get_visible_lead(session, user, lead_id)
    before = svc.serialize_lead(lead)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(lead, field, value)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="update",
        business_type="lead",
        business_id=lead.id,
        before=before,
        after=svc.serialize_lead(lead),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_lead(lead), "已保存")


@router.post("/leads/{lead_id}/assign")
async def assign_lead(
    lead_id: int,
    payload: LeadAssign,
    request: Request,
    user: CurrentUser = Depends(require_permission("lead:assign")),
    session: AsyncSession = Depends(get_db),
):
    lead = await svc.get_visible_lead(session, user, lead_id)
    before_owner = lead.owner_id
    await svc.assign_lead(
        session, lead, to_user_id=payload.owner_id, operator_id=user.id, reason=payload.reason
    )
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="assign",
        business_type="lead",
        business_id=lead.id,
        # 审计要能回答"原来归谁、改成了谁"（返工单 6.1 第 6 条）
        before={"owner_id": before_owner},
        after={"owner_id": lead.owner_id, "reason": payload.reason},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_lead(lead), "已分配")


@router.post("/leads/batch-assign")
async def batch_assign_leads(
    payload: LeadBatchAssign,
    request: Request,
    user: CurrentUser = Depends(require_permission("lead:assign")),
    session: AsyncSession = Depends(get_db),
):
    """批量分配线索（03-API §6）。

    与单条分配共用 `svc.assign_lead`，所以分配历史、通知与"不能分配给停用账号"
    这些规则都一致。单条失败不影响其余：返回成功/跳过清单，
    让操作的人知道哪几条没成、为什么。

    **批量不是绕过数据范围的旁路**：逐条走 `svc.assert_lead_visible`，
    与单条 `get_visible_lead` 同一口径 —— 越权的那条按"无权分配"跳过，
    **不改任何数据**，也不会因为"批量"就悄悄改成。
    结果里 `reason` 与 `code` 成对给出，和单条入口的拒绝原因一致。

    每条用 **SAVEPOINT** 包起来：某条中途失败时只回滚这一条，
    不会把前面成功的一起带走，也不会留下"历史写了、负责人没改"的半截状态。
    """
    if not payload.lead_ids:
        raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "lead_ids 不能为空", 422)
    # 去重但保持顺序，避免重复 id 造成重复记历史
    unique_ids = list(dict.fromkeys(payload.lead_ids))

    assigned: list[int] = []
    skipped: list[dict] = []
    for lead_id in unique_ids:
        try:
            async with session.begin_nested():  # SAVEPOINT：这一条失败只回滚这一条
                lead = (
                    await session.execute(
                        select(Lead).where(
                            Lead.id == lead_id, Lead.deleted_at.is_(None)
                        )
                    )
                ).scalars().first()
                if lead is None:
                    raise AppError(ErrorCode.NOT_FOUND, "线索不存在", 404)
                # 与单条入口同口径的范围校验（无权的那条在这里被拦下）
                await svc.assert_lead_visible(session, user, lead)
                await svc.assign_lead(
                    session,
                    lead,
                    to_user_id=payload.owner_id,
                    operator_id=user.id,
                    reason=payload.reason or "批量分配",
                )
        except AppError as error:
            # 逐条报出"为什么没成"，且 code 与单条入口一致，
            # 前端不用为批量单独写一套文案
            skipped.append(
                {"lead_id": lead_id, "reason": error.message, "code": error.code}
            )
            continue
        assigned.append(lead_id)

    await write_audit(
        session,
        operator_id=user.id,
        action="batch_assign",
        business_type="lead",
        business_id=None,
        after={"owner_id": payload.owner_id, "assigned": len(assigned), "skipped": len(skipped)},
        ip=client_ip(request),
    )
    await session.commit()
    message = f"已分配 {len(assigned)} 条"
    if skipped:
        message += f"，跳过 {len(skipped)} 条"
    return ok({"assigned": assigned, "skipped": skipped}, message)


@router.post("/leads/{lead_id}/claim")
async def claim_lead(
    lead_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("lead:view")),
    session: AsyncSession = Depends(get_db),
):
    """领取线索。

    与公海页面的 `POST /public-pool/leads/{id}/claim` 走**同一个服务函数**
    （`svc.claim_lead`）：行锁、可领取状态（已转客户/已废弃的不能领）、
    幂等、报错文案全部一致。此前两处各写一份，只判"有没有负责人"。
    """
    lead, claimed = await svc.claim_lead(session, user, lead_id, reason="线索池领取")
    if claimed:
        await write_audit(
            session,
            operator_id=user.id,
            action="claim",
            business_type="lead",
            business_id=lead.id,
            after={"owner_id": user.id},
            ip=client_ip(request),
        )
    await session.commit()
    return ok(
        svc.serialize_lead(lead),
        "领取成功" if claimed else f"线索「{lead.name}」已经是你的",
    )


@router.post("/leads/{lead_id}/release")
async def release_lead(
    lead_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("lead:assign")),
    session: AsyncSession = Depends(get_db),
):
    lead = await svc.get_visible_lead(session, user, lead_id)
    await svc.assign_lead(
        session, lead, to_user_id=None, operator_id=user.id, reason="释放回线索池"
    )
    await write_audit(
        session,
        operator_id=user.id,
        action="release",
        business_type="lead",
        business_id=lead.id,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_lead(lead), "已释放回线索池")


@router.post("/leads/{lead_id}/discard")
async def discard_lead(
    lead_id: int,
    payload: LeadDiscard,
    request: Request,
    user: CurrentUser = Depends(require_permission("lead:assign")),
    session: AsyncSession = Depends(get_db),
):
    lead = await svc.get_visible_lead(session, user, lead_id)
    svc.mark_discarded(session, lead, reason=payload.reason, operator_id=user.id)
    await write_audit(
        session,
        operator_id=user.id,
        action="discard",
        business_type="lead",
        business_id=lead.id,
        after={"reason": payload.reason},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(None, "线索已废弃")


@router.post("/leads/{lead_id}/deduplicate")
async def deduplicate_lead(
    lead_id: int,
    user: CurrentUser = Depends(require_permission("lead:view")),
    session: AsyncSession = Depends(get_db),
):
    """查重：给出疑似重复的已有客户，供转化时选择关联。"""
    lead = await svc.get_visible_lead(session, user, lead_id)
    # 按调用者的数据范围限定候选：这个函数原来直接捞全库、返回里还带客户名/等级/
    # 负责人，业务员拿名称前缀或手机号就能把全公司客户枚举出来。
    # 跨范围的疑似重复仍有出口——转化或建档时会走"开待裁定单"那条路，由主管裁定。
    from app.core.data_scope import scoped_owner_ids

    candidates = await find_duplicate_customers(
        session,
        company_name=lead.company_name or lead.name,
        mobile=lead.mobile,
        owner_ids=await scoped_owner_ids(session, user),
    )
    return ok({"candidates": candidates})


@router.post("/leads/{lead_id}/convert")
async def convert_lead(
    lead_id: int,
    payload: LeadConvert,
    request: Request,
    user: CurrentUser = Depends(require_permission("lead:convert")),
    session: AsyncSession = Depends(get_db),
):
    lead = await svc.get_visible_lead(session, user, lead_id)
    if lead.status == "converted":
        # 一次线索转化必须幂等（02-ER §21）
        raise AppError(ErrorCode.DUPLICATE_CONVERT, "该线索已经转化过", 409)

    if payload.customer_mode == "existing":
        if not payload.customer_id:
            raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "请选择要关联的客户")
        customer = await session.get(Customer, payload.customer_id)
        if customer is None or customer.deleted_at is not None:
            raise AppError(ErrorCode.NOT_FOUND, "客户不存在", 404)
        # 只判存在不够：转化会在该客户下**建联系人和商机**（见下面的
        # create_contact_for_customer / create_opportunity_from_lead），
        # 用别人的客户 id 就等于往别人的客户里写数据。与单个客户接口同一口径。
        from app.core.data_scope import ensure_in_scope

        await ensure_in_scope(session, user, owner_id=customer.owner_id, label="客户")
    else:
        customer = Customer(
            name=lead.company_name or lead.name,
            short_name=lead.company_name or lead.name,
            customer_type="企业",
            country=lead.country or "中国",
            region=lead.region,
            source=lead.source or "其他渠道",
            level="C",
            status="active",
            pool_status="private" if lead.owner_id else "public",
            owner_id=lead.owner_id,
            created_by=user.id,
        )
        session.add(customer)
        await session.flush()

    contact_id = None
    if payload.create_contact and (lead.contact_name or lead.mobile):
        contact = await create_contact_for_customer(
            session,
            customer_id=customer.id,
            name=lead.contact_name or lead.name,
            mobile=lead.mobile,
            email=lead.email,
            owner_id=customer.owner_id,
            source=lead.source or "线索转化",
        )
        contact_id = contact.id

    opportunity_id = None
    if payload.create_opportunity:
        from app.modules.opportunity.service import create_opportunity_from_lead

        opportunity = await create_opportunity_from_lead(
            session,
            customer_id=customer.id,
            primary_contact_id=contact_id,
            title=payload.opportunity_title or f"{customer.name} 商机",
            owner_id=customer.owner_id or user.id,
            created_by=user.id,
            expected_amount=payload.expected_amount,
            expected_close_date=payload.expected_close_date,
        )
        opportunity_id = opportunity.id

    lead.status = "converted"
    lead.converted_customer_id = customer.id
    lead.converted_contact_id = contact_id
    lead.converted_opportunity_id = opportunity_id
    await session.flush()

    await write_audit(
        session,
        operator_id=user.id,
        action="convert",
        business_type="lead",
        business_id=lead.id,
        after={
            "customer_id": customer.id,
            "contact_id": contact_id,
            "opportunity_id": opportunity_id,
        },
        ip=client_ip(request),
    )
    await session.commit()
    return ok(
        {
            "lead": svc.serialize_lead(lead),
            "customer_id": customer.id,
            "contact_id": contact_id,
            "opportunity_id": opportunity_id,
        },
        "线索已转化",
    )
