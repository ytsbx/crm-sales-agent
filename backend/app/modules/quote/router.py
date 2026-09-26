"""报价中心接口（对齐 03-API §20 ~ §22）。"""

from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.data_scope import scoped_owner_ids
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok, page_data, paginate
from app.modules.opportunity.model import Opportunity
from app.modules.notification import service as notification_service
from app.modules.quote import service as svc
from app.modules.quote.model import (
    QUOTE_STATUS_LABEL,
    Quote,
    QuoteCharge,
    QuoteItem,
    QuoteSendLog,
    QuoteVersion,
)
from app.modules.quote.pdf import render_quote_pdf
from app.modules.settings import service as settings_service
from app.modules.quote.schema import (
    ApprovalAction,
    DeclinedRequest,
    QuoteChargeInput,
    QuoteCreate,
    QuoteItemInput,
    QuoteItemUpdate,
    QuoteVersionUpdate,
    SendRequest,
    SubmitApprovalRequest,
)
from app.modules.user.model import User

router = APIRouter(tags=["Quote"])

CHARGE_LABEL = {
    "logistics": "物流",
    "packaging": "包装",
    "tax": "税费",
    "discount": "折扣",
    "service": "服务费",
    "other": "其他",
}


async def _quote_context(session: AsyncSession, quotes: list[Quote]) -> dict:
    customer_ids = {q.customer_id for q in quotes}
    owner_ids = {q.owner_id for q in quotes if q.owner_id}
    version_ids = {q.current_version_id for q in quotes if q.current_version_id}
    opp_ids = {q.opportunity_id for q in quotes if q.opportunity_id}

    from app.modules.customer.model import Customer

    customers = {
        int(cid): name
        for cid, name in (
            await session.execute(select(Customer.id, Customer.name).where(Customer.id.in_(customer_ids)))
        ).all()
    } if customer_ids else {}
    owners = {
        int(uid): name
        for uid, name in (
            await session.execute(select(User.id, User.name).where(User.id.in_(owner_ids)))
        ).all()
    } if owner_ids else {}
    versions = {
        v.id: v
        for v in (
            await session.execute(select(QuoteVersion).where(QuoteVersion.id.in_(version_ids)))
        ).scalars().all()
    } if version_ids else {}
    titles = {
        int(oid): title
        for oid, title in (
            await session.execute(select(Opportunity.id, Opportunity.title).where(Opportunity.id.in_(opp_ids)))
        ).all()
    } if opp_ids else {}
    return {"customers": customers, "owners": owners, "versions": versions, "titles": titles}


@router.get("/quotes")
async def list_quotes(
    keyword: str | None = None,
    status: str | None = None,
    opportunity_id: int | None = None,
    customer_id: int | None = None,
    owner_id: int | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user: CurrentUser = Depends(require_permission("quote:view")),
    session: AsyncSession = Depends(get_db),
):
    stmt = select(Quote).where(Quote.deleted_at.is_(None))
    if keyword:
        stmt = stmt.where(Quote.quote_no.ilike(f"%{keyword.strip()}%"))
    if status:
        stmt = stmt.where(Quote.status == status)
    if opportunity_id:
        stmt = stmt.where(Quote.opportunity_id == opportunity_id)
    if customer_id:
        stmt = stmt.where(Quote.customer_id == customer_id)
    if owner_id:
        stmt = stmt.where(Quote.owner_id == owner_id)
    owner_ids = await scoped_owner_ids(session, user)
    if owner_ids is not None:
        stmt = stmt.where(Quote.owner_id.in_(owner_ids))

    rows, total = await paginate(session, stmt.order_by(Quote.id.desc()), page, page_size)
    ctx = await _quote_context(session, rows)
    items = [
        svc.serialize_quote(
            quote,
            version=ctx["versions"].get(quote.current_version_id),
            customer_name=ctx["customers"].get(quote.customer_id),
            owner_name=ctx["owners"].get(quote.owner_id) if quote.owner_id else None,
            opportunity_title=ctx["titles"].get(quote.opportunity_id) if quote.opportunity_id else None,
        )
        for quote in rows
    ]
    return ok(page_data(items, total, page, page_size))


