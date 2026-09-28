"""报价中心接口（对齐 03-API §20 ~ §22）。"""

import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.data_scope import scoped_owner_ids
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok, page_data, paginate
from app.modules.customer import service as customer_service
from app.modules.customer.model import Customer
from app.modules.opportunity.model import Opportunity
from app.modules.order.model import SalesOrder
from app.modules.followup import service as followup_service
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
    DeclinedRequest,
    QuoteChargeInput,
    QuoteChargeUpdate,
    QuoteClone,
    QuoteCreate,
    QuoteExpire,
    QuoteItemInput,
    QuoteItemUpdate,
    QuoteUpdate,
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
        # 单号或客户名都能搜（规则沙盒按客户挑单就是走这里）
        from app.modules.customer.model import Customer

        stmt = stmt.where(
            or_(
                Quote.quote_no.ilike(f"%{keyword.strip()}%"),
                Quote.customer_id.in_(
                    select(Customer.id).where(Customer.name.ilike(f"%{keyword.strip()}%"))
                ),
            )
        )
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

    # 客户/联系人存在性由 svc.create_quote 统一校验（Agent 工具同路）。
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
    # 业务进展时钟（§2.3）：建报价算客户活跃，冷落/回收不该盯着手工跟进单看
    await customer_service.touch_progress(session, quote.customer_id)

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
    user: CurrentUser = Depends(require_permission("quote:view")),
    session: AsyncSession = Depends(get_db),
):
    quote = await svc.get_visible_quote(session, user, quote_id)
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
    user: CurrentUser = Depends(require_permission("quote:view")),
    session: AsyncSession = Depends(get_db),
):
    await svc.get_visible_quote(session, user, quote_id)
    rows = (
        await session.execute(
            select(QuoteVersion)
            .where(QuoteVersion.quote_id == quote_id)
            .order_by(QuoteVersion.version_no.desc())
        )
    ).scalars().all()
    return ok([svc.serialize_version(version) for version in rows])


# --------------------------------------------- 03-API §20 补齐的报价单级接口


@router.patch("/quotes/{quote_id}")
async def update_quote(
    quote_id: int,
    payload: QuoteUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("quote:manage")),
    session: AsyncSession = Depends(get_db),
):
    """改报价单本身。

    只允许改"单据级"字段（联系人/负责人/有效期/备注）。
    金额、明细、费用属于**版本**，必须走版本接口 ——
    否则会出现"单据金额变了但版本快照没变"，历史报价再也对不上。
    """
    quote = await svc.get_visible_quote(session, user, quote_id)
    before = svc.serialize_quote(quote)
    data = payload.model_dump(exclude_unset=True)
    if data.get("owner_id") is not None:
        owner = await session.get(User, data["owner_id"])
        if owner is None:
            raise AppError(ErrorCode.NOT_FOUND, f"负责人 id={data['owner_id']} 不存在", 404)
        if owner.status != "active":
            raise AppError(ErrorCode.PARAM_ERROR, f"负责人「{owner.name}」已停用", 422)
    for field, value in data.items():
        setattr(quote, field, value)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="update",
        business_type="quote",
        business_id=quote.id,
        before=before,
        after=svc.serialize_quote(quote),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_quote(quote), "已保存")


