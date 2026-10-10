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
from app.modules.quote import lifecycle
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


def _with_master_warnings(payload: dict, warnings: set[str]) -> dict:
    """把"哪些 SKU 的主数据还没确认"放进**响应体**。

    为什么不能只拼在 message 里（审查 2026-10-07 实测）：前端统一客户端
    （`shared/api/client.ts` 的 `unwrap`）只把 `body.data` 交给页面，
    `message` 根本到不了界面，提示等于没写 —— 原来那个"起订量提醒"也是这样白写的。
    草稿阶段不阻断，但要让用户在明细页看得见"这几条的主数据还没确认"。
    """
    if not warnings:
        return payload
    return {**payload, "master_warnings": sorted(warnings)}


async def _ensure_inquiry_visible(
    session: AsyncSession, user: CurrentUser, inquiry_id: int | None
) -> None:
    """定制明细引用的需求必须在该用户数据范围内。

    此前 `_build_custom_item_snapshot` 只判"需求存在"，拿别人的需求 id 也能挂进
    自己的报价（需求标题/编号会落进快照、流到对客文件）——与"拿别人的 id 访问"
    同一形态，入口处先挡。
    """
    if inquiry_id is None:
        return
    from app.modules.inquiry import service as inquiry_service

    await inquiry_service.get_visible_or_404(session, user, inquiry_id)


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

    **请求幂等（第八批 8.15）**：带同一把 `request_key`（body 字段或
    `X-Request-Key` 头）时，弱网重试只建一条报价并回放第一次的响应；
    同键不同内容报冲突。**没有键就照旧创建**，但响应里说明这次没有幂等保护——
    不假装重试是安全的。注意：同客户两次真实需求必须用**不同的键**，
    服务端不会按"内容相同"否定合法的新报价。
    """
    from app.core import idempotency

    data = payload.model_dump()
    request_key = idempotency.request_key_from(request, data.pop("request_key", None))

    reservation = None
    if request_key:
        reservation = await idempotency.reserve(
            session,
            user_id=user.id,
            action="quote:create",
            request_key=request_key,
            payload=data,
            result_type="quote",
        )
        if reservation.should_replay:
            # 回放前重查**当前**可见性（2026-10-07 修）：这份报价可能已经移交给别人、
            # 或者被软删了。原来直接返回缓存 —— 报价早就不是你的了，凭旧请求键照样读走。
            # 口径：**幂等保护的是"不重复创建"，不是"永久授权"**。
            if reservation.replay_id is not None:
                try:
                    await svc.get_visible_quote(session, user, reservation.replay_id)
                except AppError:
                    raise AppError(
                        ErrorCode.DATA_SCOPE_DENIED,
                        "这条记录已不在你的可见范围内（可能已移交或删除），无法回放原结果",
                        403,
                    ) from None
            return ok(
                reservation.replay_payload,
                "这次提交此前已成功生成过报价，已返回原报价（没有重复创建）",
            )

    try:
        opportunity = None
        if payload.opportunity_id:
            opportunity = await session.get(Opportunity, payload.opportunity_id)
            if opportunity is None or opportunity.deleted_at is not None:
                raise AppError(ErrorCode.NOT_FOUND, "商机不存在", 404)

        # 客户/联系人存在性由 svc.create_quote 统一校验（Agent 工具同路）。
        # §8.14：主数据未确认只提示、不阻断（字段权威业务尚未拍板）
        unconfirmed_master: set[str] = set()
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
            unconfirmed_out=unconfirmed_master,
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
        body = {
            "quote_id": quote.id,
            "version_id": version.id,
            "currency": version.currency,
            "exchange_rate_snapshot": created["exchange_rate_snapshot"],
            "warnings": created["warnings"],
            # §8.14：草稿里哪些 SKU 的主数据还没确认。必须放进**响应体** ——
            # 前端统一客户端只把 data 交给页面，拼在 message 里的提示根本到不了界面
            # （审查 2026-10-07 实测；原来那个"起订量提醒"也是同样白写）。
            "master_warnings": sorted(unconfirmed_master),
        }
        if reservation is not None:
            await idempotency.complete(
                session, reservation, result_payload=body, result_id=quote.id
            )
        await session.commit()
    except Exception:
        # 失败就释放占位：用户改完表单会带同一把键重试，改完的内容必然不同，
        # 不释放会把"改错重填"误判成"同键不同内容"冲突（与客户创建同口径）。
        if reservation is not None:
            await idempotency.release(session, reservation)
        raise

    message = "报价单已生成"
    if unconfirmed_master:
        message += (
            "；主数据提醒："
            + "；".join(sorted(unconfirmed_master))
            + "（本次按本地值报价，确认后请重新生成明细）"
        )
    if request_key:
        return ok(body, message)
    return ok(body, message + "（本次未带请求键，弱网重试可能产生重复报价）")


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
    if "valid_until" in data:
        # §8.7：有效期是**对客字段**，主单与当前版本的快照必须一起动 ——
        # 否则主单显示 12-01、对客文件还是 11-01，而发送与过期判断又在用主单，
        # 三方口径互不一致（审查 2026-10-07 复验的第二种写法）。
        # 可编辑的草稿版同步；已提交审批/已发送的版本按既定锁定规则**拒绝** ——
        # 不允许借"改一下主单"把已经对外的历史口径改掉。
        current_version = (
            await session.get(QuoteVersion, quote.current_version_id)
            if quote.current_version_id
            else None
        )
        if current_version is not None:
            await svc.ensure_version_editable(current_version)
            current_version.valid_until_snapshot = data["valid_until"]
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
                    "task_due_at": row.planned_at,
                    "exemption_reason": row.exemption_reason,
                    "next_task_id": row.next_task_id,
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


def _can_see_cost(user: CurrentUser) -> bool:
    """这份报价能不能看到**公司内部成本口径**的字段（含由它推出的利润/底价）。

    判据与查价/核价/价格中心完全一致：`price:manage`。
    从前报价详情、明细列表、PDF 导出都无条件返回成本，而查价那边已经隐藏 ——
    同一个用户、同一条数据，两个入口两种口径（issue #5）。
    """
    return user.has("price:manage")


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
    # ---- 最终商机必须校验（2026-10-10 修 issue #6）----
    #
    # 从前这里只取了个 id，紧接着 `new_quote.opportunity_id = ...` **直接赋值** ——
    # 既没验数据范围、也没验客户一致性，实测：
    #   A 读不到的商机（403）→ 复制时填它的 id → **复制成功**，
    #   新报价 customer_id = A 自己的客户，却挂到 B 客户的商机上 → 串账。
    # 下面这段与"复制需求行"那段 `opportunity.customer_id != target_customer` 同一口径，
    # 但**必须在建任何报价之前**跑完 —— 越范围与跨客户要在创建前拒绝。
    from app.modules.opportunity import service as opportunity_service

    target_opportunity = await opportunity_service.get_visible_opportunity(
        session, user, final_opportunity_id
    )
    target_customer_id = payload.customer_id or source.customer_id
    if target_opportunity.customer_id != target_customer_id:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"商机（{target_opportunity.title}）属于客户 id={target_opportunity.customer_id}，"
            f"与本次报价的客户 id={target_customer_id} 不一致，请确认后再复制",
            422,
        )
    # §8.14：主数据未确认只提示、不阻断
    unconfirmed_master: set[str] = set()
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
        unconfirmed_out=unconfirmed_master,
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
                    # 复制必须与 create_version 完全对齐，少一个字段就是一条断链：
                    # 这里曾漏 inquiry_id/inquiry_no_snapshot（定制件失去溯源）、
                    # price_source/customer_level_snapshot（漂移检测把系统价当人工价）、
                    # tax_refund_snapshot/profit_with_refund_snapshot（外币单复制后
                    # 退税利润丢失）。2026-09-30 复核补齐。
                    inquiry_id=item.inquiry_id,
                    inquiry_no_snapshot=item.inquiry_no_snapshot,
                    sku_code_snapshot=item.sku_code_snapshot,
                    sku_name_snapshot=item.sku_name_snapshot,
                    spec_snapshot=item.spec_snapshot,
                    # 单位快照同样要复制（§8.7）：少一个字段就是一条断链
                    unit_snapshot=item.unit_snapshot,
                    # §8.14：主数据版本号也要复制 —— 明细内容原样搬过来，追溯口径不变
                    master_version_no=item.master_version_no,
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
                    price_source=item.price_source,
                    customer_level_snapshot=item.customer_level_snapshot,
                    tax_refund_snapshot=item.tax_refund_snapshot,
                    profit_with_refund_snapshot=item.profit_with_refund_snapshot,
                    approval_required=item.approval_required,
                    approval_reason=item.approval_reason,
                    remark=item.remark,
                )
            )
            copied_items += 1
        # 费用行也要整份抄过来（与 `service.create_version` 共用同一份实现）。
        # 这条路径以前只抄明细不抄费用 —— 复制出来的新单货款对、**运费与折扣全丢**，
        # 而缺运费的报价在正式发送时会被 `ensure_freight_confirmed` 拦下。
        await session.flush()
        await svc.copy_version_charges(
            session, source_id=source.current_version_id, target_version=new_version
        )
        await session.flush()
        # 复制过来的快照是**按源报价口径**算的，而复制出的是新报价（当前口径）。
        # 不重算就会照抄旧利润（审查实测：单价 100、成本 80，复制后利润仍是 15，
        # 正确应为 20）。与 `service.create_version` 用**同一个**函数，
        # 保证"建立新版"与"复制报价"两个入口口径一致 —— 上一版我只接了前者，
        # 漏了这里（审查 2026-10-09 第二次指出）。
        for copied in await svc.version_items(session, new_version.id):
            await svc.apply_current_basis_to_item(
                session, copied, new_version, user=user
            )
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
    clone_message = f"已从 {source.quote_no} 复制出 {new_quote.quote_no}"
    if unconfirmed_master:
        clone_message += (
            "；主数据提醒："
            + "；".join(sorted(unconfirmed_master))
            + "（本次按本地值报价）"
        )
    return ok(
        _with_master_warnings(
            {
                "quote_id": new_quote.id,
                "quote_no": new_quote.quote_no,
                "version_id": created["_version"].id,
                "copied_items": copied_items,
                "source_quote_id": source.id,
            },
            unconfirmed_master,
        ),
        clone_message,
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


@router.get("/quote-versions/{version_id}/master-refresh-preview")
async def master_refresh_preview(
    version_id: int,
    user: CurrentUser = Depends(require_permission("quote:view")),
    session: AsyncSession = Depends(get_db),
):
    """刷新主数据**之前**先看会变什么（issue 建议第 5 条）。

    只读、不写。返回每条明细里名称/规格/单位的"从什么变成什么"，
    由用户看过之后再决定是否真的刷新。

    为什么先预览：这三个字段是**印给客户**的，刷新会直接改掉它们。
    让人先看清再确认，比刷完发现印错了便宜得多。

    ⚠️ **已发送的版本不给刷**：报价发出去之后内容就是对客承诺，
    改它等于改承诺 —— 这条路只能新建版本。这里用一个显式字段
    `refreshable` 告诉前端，而不是靠前端自己猜。
    """
    from app.modules.product import master as master_svc

    version = await svc.get_visible_version(session, user, version_id)
    items = await svc.version_items(session, version.id)
    preview = await master_svc.quote_master_refresh_preview(
        session, version=version, items=items
    )
    sent = version.sent_at is not None
    submitted = version.approval_status in ("pending", "approved")
    preview["sent"] = sent
    preview["refreshable"] = not (sent or submitted)
    if sent:
        preview["blocked_reason"] = (
            "这一版已经发给客户了，内容是对客承诺，不能刷新 —— "
            "请「新建版本」后再刷新"
        )
    elif submitted:
        preview["blocked_reason"] = (
            "这一版已提交审批，不能直接刷新 —— 请「新建版本」后再刷新"
        )
    return ok(preview)


@router.post("/quote-versions/{version_id}/link-master")
async def link_master(
    version_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("quote:manage")),
    session: AsyncSession = Depends(get_db),
):
    """一键修复：把明细接到**最新已确认主数据**上，**不碰价格**。

    为什么要有这条独立的路（2026-10-10 实测）：黄条说"这条明细没有可引用的
    已确认主数据版本"，用户去产品中心确认完回来点「刷新主数据」，**黄条还在** ——
    因为刷新复用的是 `refresh_prices`，而它在"手工定价"或"查不到价"时
    直接跳过整条明细。于是**"接主数据"被"查不到价"挡住了**。

    这两件事本来就不该绑在一起：主数据版本号回答的是"这一行对着哪一版
    名称/规格/单位"，与"这一行卖多少钱"无关。所以这里只重建对客三字段快照
    + 钉版本号，价格/成本/利润一个字不动。

    缺确认时**明确报缺什么**，不静默跳过 —— 静默跳过正是"点了没反应"的来源。
    """
    version = await svc.get_visible_version(session, user, version_id)
    sent = version.sent_at is not None
    submitted = version.approval_status in ("pending", "approved")
    if sent or submitted:
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            "这一版已经发给客户或已提交审批，内容不能改 —— 请「新建版本」后再接入主数据",
            422,
        )
    result = await svc.link_master_only(session, version=version)
    await write_audit(
        session,
        operator_id=user.id,
        action="quote_link_master",
        business_type="quote",
        business_id=version.quote_id,
        after=result,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(result, result["message"])


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
    confirm: bool = False,
    user: CurrentUser = Depends(require_permission("quote:manage")),
    session: AsyncSession = Depends(get_db),
):
    """新建版本：复制上一版全部明细与费用，旧版本原样保留、不可覆盖。

    复制逻辑在 `svc.create_version`，与 Agent 工具 `create_quote_version` 共用。

    ⚠️ **必须先确认**（审查 2026-10-10）：这个按钮点一下不只是"多一份草稿"，
    它还会**切换当前版本、把报价状态改回草稿、并自动结束旧版还在走的审批流程**，
    而系统里**没有**"撤销新版本、恢复上述状态"的入口。所以不带 `confirm=True`
    时这里直接拒，并把"点了会怎样"讲清楚 —— 前端拿它当确认弹窗的正文。
    """
    quote = await svc.get_visible_quote(session, user, quote_id)
    if not confirm:
        from app.core.confirmation import confirmation_message

        latest = await svc.latest_version(session, quote_id=quote.id)
        if latest is None:
            raise AppError(ErrorCode.NOT_FOUND, "报价单没有版本", 404)
        # ⚠️ **不要把最新版排除掉**：`close_superseded_approvals` 会结束
        # 除新版本之外**所有**版本的待审批 —— 包括正在跑审批的最新版自己
        # （"V2 审批中，我又做了 V3"正是最常见的场景）。
        # 排除最新版的话，最该被点名的那一版反而不会出现在提示里（我踩过）。
        pending = await svc.versions_with_pending_approval(session, quote_id=quote.id)
        parts = [
            f"将基于当前最新版 V{latest.version_no} 创建 V{latest.version_no + 1}，"
            "并切换为当前草稿",
            "报价状态会改回草稿",
        ]
        if pending:
            parts.append(
                "旧版 V"
                + "、V".join(str(no) for _id, no in pending)
                + " 的待审批流程将结束"
            )
        parts.append("历史资料保留，但**没有撤销入口**")
        raise AppError(
            ErrorCode.CONFIRM_REQUIRED,
            confirmation_message(action="新建报价版本", detail="；".join(parts)),
            422,
        )

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
    body = {
        "version": svc.serialize_version(version, total_profit),
            # 金额汇总由**后端**算好给前端（2026-10-09）：货款 / 运费 / 其他费用 /
            # 优惠 / 应付合计。页面、对客文件、订单共用这一份公式，
            # 不让前端再实现一套（两套公式迟早对不上，而这里对不上就是钱对不上）。
            "summary": svc.amount_summary(version),
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
            "items": [
                svc.serialize_item(item, can_see_cost=_can_see_cost(user))
                for item in items
            ],
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
    # §8.14 复审（第四项）：未确认清单要随**详情**一起给，页面才能"持续显示"。
    # 只靠保存明细时闪一次 Toast 不够 —— 刷新页面、换个人打开、隔天再看，
    # 都该看得到"这几条的主数据还没确认"，而不是等到发送被拒才知道。
    master_problems = await svc.master_confirmation_problems(
        session, version_id=version_id
    )
    # 运费未确认同样要**持续可见**（2026-10-09）：草稿允许没填运费，
    # 但页面上要一直提示"正式发送前得先确认运费"，而不是等发送被拒才知道。
    body["freight_unconfirmed_reason"] = svc.quote_freight_unconfirmed_reason(charges)
    return ok(_with_master_warnings(body, set(master_problems)))


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
        # §8.7 复审（第三轮）修的两个毛病：
        #
        # ① **本版快照也要跟着改**。原来只写主单，于是"主单 2027-01-01、
        #    版本快照与 PDF/BizDoc 还是 2026-12-01" —— 业务判断（发送/过期读主单）
        #    和印给客户的文件对不上。快照是这一版对客有效期的唯一依据。
        # ② **只有当前版本才动主单**。主单的 `valid_until` 语义是"当前版本的
        #    对客有效期"；编辑一个**历史草稿版**时若顺手改主单，等于把另一个
        #    版本（以及正在生效的那一版）的有效期改掉了。
        if quote.current_version_id == version.id:
            quote.valid_until = valid_until
        version.valid_until_snapshot = valid_until
        data["valid_until_snapshot"] = valid_until.isoformat()
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
    # §8.14：这几条 SKU 的关键字段主数据还没人工确认 —— 只提示，不阻断报价
    unconfirmed_master: set[str] = set()
    for payload in items:
        await _ensure_inquiry_visible(session, user, payload.inquiry_id)
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
            # 定制项（场景09）：无 SKU 时按需求编号 + 人工核价成本落快照
            inquiry_id=payload.inquiry_id,
            item_name=payload.item_name,
            unit_cost=payload.unit_cost,
            unconfirmed_out=unconfirmed_master,
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
    if unconfirmed_master:
        message += (
            "；主数据提醒："
            + "；".join(sorted(unconfirmed_master))
            + "（本次按本地值报价，确认后请重新生成明细）"
        )
    return ok(
        _with_master_warnings(svc.serialize_version(version), unconfirmed_master), message
    )


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

    # 只有**会影响价格/成本**的字段变了才重建快照（2026-10-09 缺陷③）。
    #
    # 从前这里无条件重建，于是"只改一句备注"也会把成本、底价、利润、建议价
    # 全部按**当前主数据与当前口径**重算一遍 —— 审查实测：产品价格、数量、成本
    # 一个字没动，利润却从 15 变成 20（因为旧快照是旧口径算的、重建按新口径算）。
    # 报价明细的快照是**发出时的凭证**，不该被一次备注修改改写。
    #
    # 判据：`QuoteItemUpdate` 里只有 quantity / quoted_price / unit_cost /
    # logistics_cost 四个字段影响价格与成本；remark 不影响。
    # `exclude_unset` 保证"没传"与"传了同值"都不会误判成变化。
    affects_pricing = any(
        key in data for key in ("quantity", "quoted_price", "unit_cost", "logistics_cost")
    )
    if not affects_pricing:
        if "remark" in data:
            item.remark = data["remark"]
        await session.flush()
        await write_audit(
            session,
            operator_id=user.id,
            action="update_item",
            business_type="quote",
            business_id=quote.id,
            before={"item_id": item.id, "quoted_price": old_price, "remark_only": True},
            after={"item_id": item.id, "quoted_price": float(item.quoted_price)},
            ip=client_ip(request),
        )
        await session.commit()
        return ok(svc.serialize_item(item), "已保存（只改了备注，价格与成本快照未重算）")

    # §8.14：主数据未确认只提示、不阻断（见 build_item_snapshot 的说明）
    unconfirmed_master: set[str] = set()
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
        # 定制行必须把需求编号带下去：重算走的是"sku_id 为空 → 定制分支"，
        # 少了 inquiry_id 就会报"明细必须关联 SKU 或定制需求编号"，
        # 于是新做的定制报价只能一次填死、改不动（真踩过）。
        inquiry_id=item.inquiry_id,
        item_name=item.sku_name_snapshot,
        # 没传新成本就沿用原快照（人民币口径），不能丢
        unit_cost=(
            data.get("unit_cost", item.cost_snapshot) if item.sku_id is None else None
        ),
        unconfirmed_out=unconfirmed_master,
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
        # 单位快照（§8.7）：改一条明细会重建快照，落下它才不会让改完的行显示"待核实"
        "unit_snapshot",
        # §8.14：重建会重新经过主数据解析，版本号跟着重建后的口径走
        "master_version_no",
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
    message = "已保存"
    if unconfirmed_master:
        message += (
            "；主数据提醒："
            + "；".join(sorted(unconfirmed_master))
            + "（本次按本地值报价）"
        )
    return ok(
        _with_master_warnings(svc.serialize_item(item), unconfirmed_master), message
    )


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
    # 物流费用：通过接口填进来就是一次**显式确认**（含明确确认的零运费）。
    # 不这么做的话，"amount=0" 就分不清是"没填"还是"确实是零运费"，
    # 而正式发送必须要求后者才算已确认。
    svc.mark_logistics_confirmed(charge)
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
    version = await svc.get_visible_version(session, user, version_id, for_update=True)
    quote = await svc.get_visible_quote(session, user, version.quote_id)
    lifecycle.ensure_current_version(quote, version)
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
        operator_id=user.id,
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
    version = await svc.get_visible_version(session, user, version_id, for_update=True)
    lifecycle.ensure_current_version(await svc.get_visible_quote(session, user, version.quote_id), version)
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
    version = await svc.get_visible_version(session, user, version_id, for_update=True)
    quote = await svc.get_visible_quote(session, user, version.quote_id)
    changed = await lifecycle.mark_version_sent(session, quote=quote, version=version,
                                                operator_id=user.id, payload=payload, ip=client_ip(request))
    await session.commit()
    await notification_service.dispatch_pending(session)
    return ok(svc.serialize_version(version), "已标记为已发送" if changed else "该次发送已记录")


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
    version = await svc.get_visible_version(session, user, version_id, for_update=True)
    quote = await svc.get_visible_quote(session, user, version.quote_id)
    await lifecycle.accept_version(session, quote=quote, version=version,
                                   operator_id=user.id, ip=client_ip(request))
    await session.commit()
    await notification_service.dispatch_pending(session)
    return ok(svc.serialize_version(version), "客户已接受，可以转订单了")


@router.post("/quote-versions/{version_id}/reject")
async def reject_quote(
    version_id: int,
    payload: DeclinedRequest,
    request: Request,
    user: CurrentUser = Depends(require_permission("quote:manage")),
    session: AsyncSession = Depends(get_db),
):
    version = await svc.get_visible_version(session, user, version_id, for_update=True)
    quote = await svc.get_visible_quote(session, user, version.quote_id)
    await lifecycle.decline_version(session, quote=quote, version=version,
                                    operator_id=user.id, reason=payload.reason, ip=client_ip(request))
    await session.commit()
    await notification_service.dispatch_pending(session)
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
        "customer_name": version.customer_name_snapshot or "待核实",
        "opportunity_title": ctx["titles"].get(quote.opportunity_id) if quote.opportunity_id else None,
        "owner_name": ctx["owners"].get(quote.owner_id) if quote.owner_id else user.name,
        "quote_date": version.created_at.strftime("%Y-%m-%d"),
        # §8.7：对客口径一律取**这一版冻结的快照**，不回查当前客户资料 / 主单。
        # 原来这里读的是 `ctx["customers"]`（当前客户名）和 `quote.valid_until`
        # （主单当前有效期），于是同一个 V1：BizDoc 印的是旧名、PDF 印的是新名。
        # 历史版本没留存 → 显示"待核实"，不拿今天的资料冒充当时发出去的那一份。
        "valid_until": (
            version.valid_until_snapshot.isoformat()
            if version.valid_until_snapshot
            else "待核实"
        ),
        "status_label": QUOTE_STATUS_LABEL.get(quote.status, quote.status),
        "items": [
            svc.serialize_item(item, can_see_cost=_can_see_cost(user))
            for item in items
        ],
        "charges": [
            {**svc.serialize_charge(charge), "type_label": CHARGE_LABEL.get(charge.charge_type)}
            for charge in charges
        ],
        "subtotal_amount": float(version.subtotal_amount),
        "charge_amount": float(version.charge_amount),
        "discount_amount": float(version.discount_amount),
        "total_amount": float(version.total_amount),
        # 金额拆分（2026-10-09 运费分离）：对客 PDF 要把运费**单独列一行具体金额**，
        # 不能混在"附加费用"里让客户自己猜。同时给出统一汇总块，
        # 保证 PDF、页面、订单、应收四处口径一致。
        "summary": svc.amount_summary(version),
        #: 产品单价的对外口径说明：运费分离后必须在客户看得到的文件上说清楚，
        #: 否则客户按"含运费"的旧口径理解，会以为运费已经包在单价里。
        #:
        #: ⚠️ 只在本版**确实是**"单价不含运费"口径时才写（2026-10-09 修）。
        #: 从前这里无条件写死，等于给历史版本硬加一句它当时并不成立的说明 ——
        #: 审查实测出同一份 Excel 同时出现"交付条件：含运费，送货上门"与
        #: "以上产品单价均不含运费"，自相矛盾，等于改写了已发文件的口径。
        #: 口径判据只有 `version_logistics_in_base_cost` 一份（统一后恒为 False，
        #: 即新口径；这里仍写成分支，将来若加回"运费进价"的计价方式不会再错）。
        "unit_price_note": (
            None if svc.version_logistics_in_base_cost(version)
            else "以上产品单价均不含运费"
        ),
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
    return ok(
        [svc.serialize_item(item, can_see_cost=_can_see_cost(user)) for item in items]
    )


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
    await _ensure_inquiry_visible(session, user, payload.inquiry_id)
    # §8.14：主数据未确认只提示、不阻断
    unconfirmed_master: set[str] = set()
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
        # 定制项（场景09）：无 SKU 时按需求编号 + 人工核价成本落快照
        inquiry_id=payload.inquiry_id,
        item_name=payload.item_name,
        unit_cost=payload.unit_cost,
        unconfirmed_out=unconfirmed_master,
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
    message = "明细已添加"
    if moq_hint:
        message += f"；注意：{moq_hint}"
    if unconfirmed_master:
        message += (
            "；主数据提醒："
            + "；".join(sorted(unconfirmed_master))
            + "（本次按本地值报价）"
        )
    return ok(
        _with_master_warnings(svc.serialize_item(item), unconfirmed_master), message
    )


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
    # 保存即重新确认一次运费（含"从物流改成别的分类"→ 清空确认时刻）。
    # 顺序不能挪到折扣归一之前：`is_logistics_charge` 要看最终的 is_discount。
    svc.mark_logistics_confirmed(charge)
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
    version = await svc.get_visible_version(session, user, version_id, for_update=True)
    quote = await svc.get_visible_quote(session, user, version.quote_id)
    lifecycle.ensure_current_version(quote, version)
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
    只登记未投递记录，不写正式发送事实、不自动推进商机。
    实际对客发送后须使用 mark-sent 确认。
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