@router.post("/quotes")
async def create_quote(
    payload: QuoteCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("quote:manage")),
    session: AsyncSession = Depends(get_db),
):
    """从商机生成报价：默认按核价建议价生成 V1，明细来自商机需求明细。

    生成逻辑在 `svc.create_quote`，与 Agent 工具 `create_quote_draft` 共用同一份实现。
    """
    opportunity = None
    if payload.opportunity_id:
        opportunity = await session.get(Opportunity, payload.opportunity_id)
        if opportunity is None or opportunity.deleted_at is not None:
            raise AppError(ErrorCode.NOT_FOUND, "商机不存在", 404)

    created = await svc.create_quote(
        session,
        user=user,
        opportunity=opportunity,
        customer_id=payload.customer_id,
        contact_id=payload.contact_id,
        currency=payload.currency,
        exchange_rate=payload.exchange_rate,
        valid_until=payload.valid_until,
        payment_terms=payload.payment_terms,
        delivery_terms=payload.delivery_terms,
        remark=payload.remark,
    )
    quote = created["_quote"]
    version = created["_version"]

    await write_audit(
        session,
        operator_id=user.id,
        action="create",
        business_type="quote",
        business_id=quote.id,
        after=svc.serialize_quote(quote, version=version),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(
        {
            "quote_id": quote.id,
            "version_id": version.id,
            "currency": version.currency,
            "exchange_rate_snapshot": created["exchange_rate_snapshot"],
            "warnings": created["warnings"],
        },
        "报价单已生成",
    )


@router.get("/quotes/{quote_id}")
async def get_quote(
    quote_id: int,
    _: CurrentUser = Depends(require_permission("quote:view")),
    session: AsyncSession = Depends(get_db),
):
    quote = await svc.get_quote_or_404(session, quote_id)
    ctx = await _quote_context(session, [quote])
    return ok(
        svc.serialize_quote(
            quote,
            version=ctx["versions"].get(quote.current_version_id),
            customer_name=ctx["customers"].get(quote.customer_id),
            owner_name=ctx["owners"].get(quote.owner_id) if quote.owner_id else None,
            opportunity_title=ctx["titles"].get(quote.opportunity_id) if quote.opportunity_id else None,
        )
    )


@router.get("/quotes/{quote_id}/versions")
async def list_versions(
    quote_id: int,
    _: CurrentUser = Depends(require_permission("quote:view")),
    session: AsyncSession = Depends(get_db),
):
    await svc.get_quote_or_404(session, quote_id)
    rows = (
        await session.execute(
            select(QuoteVersion)
            .where(QuoteVersion.quote_id == quote_id)
            .order_by(QuoteVersion.version_no.desc())
        )
    ).scalars().all()
    return ok([svc.serialize_version(version) for version in rows])


@router.post("/quotes/{quote_id}/versions")
async def create_version(
    quote_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("quote:manage")),
    session: AsyncSession = Depends(get_db),
):
    """新建版本：复制上一版全部明细与费用，旧版本原样保留、不可覆盖。

    复制逻辑在 `svc.create_version`，与 Agent 工具 `create_quote_version` 共用。
    """
    quote = await svc.get_quote_or_404(session, quote_id)
    version = await svc.create_version(session, quote=quote, user=user)

    await write_audit(
        session,
        operator_id=user.id,
        action="create_version",
        business_type="quote",
        business_id=quote.id,
        after={"version_no": version.version_no},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_version(version), f"已创建 V{version.version_no}")