@router.delete("/quotes/{quote_id}")
async def delete_quote(
    quote_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("quote:manage")),
    session: AsyncSession = Depends(get_db),
):
    """删除报价单（软删）。

    已转订单的报价不能删：订单与报价是追溯关系，删了报价会让订单
    失去来源。其余情况软删，历史版本与审计都保留。
    """
    quote = await svc.get_visible_quote(session, user, quote_id)
    order_id = (
        await session.execute(
            select(SalesOrder.id).where(SalesOrder.quote_id == quote.id)
        )
    ).scalars().first()
    if order_id is not None:
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"该报价已转成订单（id={order_id}），不能删除",
        )
    before = svc.serialize_quote(quote)
    quote.deleted_at = datetime.now(UTC)
    await write_audit(
        session,
        operator_id=user.id,
        action="delete",
        business_type="quote",
        business_id=quote.id,
        before=before,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(None, "报价单已删除")


@router.get("/quotes/{quote_id}/followups")
async def quote_followups(
    quote_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user: CurrentUser = Depends(require_permission("quote:view")),
    session: AsyncSession = Depends(get_db),
):
    """该报价单相关的跟进记录（03-API §20）。

    "报了价之后客户有没有回音" —— 跟进记录里 `quote_id` 指向这一单的那些。
    """
    from app.modules.followup.model import FollowUp

    await svc.get_visible_quote(session, user, quote_id)
    stmt = (
        select(FollowUp)
        .where(FollowUp.quote_id == quote_id)
        .order_by(FollowUp.id.desc())
    )
    rows, total = await paginate(session, stmt, page, page_size)
    return ok(
        page_data(
            [
                {
                    "id": row.id,
                    "customer_id": row.customer_id,
                    "contact_id": row.contact_id,
                    "followup_type": row.followup_type,
                    "content": row.content,
                    "customer_feedback": row.customer_feedback,
                    "next_action": row.next_action,
                    "owner_id": row.owner_id,
                    "created_at": row.created_at,
                }
                for row in rows
            ],
            total,
            page,
            page_size,
        )
    )


@router.get("/quotes/{quote_id}/send-logs")
async def quote_send_logs(
    quote_id: int,
    user: CurrentUser = Depends(require_permission("quote:view")),
    session: AsyncSession = Depends(get_db),
):
    """整张报价单的发送记录（03-API §20）。

    与 `/quote-versions/{id}/send-logs` 的区别：那个只看一版，
    这个跨所有版本 —— "这张报价到底发过几次、发给谁"要看这个。
    """
    await svc.get_visible_quote(session, user, quote_id)
    rows = (
        await session.execute(
            select(QuoteSendLog, QuoteVersion.version_no)
            .join(QuoteVersion, QuoteVersion.id == QuoteSendLog.quote_version_id)
            .where(QuoteVersion.quote_id == quote_id)
            .order_by(QuoteSendLog.id.desc())
        )
    ).all()
    return ok(
        [
            {
                "id": log.id,
                "version_id": log.quote_version_id,
                "version_no": version_no,
                "channel": log.channel,
                "receiver": log.receiver,
                "status": log.status,
                "sent_at": log.sent_at,
                "error_message": log.error_message,
            }
            for log, version_no in rows
        ]
    )


@router.get("/quotes/{quote_id}/approval-history")
async def quote_approval_history(
    quote_id: int,
    user: CurrentUser = Depends(require_permission("quote:view")),
    session: AsyncSession = Depends(get_db),
):
    """整张报价单的审批历史（03-API §20）。

    按版本聚合，每版给审批实例 + 处理记录 —— "这单被谁卡过、为什么"
    一次看全，不用逐版点进去。
    """
    await svc.get_visible_quote(session, user, quote_id)
    versions = (
        await session.execute(
            select(QuoteVersion)
            .where(QuoteVersion.quote_id == quote_id)
            .order_by(QuoteVersion.version_no.asc())
        )
    ).scalars().all()

    history = []
    for version in versions:
        instance = await svc.latest_approval(session, version.id)
        if instance is None:
            continue
        records = await svc.approval_records(session, instance.id)
        history.append(
            {
                "version_id": version.id,
                "version_no": version.version_no,
                "approval_status": version.approval_status,
                "submitted_at": version.submitted_at,
                "approved_at": version.approved_at,
                "instance": {
                    "id": instance.id,
                    "status": instance.status,
                    "current_node": instance.current_node,
                    "summary": instance.summary,
                    "created_at": instance.created_at,
                    "finished_at": instance.finished_at,
                    "records": records,
                },
            }
        )
    return ok(history)


@router.post("/quotes/{quote_id}/clone")
async def clone_quote(
    quote_id: int,
    payload: QuoteClone,
    request: Request,
    user: CurrentUser = Depends(require_permission("quote:manage")),
    session: AsyncSession = Depends(get_db),
):
    """复制报价单（03-API §20）。

    复制的是**报价内容**，不是它的审批结论：新报价是草稿、
    版本状态重置为未提交、不带发送记录。审批通过/已发送是上一单的事实。
    """
    source = await svc.get_visible_quote(session, user, quote_id)
    if payload.customer_id is not None:
        if await session.get(Customer, payload.customer_id) is None:
            raise AppError(ErrorCode.NOT_FOUND, f"客户 id={payload.customer_id} 不存在", 404)

    # 币种沿用源报价当前版本的快照；汇率不复制（汇率是时点数据，
    # 复制一个旧汇率会让新报价按过期汇率算，比让它重新解析更危险）。
    source_version = (
        await session.get(QuoteVersion, source.current_version_id)
        if source.current_version_id
        else None
    )
    source_currency = source_version.currency if source_version else "CNY"

    # D8：复制出的也是**新报价**，同样必须归属商机。先取"显式指定 > 源报价"的
    # 最终归属，源报价就没挂商机且未指定时按同一口径拒绝（历史数据不追溯，
    # 但复制产生的是新数据）。create_quote 本身先放行（enforce_opportunity=False），
    # 因为它建壳在先、归属在后。
    final_opportunity_id = (
        payload.opportunity_id if payload.opportunity_id is not None else source.opportunity_id
    )
    if final_opportunity_id is None:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            "复制出的报价必须关联商机——源报价没有商机，请在复制时指定 opportunity_id"
            "（或在查价页「选品下单」一键新建快捷商机）",
            422,
        )
    created = await svc.create_quote(
        session,
        user=user,
        opportunity=None,
        enforce_opportunity=False,
        customer_id=payload.customer_id or source.customer_id,
        contact_id=payload.contact_id if payload.contact_id is not None else source.contact_id,
        currency=source_currency,
        exchange_rate=None,
        valid_until=payload.valid_until or source.valid_until,
        payment_terms=source_version.payment_terms if source_version else None,
        delivery_terms=source_version.delivery_terms if source_version else None,
        remark=payload.remark or f"由报价 {source.quote_no} 复制",
    )
    new_quote = created["_quote"]
    new_quote.opportunity_id = (
        payload.opportunity_id if payload.opportunity_id is not None else source.opportunity_id
    )
    new_quote.owner_id = payload.owner_id if payload.owner_id is not None else source.owner_id

    copied_items = 0
    if payload.copy_items and source.current_version_id:
        source_items = await svc.version_items(session, source.current_version_id)
        new_version = created["_version"]
        for item in source_items:
            session.add(
                QuoteItem(
                    quote_version_id=new_version.id,
                    opportunity_item_id=item.opportunity_item_id,
                    sku_id=item.sku_id,
                    sku_code_snapshot=item.sku_code_snapshot,
                    sku_name_snapshot=item.sku_name_snapshot,
                    spec_snapshot=item.spec_snapshot,
                    quantity=item.quantity,
                    cost_snapshot=item.cost_snapshot,
                    package_cost_snapshot=item.package_cost_snapshot,
                    logistics_cost_snapshot=item.logistics_cost_snapshot,
                    standard_price_snapshot=item.standard_price_snapshot,
                    recommended_price_snapshot=item.recommended_price_snapshot,
                    minimum_price_snapshot=item.minimum_price_snapshot,
                    quoted_price=item.quoted_price,
                    profit_snapshot=item.profit_snapshot,
                    profit_rate_snapshot=item.profit_rate_snapshot,
                    approval_required=item.approval_required,
                    approval_reason=item.approval_reason,
                    remark=item.remark,
                )
            )
            copied_items += 1
        await session.flush()
        await svc.recalc_version(session, new_version)

    await write_audit(
        session,
        operator_id=user.id,
        action="clone",
        business_type="quote",
        business_id=new_quote.id,
        before={"source_quote_id": source.id},
        after={"copied_items": copied_items},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(
        {
            "quote_id": new_quote.id,
            "quote_no": new_quote.quote_no,
            "version_id": created["_version"].id,
            "copied_items": copied_items,
            "source_quote_id": source.id,
        },
        f"已从 {source.quote_no} 复制出 {new_quote.quote_no}",
    )


# ---------------------------------------------------------------- 价格刷新（A09 后半）

@router.get("/quote-versions/{version_id}/price-drift")
async def price_drift(
    version_id: int,
    user: CurrentUser = Depends(require_permission("quote:view")),
    session: AsyncSession = Depends(get_db),
):
    """草稿版本"价格已有更新"检测（方案 §5/A09）。

    只读：逐明细按当前条件重查适用价，与快照拟报价比对。
    """
    version = await svc.get_visible_version(session, user, version_id)
    return ok(await svc.price_drift(session, version=version))


@router.post("/quote-versions/{version_id}/price-refresh")
async def price_refresh(
    version_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("quote:manage")),
    session: AsyncSession = Depends(get_db),
):
    """把系统带价的明细刷新到当前适用价（仅草稿可刷；手工价明细不覆盖）。"""
    version = await svc.get_visible_version(session, user, version_id)
    await svc.ensure_version_editable(version)
    quote = await svc.get_visible_quote(session, user, version.quote_id)

    result = await svc.refresh_prices(session, version=version, user=user)
    await write_audit(
        session,
        operator_id=user.id,
        action="price_refresh",
        business_type="quote",
        business_id=quote.id,
        after=result,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(result, f"已刷新 {result['refreshed']} 条明细（手工价 {result['skipped']} 条未动）")


@router.get("/quotes/{quote_id}/version-comparison")
async def compare_versions(
    quote_id: int,
    user: CurrentUser = Depends(require_permission("quote:view")),
    session: AsyncSession = Depends(get_db),
):
    """报价多方案对比（What-if）：逐版本汇总 + 与上一版的差异明细。"""
    await svc.get_visible_quote(session, user, quote_id)
    return ok(await svc.version_comparison(session, quote_id))


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
    quote = await svc.get_visible_quote(session, user, quote_id)
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
    user: CurrentUser = Depends(require_permission("quote:view")),
    session: AsyncSession = Depends(get_db),
):
    version = await svc.get_visible_version(session, user, version_id)
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
    version = await svc.get_visible_version(session, user, version_id)
    await svc.ensure_version_editable(version)
    data = payload.model_dump(exclude_unset=True)
    valid_until = data.pop("valid_until", None)
    for field, value in data.items():
        setattr(version, field, value)
    if valid_until is not None:
        quote = await svc.get_visible_quote(session, user, version.quote_id)
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
    version = await svc.get_visible_version(session, user, version_id)
    await svc.ensure_version_editable(version)
    quote = await svc.get_visible_quote(session, user, version.quote_id)

    existing = await svc.version_items(session, version_id)
    for item in existing:
        await session.delete(item)
    await session.flush()

    moq_warnings: list[str] = []
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
        hint = await svc.moq_warning(session, payload.sku_id, payload.quantity)
        if hint:
            moq_warnings.append(hint)
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
    message = "报价明细已保存"
    if moq_warnings:
        message += "；注意：" + "；".join(moq_warnings)
    return ok(svc.serialize_version(version), message)


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
    version = await svc.get_visible_version(session, user, item.quote_version_id)
    await svc.ensure_version_editable(version)
    quote = await svc.get_visible_quote(session, user, version.quote_id)

    data = payload.model_dump(exclude_unset=True)
    quantity = data.get("quantity", item.quantity)
    price = data.get("quoted_price", item.quoted_price)
    old_price = float(item.quoted_price)
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
        before={"item_id": item.id, "quoted_price": old_price},
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
    version = await svc.get_visible_version(session, user, item.quote_version_id)
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
    version = await svc.get_visible_version(session, user, version_id)
    await svc.ensure_version_editable(version)
    charge = QuoteCharge(
        quote_version_id=version_id,
        charge_type=payload.charge_type,
        description=payload.description,
        amount=payload.amount,
        is_discount=payload.is_discount,
    )
    # 折扣在库里的形态恒为负数（recalc_version 直接代数相加）。用户填正数时
    # 自动取负——不强制的话，"折扣 500"会静默把总额加 500。
    if charge.is_discount and charge.amount > 0:
        charge.amount = -charge.amount
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
    version = await svc.get_visible_version(session, user, charge.quote_version_id)
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
    version = await svc.get_visible_version(session, user, version_id)
    quote = await svc.get_visible_quote(session, user, version.quote_id)
    if version.sent_at is not None:
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "该版本已经发送，不能再次提交审批")
    instance, required = await svc.submit_for_approval(
        session,
        quote=quote,
        version=version,
        applicant_id=user.id,
        user_roles=user.roles,
        reason=payload.reason,
        # 硬拒文案的底价数字只给价格管理员（底价=成本推算，销售可见即泄成本）
        can_see_floor=user.has("price:manage"),
    )
    if required and instance is not None and instance.status == "pending":
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
        after={"approval_required": required, "approval_status": version.approval_status},
        ip=client_ip(request),
    )
    # 领导六阶段口径"过程记录"：报价更新自动写跟进并推送业务主管
    await followup_service.record_and_notify(
        session,
        customer_id=quote.customer_id,
        owner_id=quote.owner_id,
        title=f"报价已提交审批 {quote.quote_no}",
        content=(
            f"{quote.quote_no} V{version.version_no} 提交审批，"
            f"金额 ¥{float(version.total_amount or 0):,.2f}"
        ),
        business_type="quote",
        business_id=quote.id,
        quote_id=quote.id,
        exclude_user_id=user.id,
        # 撤销后重提是新的真实事件（新审批实例新 key）；重放由状态机挡在前面
        event_key=f"quote:submit:{version.id}:{instance.id if instance else 'auto'}",
    )
    await session.commit()
    await notification_service.dispatch_pending(session)
    if required and instance is not None and (instance.summary or {}).get("auto_passed"):
        rule_name = (instance.summary or {}).get("rule_trace", {}).get("rule_name")
        message = f"命中免审规则「{rule_name}」，报价已自动通过"
    elif required:
        message = "已提交审批"
    else:
        message = "未超出权限，报价已通过"
    return ok(
        {
            "approval_required": required,
            "approval_id": instance.id if instance else None,
            "auto_passed": bool(instance and (instance.summary or {}).get("auto_passed")),
            "version": svc.serialize_version(version),
        },
        message,
    )