@router.get("/quote-versions/{version_id}")
async def get_version(
    version_id: int,
    _: CurrentUser = Depends(require_permission("quote:view")),
    session: AsyncSession = Depends(get_db),
):
    version = await svc.get_version_or_404(session, version_id)
    items = await svc.version_items(session, version_id)
    charges = await svc.version_charges(session, version_id)
    instance = await svc.latest_approval(session, version_id)
    records = await svc.approval_records(session, instance.id) if instance else []
    total_profit = sum(
        (item.profit_snapshot * item.quantity for item in items), Decimal(0)
    )
    quote = await session.get(Quote, version.quote_id)
    ctx = await _quote_context(session, [quote]) if quote else None
    return ok(
        {
            "version": svc.serialize_version(version, total_profit),
            "quote": (
                svc.serialize_quote(
                    quote,
                    version=version,
                    customer_name=ctx["customers"].get(quote.customer_id),
                    owner_name=ctx["owners"].get(quote.owner_id) if quote.owner_id else None,
                    opportunity_title=(
                        ctx["titles"].get(quote.opportunity_id) if quote.opportunity_id else None
                    ),
                )
                if quote
                else None
            ),
            "items": [svc.serialize_item(item) for item in items],
            "charges": [
                {**svc.serialize_charge(charge), "type_label": CHARGE_LABEL.get(charge.charge_type)}
                for charge in charges
            ],
            "approval": (
                {
                    "id": instance.id,
                    "status": instance.status,
                    "current_node": instance.current_node,
                    "applicant_id": instance.applicant_id,
                    "summary": instance.summary,
                    "created_at": instance.created_at,
                    "finished_at": instance.finished_at,
                    "records": records,
                }
                if instance
                else None
            ),
        }
    )


@router.patch("/quote-versions/{version_id}")
async def update_version(
    version_id: int,
    payload: QuoteVersionUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("quote:manage")),
    session: AsyncSession = Depends(get_db),
):
    version = await svc.get_version_or_404(session, version_id)
    await svc.ensure_version_editable(version)
    data = payload.model_dump(exclude_unset=True)
    valid_until = data.pop("valid_until", None)
    for field, value in data.items():
        setattr(version, field, value)
    if valid_until is not None:
        quote = await svc.get_quote_or_404(session, version.quote_id)
        quote.valid_until = valid_until
    await write_audit(
        session,
        operator_id=user.id,
        action="update_version",
        business_type="quote",
        business_id=version.quote_id,
        after=data,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_version(version), "已保存")


@router.post("/quote-versions/{version_id}/items/batch")
async def set_items(
    version_id: int,
    items: list[QuoteItemInput],
    request: Request,
    user: CurrentUser = Depends(require_permission("quote:manage")),
    session: AsyncSession = Depends(get_db),
):
    """整版替换明细：报价明细按 SKU 逐条计算并落快照。"""
    version = await svc.get_version_or_404(session, version_id)
    await svc.ensure_version_editable(version)
    quote = await svc.get_quote_or_404(session, version.quote_id)

    existing = await svc.version_items(session, version_id)
    for item in existing:
        await session.delete(item)
    await session.flush()

    for payload in items:
        item = await svc.build_item_snapshot(
            session,
            version=version,
            sku_id=payload.sku_id,
            quantity=payload.quantity,
            customer_id=quote.customer_id,
            quoted_price=payload.quoted_price,
            logistics_cost=payload.logistics_cost,
            opportunity_item_id=payload.opportunity_item_id,
            spec_snapshot=payload.spec_snapshot,
            remark=payload.remark,
            role_codes=user.roles,
        )
        session.add(item)
    await session.flush()
    await svc.recalc_version(session, version)
    await write_audit(
        session,
        operator_id=user.id,
        action="set_items",
        business_type="quote",
        business_id=quote.id,
        after={"count": len(items)},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_version(version), "报价明细已保存")


@router.patch("/quote-items/{item_id}")
async def update_item(
    item_id: int,
    payload: QuoteItemUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("quote:manage")),
    session: AsyncSession = Depends(get_db),
):
    item = await session.get(QuoteItem, item_id)
    if item is None:
        raise AppError(ErrorCode.NOT_FOUND, "报价明细不存在", 404)
    version = await svc.get_version_or_404(session, item.quote_version_id)
    await svc.ensure_version_editable(version)
    quote = await svc.get_quote_or_404(session, version.quote_id)

    data = payload.model_dump(exclude_unset=True)
    quantity = data.get("quantity", item.quantity)
    price = data.get("quoted_price", item.quoted_price)
    logistics = data.get("logistics_cost")
    if logistics is not None:
        item.logistics_cost_snapshot = logistics

    rebuilt = await svc.build_item_snapshot(
        session,
        version=version,
        sku_id=item.sku_id,
        quantity=quantity,
        customer_id=quote.customer_id,
        quoted_price=price,
        logistics_cost=item.logistics_cost_snapshot,
        opportunity_item_id=item.opportunity_item_id,
        spec_snapshot=item.spec_snapshot,
        remark=data.get("remark", item.remark),
        role_codes=user.roles,
    )
    for field in (
        "quantity",
        "quoted_price",
        "cost_snapshot",
        "logistics_cost_snapshot",
        "standard_price_snapshot",
        "recommended_price_snapshot",
        "minimum_price_snapshot",
        "profit_snapshot",
        "profit_rate_snapshot",
        "approval_required",
        "approval_reason",
        "remark",
    ):
        setattr(item, field, getattr(rebuilt, field))
    await session.flush()
    await svc.recalc_version(session, version)
    await write_audit(
        session,
        operator_id=user.id,
        action="update_item",
        business_type="quote",
        business_id=quote.id,
        after={"item_id": item.id, "quoted_price": float(item.quoted_price)},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_item(item), "已保存")