@router.post("/quote-versions/{version_id}/withdraw-approval")
async def withdraw_approval(
    version_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("quote:manage")),
    session: AsyncSession = Depends(get_db),
):
    version = await svc.get_visible_version(session, user, version_id)
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
    quote = await svc.get_visible_quote(session, user, version.quote_id)
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
    version = await svc.get_visible_version(session, user, version_id)
    if version.approval_status not in ("approved",):
        raise AppError(ErrorCode.APPROVAL_PENDING, "报价未通过审批，不能发送", 422)
    quote = await svc.get_visible_quote(session, user, version.quote_id)
    version.sent_at = datetime.now(UTC)
    quote.status = "sent"
    await customer_service.touch_progress(session, quote.customer_id)
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
    user: CurrentUser = Depends(require_permission("quote:view")),
    session: AsyncSession = Depends(get_db),
):
    # 先校验这一版报价在自己的数据范围内 —— 发送记录里有收件人，
    # 不校验的话能直接看到别人把报价发给了谁（实测确认过）。
    await svc.get_visible_version(session, user, version_id)
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
    version = await svc.get_visible_version(session, user, version_id)
    quote = await svc.get_visible_quote(session, user, version.quote_id)
    if quote.status not in ("sent", "approved"):
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "只有已发送的报价才能标记客户接受")
    version.accepted_at = datetime.now(UTC)
    quote.status = "accepted"
    await customer_service.touch_progress(session, quote.customer_id)
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
    version = await svc.get_visible_version(session, user, version_id)
    quote = await svc.get_visible_quote(session, user, version.quote_id)
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
    version = await svc.get_visible_version(session, user, version_id)
    quote = await svc.get_visible_quote(session, user, version.quote_id)
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
    # reportlab 渲染是同步 CPU 密集操作，直接在事件循环里跑会把
    # 所有并发请求卡住几百毫秒——必须丢线程池
    pdf_bytes = await asyncio.to_thread(render_quote_pdf, data)
    filename = f"{quote.quote_no}-V{version.version_no}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


__all__ = ["date", "QuoteItem"]


# --------------------------- 03-API §21 §22 补齐：版本与明细的读取/复制/失效
#
# 文档里的路径与既有实现有出入：
#   `POST /quote-versions/{id}/generate-pdf` 实现为 `GET /quote-versions/{id}/pdf`（保留 GET，
#     PDF 下载用 GET 更自然，且前端已在用）
#   `POST /quote-versions/{id}/copy` 等价于 `POST /quotes/{id}/versions`（复制上一版）
# 这些只加别名不重复实现；下面补的是**功能确实缺**的部分：
#   版本明细/费用列表、单条费用修改、重算、标记失效、发邮件、版本时间线。


@router.get("/quote-versions/{version_id}/items")
async def list_version_items(
    version_id: int,
    user: CurrentUser = Depends(require_permission("quote:view")),
    session: AsyncSession = Depends(get_db),
):
    """某一版的明细列表（03-API §22）。

    详情接口已经带 items，但这个单独入口是给"只刷新明细表格"用的，
    避免为了拿几行明细把整版（含审批记录）都重新查一遍。
    """
    await svc.get_visible_version(session, user, version_id)
    items = await svc.version_items(session, version_id)
    return ok([svc.serialize_item(item) for item in items])


@router.post("/quote-versions/{version_id}/items")
async def add_version_item(
    version_id: int,
    payload: QuoteItemInput,
    request: Request,
    user: CurrentUser = Depends(require_permission("quote:manage")),
    session: AsyncSession = Depends(get_db),
):
    """追加一条明细（03-API §22）。

    与 `/items/batch` 的区别：batch 是整版替换（界面保存整版用），
    这个是单条追加 —— 逐条录需求时用得上。
    """
    version = await svc.get_visible_version(session, user, version_id)
    await svc.ensure_version_editable(version)
    quote = await svc.get_visible_quote(session, user, version.quote_id)
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
        action="add_item",
        business_type="quote",
        business_id=quote.id,
        after=svc.serialize_item(item),
        ip=client_ip(request),
    )
    await session.commit()
    moq_hint = await svc.moq_warning(session, payload.sku_id, payload.quantity)
    return ok(svc.serialize_item(item), "明细已添加" + (f"；注意：{moq_hint}" if moq_hint else ""))