@router.delete("/quote-items/{item_id}")
async def delete_item(
    item_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("quote:manage")),
    session: AsyncSession = Depends(get_db),
):
    item = await session.get(QuoteItem, item_id)
    if item is None:
        raise AppError(ErrorCode.NOT_FOUND, "报价明细不存在", 404)
    version = await svc.get_version_or_404(session, item.quote_version_id)
    await svc.ensure_version_editable(version)
    before = svc.serialize_item(item)
    await session.delete(item)
    await session.flush()
    await svc.recalc_version(session, version)
    await write_audit(
        session,
        operator_id=user.id,
        action="delete",
        business_type="quote_item",
        business_id=item_id,
        before=before,
        after={"quote_version_id": version.id, "total_amount": float(version.total_amount)},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(None, "已删除")


@router.post("/quote-versions/{version_id}/charges")
async def add_charge(
    version_id: int,
    payload: QuoteChargeInput,
    request: Request,
    user: CurrentUser = Depends(require_permission("quote:manage")),
    session: AsyncSession = Depends(get_db),
):
    version = await svc.get_version_or_404(session, version_id)
    await svc.ensure_version_editable(version)
    charge = QuoteCharge(
        quote_version_id=version_id,
        charge_type=payload.charge_type,
        description=payload.description,
        amount=payload.amount,
        is_discount=payload.is_discount,
    )
    session.add(charge)
    await session.flush()
    await svc.recalc_version(session, version)
    await write_audit(
        session,
        operator_id=user.id,
        action="create",
        business_type="quote_charge",
        business_id=charge.id,
        after={
            **svc.serialize_charge(charge),
            "total_amount": float(version.total_amount),
        },
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_charge(charge), "附加费用已添加")


@router.delete("/quote-charges/{charge_id}")
async def delete_charge(
    charge_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("quote:manage")),
    session: AsyncSession = Depends(get_db),
):
    charge = await session.get(QuoteCharge, charge_id)
    if charge is None:
        raise AppError(ErrorCode.NOT_FOUND, "附加费用不存在", 404)
    version = await svc.get_version_or_404(session, charge.quote_version_id)
    await svc.ensure_version_editable(version)
    before = svc.serialize_charge(charge)
    await session.delete(charge)
    await session.flush()
    await svc.recalc_version(session, version)
    await write_audit(
        session,
        operator_id=user.id,
        action="delete",
        business_type="quote_charge",
        business_id=charge_id,
        before=before,
        after={"quote_version_id": version.id, "total_amount": float(version.total_amount)},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(None, "已删除")


@router.post("/quote-versions/{version_id}/submit-approval")
async def submit_approval(
    version_id: int,
    payload: SubmitApprovalRequest,
    request: Request,
    user: CurrentUser = Depends(require_permission("quote:manage")),
    session: AsyncSession = Depends(get_db),
):
    version = await svc.get_version_or_404(session, version_id)
    quote = await svc.get_quote_or_404(session, version.quote_id)
    if version.sent_at is not None:
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "该版本已经发送，不能再次提交审批")
    instance, required = await svc.submit_for_approval(
        session,
        quote=quote,
        version=version,
        applicant_id=user.id,
        user_roles=user.roles,
        reason=payload.reason,
    )
    if required:
        await notification_service.notify_approvers(
            session,
            permission_code="quote:approve",
            title=f"待审批报价 {quote.quote_no}",
            content=f"V{version.version_no} 报价 ¥{float(version.total_amount):,.2f}，{payload.reason or '超出业务员价格权限'}",
            business_type="quote",
            business_id=quote.id,
            exclude_user_id=user.id,
        )
    await write_audit(
        session,
        operator_id=user.id,
        action="submit_approval",
        business_type="quote",
        business_id=quote.id,
        after={"approval_required": required},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(
        {
            "approval_required": required,
            "approval_id": instance.id if instance else None,
            "version": svc.serialize_version(version),
        },
        "已提交审批" if required else "未超出权限，报价已通过",
    )


@router.post("/quote-versions/{version_id}/withdraw-approval")
async def withdraw_approval(
    version_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("quote:manage")),
    session: AsyncSession = Depends(get_db),
):
    version = await svc.get_version_or_404(session, version_id)
    if version.approval_status != "pending":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "当前没有待审批的申请")
    instance = await svc.latest_approval(session, version_id)
    withdrawn_instance_id = None
    if instance:
        instance.status = "withdrawn"
        instance.finished_at = datetime.now(UTC)
        withdrawn_instance_id = instance.id
        from app.modules.approval.model import ApprovalRecord

        session.add(
            ApprovalRecord(
                approval_instance_id=instance.id,
                node_code="withdraw",
                approver_id=user.id,
                action="withdraw",
            )
        )
    version.approval_status = "not_submitted"
    version.approval_required = False
    version.submitted_at = None
    quote = await svc.get_quote_or_404(session, version.quote_id)
    quote.status = "draft"
    await session.flush()
    # 撤回是审批流里的关键动作，必须留痕：否则"谁在什么时候把审批撤了"查不到
    await write_audit(
        session,
        operator_id=user.id,
        action="withdraw_approval",
        business_type="quote_version",
        business_id=version.id,
        before={"approval_status": "pending", "approval_instance_id": withdrawn_instance_id},
        after={"approval_status": "not_submitted"},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(None, "已撤回审批")


@router.post("/quote-versions/{version_id}/mark-sent")
async def mark_sent(
    version_id: int,
    payload: SendRequest,
    request: Request,
    user: CurrentUser = Depends(require_permission("quote:manage")),
    session: AsyncSession = Depends(get_db),
):
    version = await svc.get_version_or_404(session, version_id)
    if version.approval_status not in ("approved",):
        raise AppError(ErrorCode.APPROVAL_PENDING, "报价未通过审批，不能发送", 422)
    quote = await svc.get_quote_or_404(session, version.quote_id)
    version.sent_at = datetime.now(UTC)
    quote.status = "sent"
    session.add(
        QuoteSendLog(
            quote_version_id=version.id,
            channel=payload.channel,
            receiver=payload.receiver,
            sent_by=user.id,
            status="success",
            sent_at=datetime.now(UTC),
        )
    )
    await write_audit(
        session,
        operator_id=user.id,
        action="send",
        business_type="quote",
        business_id=quote.id,
        after={"channel": payload.channel, "receiver": payload.receiver},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_version(version), "已标记为已发送")


@router.get("/quote-versions/{version_id}/send-logs")
async def send_logs(
    version_id: int,
    _: CurrentUser = Depends(require_permission("quote:view")),
    session: AsyncSession = Depends(get_db),
):
    rows = (
        await session.execute(
            select(QuoteSendLog)
            .where(QuoteSendLog.quote_version_id == version_id)
            .order_by(QuoteSendLog.id.desc())
        )
    ).scalars().all()
    return ok(
        [
            {
                "id": row.id,
                "channel": row.channel,
                "receiver": row.receiver,
                "status": row.status,
                "sent_at": row.sent_at,
            }
            for row in rows
        ]
    )


@router.post("/quote-versions/{version_id}/accept")
async def accept_quote(
    version_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("quote:manage")),
    session: AsyncSession = Depends(get_db),
):
    version = await svc.get_version_or_404(session, version_id)
    quote = await svc.get_quote_or_404(session, version.quote_id)
    if quote.status not in ("sent", "approved"):
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "只有已发送的报价才能标记客户接受")
    version.accepted_at = datetime.now(UTC)
    quote.status = "accepted"
    await write_audit(
        session,
        operator_id=user.id,
        action="accept",
        business_type="quote",
        business_id=quote.id,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_version(version), "客户已接受，可以转订单了")