@router.get("/quote-versions/{version_id}/charges")
async def list_version_charges(
    version_id: int,
    user: CurrentUser = Depends(require_permission("quote:view")),
    session: AsyncSession = Depends(get_db),
):
    """某一版的附加费用列表（03-API §22）。"""
    await svc.get_visible_version(session, user, version_id)
    charges = await svc.version_charges(session, version_id)
    return ok(
        [
            {**svc.serialize_charge(charge), "type_label": CHARGE_LABEL.get(charge.charge_type)}
            for charge in charges
        ]
    )


@router.patch("/quote-charges/{charge_id}")
async def update_charge(
    charge_id: int,
    payload: QuoteChargeUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("quote:manage")),
    session: AsyncSession = Depends(get_db),
):
    """改一条附加费用（03-API §22）。

    金额变了必须重算版本汇总 —— 否则"合计金额"与附加费用对不上，
    报价单上两个数字自相矛盾。已发送的版本不允许改（与明细同一规则）。
    """
    charge = await session.get(QuoteCharge, charge_id)
    if charge is None:
        raise AppError(ErrorCode.NOT_FOUND, "附加费用不存在", 404)
    version = await svc.get_visible_version(session, user, charge.quote_version_id)
    await svc.ensure_version_editable(version)

    before = svc.serialize_charge(charge)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(charge, field, value)
    # 与 add_charge 同一规则：折扣在库里恒为负数，改完再归一一次
    if charge.is_discount and charge.amount > 0:
        charge.amount = -charge.amount
    await session.flush()
    await svc.recalc_version(session, version)
    await write_audit(
        session,
        operator_id=user.id,
        action="update_charge",
        business_type="quote",
        business_id=version.quote_id,
        before=before,
        after=svc.serialize_charge(charge),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_charge(charge), "已保存并重算合计")


@router.post("/quote-versions/{version_id}/recalculate")
async def recalculate_version(
    version_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("quote:manage")),
    session: AsyncSession = Depends(get_db),
):
    """重算版本汇总（03-API §21）。

    正常情况下每次改动都会自动重算，这个接口是**兜底与排查用**：
    发现合计对不上时手动跑一次，并返回重算前后对比。
    已发送的版本也会重算（只算金额、不改报价内容），
    因为"算错了"本身就该能被纠正。
    """
    version = await svc.get_visible_version(session, user, version_id)
    before = {
        "subtotal_amount": float(version.subtotal_amount),
        "charge_amount": float(version.charge_amount),
        "discount_amount": float(version.discount_amount),
        "total_amount": float(version.total_amount),
    }
    await svc.recalc_version(session, version)
    await session.flush()
    after = {
        "subtotal_amount": float(version.subtotal_amount),
        "charge_amount": float(version.charge_amount),
        "discount_amount": float(version.discount_amount),
        "total_amount": float(version.total_amount),
    }
    await write_audit(
        session,
        operator_id=user.id,
        action="recalculate",
        business_type="quote",
        business_id=version.quote_id,
        before=before,
        after=after,
        ip=client_ip(request),
    )
    await session.commit()
    changed = before != after
    return ok(
        {"before": before, "after": after, "changed": changed},
        "重算完成：合计有变化" if changed else "重算完成：合计本来就对",
    )