@router.post("/quote-versions/{version_id}/reject")
async def reject_quote(
    version_id: int,
    payload: DeclinedRequest,
    request: Request,
    user: CurrentUser = Depends(require_permission("quote:manage")),
    session: AsyncSession = Depends(get_db),
):
    version = await svc.get_version_or_404(session, version_id)
    quote = await svc.get_quote_or_404(session, version.quote_id)
    version.declined_at = datetime.now(UTC)
    quote.status = "declined"
    await write_audit(
        session,
        operator_id=user.id,
        action="decline",
        business_type="quote",
        business_id=quote.id,
        after={"reason": payload.reason},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_version(version), "已记录客户拒绝")


@router.get("/quote-versions/{version_id}/pdf")
async def download_pdf(
    version_id: int,
    user: CurrentUser = Depends(require_permission("quote:view")),
    session: AsyncSession = Depends(get_db),
):
    """生成并下载报价单 PDF。数据全部取快照，不回查当前价格。"""
    version = await svc.get_version_or_404(session, version_id)
    quote = await svc.get_quote_or_404(session, version.quote_id)
    ctx = await _quote_context(session, [quote])
    items = await svc.version_items(session, version_id)
    charges = await svc.version_charges(session, version_id)

    data = {
        "company_name": await settings_service.get_text(session, "company_name", "text", ""),
        "quote_no": quote.quote_no,
        "version_no": version.version_no,
        "customer_name": ctx["customers"].get(quote.customer_id),
        "opportunity_title": ctx["titles"].get(quote.opportunity_id) if quote.opportunity_id else None,
        "owner_name": ctx["owners"].get(quote.owner_id) if quote.owner_id else user.name,
        "quote_date": version.created_at.strftime("%Y-%m-%d"),
        "valid_until": quote.valid_until,
        "status_label": QUOTE_STATUS_LABEL.get(quote.status, quote.status),
        "items": [svc.serialize_item(item) for item in items],
        "charges": [
            {**svc.serialize_charge(charge), "type_label": CHARGE_LABEL.get(charge.charge_type)}
            for charge in charges
        ],
        "subtotal_amount": float(version.subtotal_amount),
        "charge_amount": float(version.charge_amount),
        "discount_amount": float(version.discount_amount),
        "total_amount": float(version.total_amount),
        "payment_terms": version.payment_terms,
        "delivery_terms": version.delivery_terms,
        "remark": version.remark,
    }
    pdf_bytes = render_quote_pdf(data)
    filename = f"{quote.quote_no}-V{version.version_no}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


__all__ = ["date", "QuoteItem"]