@router.post("/quote-versions/{version_id}/copy")
async def copy_version(
    version_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("quote:manage")),
    session: AsyncSession = Depends(get_db),
):
    """复制这一版为新版本（03-API §21）。

    与 `POST /quotes/{id}/versions` 是同一件事（都是"复制当前版"），
    区别是这个的入参是**被复制的版本 id**，语义更明确；
    前端从某个具体版本点"复制"时用这个更自然。
    """
    version = await svc.get_visible_version(session, user, version_id)
    quote = await svc.get_visible_quote(session, user, version.quote_id)
    # 以"被复制的这一版"为准整版照抄；不传 source 就会变成复制最新版。
    created = await svc.create_version(
        session, quote=quote, user=user, source_version_id=version_id
    )

    await write_audit(
        session,
        operator_id=user.id,
        action="copy_version",
        business_type="quote",
        business_id=quote.id,
        before={"source_version_id": version_id},
        after={"new_version_id": created.id, "version_no": created.version_no},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(
        {
            "version_id": created.id,
            "version_no": created.version_no,
            "source_version_id": version_id,
        },
        f"已从 V{version.version_no} 复制出 V{created.version_no}",
    )


@router.post("/quote-versions/{version_id}/expire")
async def expire_version(
    version_id: int,
    payload: QuoteExpire,
    request: Request,
    user: CurrentUser = Depends(require_permission("quote:manage")),
    session: AsyncSession = Depends(get_db),
):
    """把报价标记为已失效（03-API §21）。

    状态机里一直有 `expired` 但**全库没有任何地方写它** ——
    过了有效期没人处理，列表里永远停在"已发送"，看起来像还有效。
    这里补上入口：已发送/已通过的报价才能失效，失效后不能再转订单。
    """
    version = await svc.get_visible_version(session, user, version_id)
    quote = await svc.get_visible_quote(session, user, version.quote_id)
    if quote.status == "expired":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "该报价已经是失效状态")
    if quote.status not in ("sent", "approved"):
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"只有已发送或已通过的报价才能标记失效（当前：{QUOTE_STATUS_LABEL.get(quote.status, quote.status)}）",
        )

    before_status = quote.status
    quote.status = "expired"
    await write_audit(
        session,
        operator_id=user.id,
        action="expire",
        business_type="quote",
        business_id=quote.id,
        before={"status": before_status},
        after={"status": "expired", "reason": payload.reason},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(
        {"quote_id": quote.id, "status": quote.status, "version_id": version.id},
        "已标记为失效",
    )


@router.post("/quote-versions/{version_id}/generate-pdf")
async def generate_pdf_alias(
    version_id: int,
    user: CurrentUser = Depends(require_permission("quote:view")),
    session: AsyncSession = Depends(get_db),
):
    """生成报价 PDF（03-API §21 的 POST 写法）。

    实现与 `GET /quote-versions/{id}/pdf` 是同一份 `render_quote_pdf`；
    两个路径并存是因为文档要求 POST、而下载用 GET 更自然，
    前端已在用 GET。这里不重复实现，只转发。
    """
    return await download_pdf(version_id=version_id, user=user, session=session)


@router.post("/quote-versions/{version_id}/send-email")
async def send_email(
    version_id: int,
    payload: SendRequest,
    request: Request,
    user: CurrentUser = Depends(require_permission("quote:manage")),
    session: AsyncSession = Depends(get_db),
):
    """发送报价邮件（03-API §21）。

    **系统里没有邮件服务**（SMTP 未配置），所以这里不假装发送成功：
    只登记一条发送记录、把渠道记成"邮件"，并把报价标记为已发送 ——
    这与既有的「标记已发送」是同一件事，只是明确写了渠道与收件人。
    真正接 SMTP 时替换这一处即可，接口形状不变。
    """
    version = await svc.get_visible_version(session, user, version_id)
    quote = await svc.get_visible_quote(session, user, version.quote_id)
    # A12：审批闸门必须完整——只挡 pending 会让 not_submitted/rejected 的版本
    # 直接"发出去"，绕过 mark-sent 的 42203 前置
    if version.approval_status == "pending":
        raise AppError(ErrorCode.APPROVAL_PENDING, "该版本正在审批中，通过前不能发送")
    if version.approval_status != "approved":
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            "该版本未通过审批（未提交或被驳回），不能发送",
            422,
        )
    if not payload.receiver:
        raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "收件人必填", 422)

    now = datetime.now(UTC)
    session.add(
        QuoteSendLog(
            quote_version_id=version.id,
            channel=payload.channel or "邮件",
            receiver=payload.receiver,
            sent_by=user.id,
            status="logged",
            sent_at=now,
            error_message="尚未配置邮件服务，本次只登记发送记录（未实际投递）",
        )
    )
    # A12：未实际投递就不得把版本记成"已发送"——这里只是登记发送记录，
    # 状态流转仍走「标记已发送」（人工确认已实际送达后）。
    # 接 SMTP 后由投递结果驱动这一步。
    await write_audit(
        session,
        operator_id=user.id,
        action="send_email",
        business_type="quote",
        business_id=quote.id,
        after={"receiver": payload.receiver, "delivered": False},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(
        {
            "version_id": version.id,
            "receiver": payload.receiver,
            "delivered": False,
            "reason": "尚未配置邮件服务，已登记发送记录",
        },
        "已登记发送记录（邮件服务未配置，未实际投递）",
    )
