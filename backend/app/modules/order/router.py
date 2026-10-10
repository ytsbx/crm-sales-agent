"""订单中心接口（对齐 03-API §27 / §28）。"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.data_scope import scoped_owner_ids
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.data_scope import ensure_in_scope
from app.core.errors import AppError, ErrorCode
from app.core.refs import ensure_refs
from app.core.response import ok, page_data, paginate
from app.modules.customer.model import Customer
from app.modules.erp import service as erp_service
from app.modules.erp.adapter import ErpError, ErpNotConfigured
from app.modules.erp.router import translate_erp_error as erp_translate
from app.modules.followup import service as followup_service
from app.modules.notification import service as notification_service
from app.modules.opportunity.model import Opportunity, OpportunityItem, OpportunityStageHistory
from app.modules.opportunity.service import get_first_stage
from app.modules.order import service as svc
from app.core.timebase import today_business
from app.modules.order import milestones as milestones_svc
from app.modules.order.model import (
    ORDER_STATUS_LABEL,
    OrderMilestone,
    OrderShipmentBatch,
    OrderStatusHistory,
    SalesOrder,
    SalesOrderItem,
)
from app.modules.order.schema import (
    MilestoneUpdate,
    OrderCreate,
    OrderFromQuote,
    OrderStatusChange,
    OrderUpdate,
    ScheduleChangeCancel,
    ScheduleChangeConfirm,
    ScheduleChangeCreate,
    ShipmentBatchCreate,
    ShipmentBatchShip,
)
from app.modules.payment.model import PaymentRecord, ReceivablePlan
from app.modules.product.model import Sku
from app.modules.quote.model import Quote, QuoteVersion
from app.modules.user.model import User

router = APIRouter(tags=["Order"])


@router.get("/orders")
async def list_orders(
    keyword: str | None = None,
    status: str | None = None,
    customer_id: int | None = None,
    opportunity_id: int | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user: CurrentUser = Depends(require_permission("order:view")),
    session: AsyncSession = Depends(get_db),
):
    stmt = select(SalesOrder)
    if opportunity_id is not None:
        stmt = stmt.where(SalesOrder.opportunity_id == opportunity_id)
    if keyword:
        stmt = stmt.where(SalesOrder.order_no.ilike(f"%{keyword.strip()}%"))
    if status:
        stmt = stmt.where(SalesOrder.status == status)
    if customer_id:
        stmt = stmt.where(SalesOrder.customer_id == customer_id)
    owner_ids = await scoped_owner_ids(session, user)
    if owner_ids is not None:
        stmt = stmt.where(SalesOrder.owner_id.in_(owner_ids))

    rows, total = await paginate(session, stmt.order_by(SalesOrder.id.desc()), page, page_size)
    ctx = await svc.order_context(session, rows)
    items = [
        svc.serialize_order(
            order,
            customer_name=ctx["customers"].get(order.customer_id),
            owner_name=ctx["owners"].get(order.owner_id) if order.owner_id else None,
            sales_owner_name=(
                ctx["owners"].get(order.sales_owner_id) if order.sales_owner_id else None
            ),
            received_amount=ctx["received"].get(order.id, 0),
            item_count=ctx["counts"].get(order.id, 0),
        )
        for order in rows
    ]
    return ok(page_data(items, total, page, page_size))


@router.post("/orders")
async def create_order(
    payload: OrderCreate,
    request: Request,
    # 手工建单不需要"客户已接受报价"，却计入成交额与业绩，所以口径定为**只有主管能用**
    # （已确认）。用 order:assign 落地：它是主管级权限（主管/管理员有，业务员与财务都没有），
    # 与"代别人分配/确认订单"同一档，不新造角色常量、也不新增一份"主管"判断副本。
    user: CurrentUser = Depends(require_permission("order:assign")),
    session: AsyncSession = Depends(get_db),
):
    """手工建销售订单（03-API §27）。

    正常订单来自「报价版本转订单」（`POST /quote-versions/{id}/convert-to-order`），
    这里是线下签约/补录历史单的入口。金额由明细算出，不接受前端传。

    这条路径**不校验"客户已接受报价"**，因此限主管使用：否则它就是绕过客户确认、
    直接给自己敲单加业绩的后门（金额进成交额，却不生成应收）。
    """
    await ensure_refs(
        session, model=Customer, ids={"customer_id": payload.customer_id}, label="客户"
    )
    await ensure_refs(
        session, model=User, ids={"owner_id": payload.owner_id}, label="负责人"
    )
    await ensure_refs(
        session,
        model=Opportunity,
        ids={"opportunity_id": payload.opportunity_id},
        label="商机",
    )
    await ensure_refs(session, model=Quote, ids={"quote_id": payload.quote_id}, label="报价单")

    # **存在 ≠ 可见**（P1）：上面只确认了这些对象存在，没确认在当前用户的数据范围内。
    # 只查存在性的话，业务员可以拿别人的客户/商机/报价建单——建出来的单子还挂在
    # 自己身上，等于把别人的客户资源搬进自己名下。
    customer = await session.get(Customer, payload.customer_id)
    await ensure_in_scope(session, user, owner_id=customer.owner_id, label="客户")
    if payload.opportunity_id:
        opportunity = await session.get(Opportunity, payload.opportunity_id)
        await ensure_in_scope(session, user, owner_id=opportunity.owner_id, label="商机")
        # 归属一致性：商机必须就是这家客户的。不加这条，就能拿 A 客户的商机
        # 配 B 客户建单，事后谁也说不清这单算谁的
        if opportunity.customer_id and opportunity.customer_id != payload.customer_id:
            raise AppError(ErrorCode.PARAM_ERROR, "商机与客户不是同一家，不能混用", 422)
    if payload.quote_id:
        quote = await session.get(Quote, payload.quote_id)
        await ensure_in_scope(session, user, owner_id=quote.owner_id, label="报价单")
        if quote.customer_id and quote.customer_id != payload.customer_id:
            raise AppError(ErrorCode.PARAM_ERROR, "报价单与客户不是同一家，不能混用", 422)

    order = await svc.create_order(
        session,
        user_id=user.id,
        customer_id=payload.customer_id,
        items=payload.items,
        opportunity_id=payload.opportunity_id,
        quote_id=payload.quote_id,
        owner_id=payload.owner_id,
        currency=payload.currency,
        delivery_date=payload.delivery_date,
        payment_terms=payload.payment_terms,
        remark=payload.remark,
    )
    await write_audit(
        session,
        operator_id=user.id,
        action="create",
        business_type="order",
        business_id=order.id,
        after=svc.serialize_order(order),
        ip=client_ip(request),
    )
    # 领导六阶段口径"过程记录"：下单自动写跟进并推送业务主管
    await followup_service.record_and_notify(
        session,
        customer_id=order.customer_id,
        owner_id=order.owner_id,
        title=f"订单已创建 {order.order_no}",
        content=f"{order.order_no} 金额 ¥{float(order.total_amount):,.2f}（手工建单）",
        business_type="order",
        business_id=order.id,
        order_id=order.id,
        operator_id=user.id,
        exclude_user_id=user.id,
        event_key=f"order:create:{order.id}",
    )
    await session.commit()
    await notification_service.dispatch_pending(session)
    return ok(
        {
            "order_id": order.id,
            "order_no": order.order_no,
            "total_amount": float(order.total_amount),
            # 币种跟着金额一起给（第九批 §9.9）：前端成功提示要按订单币种显示，
            # 不能假设是人民币
            "currency": order.currency,
        },
        "订单已创建",
    )


@router.post("/orders/{order_id}/refresh-status")
async def refresh_status(
    order_id: int,
    user: CurrentUser = Depends(require_permission("order:view")),
    session: AsyncSession = Depends(get_db),
):
    """拉取 ERP 履约状态并回写（03-API §27）。

    与 `GET /integrations/erp/orders/{id}/status` 是同一件事，
    这里给订单详情页一个"就地刷新"的入口。
    """
    order = await svc.get_visible_order(session, user, order_id)
    try:
        result = await erp_service.refresh_status(session, order=order, operator_id=user.id)
    except (ErpNotConfigured, ErpError) as error:
        await session.rollback()
        raise erp_translate(error) from error
    await session.commit()
    return ok(result, "状态已同步" if result["changed"] else "状态没有变化")


@router.post("/quote-versions/{version_id}/convert-to-order")
async def convert_to_order(
    version_id: int,
    payload: OrderFromQuote,
    request: Request,
    user: CurrentUser = Depends(require_permission("order:manage")),
    session: AsyncSession = Depends(get_db),
):
    version = await session.get(QuoteVersion, version_id)
    if version is None:
        raise AppError(ErrorCode.NOT_FOUND, "报价版本不存在", 404)
    # 转单也要过数据范围（P1）：只判断"版本存在"的话，业务员能拿别人的报价
    # 转出自己的订单——订单转出来就挂在自己名下，等于把别人的成交搬走
    quote = await session.get(Quote, version.quote_id)
    if quote is not None:
        await ensure_in_scope(session, user, owner_id=quote.owner_id, label="报价单")
    order = await svc.create_order_from_quote(
        session,
        version=version,
        user_id=user.id,
        delivery_date=payload.delivery_date,
        remark=payload.remark,
    )
    await write_audit(
        session,
        operator_id=user.id,
        action="create",
        business_type="order",
        business_id=order.id,
        after=svc.serialize_order(order),
        ip=client_ip(request),
    )
    await session.commit()
    await notification_service.dispatch_pending(session)
    return ok({"order_id": order.id, "order_no": order.order_no}, "已生成销售订单")


@router.get("/orders/{order_id}")
async def get_order(
    order_id: int,
    user: CurrentUser = Depends(require_permission("order:view")),
    session: AsyncSession = Depends(get_db),
):
    order = await svc.get_visible_order(session, user, order_id)
    ctx = await svc.order_context(session, [order])
    return ok(
        svc.serialize_order(
            order,
            customer_name=ctx["customers"].get(order.customer_id),
            owner_name=ctx["owners"].get(order.owner_id) if order.owner_id else None,
            sales_owner_name=(
                ctx["owners"].get(order.sales_owner_id) if order.sales_owner_id else None
            ),
            received_amount=ctx["received"].get(order.id, 0),
            item_count=ctx["counts"].get(order.id, 0),
        )
    )


@router.patch("/orders/{order_id}")
async def update_order(
    order_id: int,
    payload: OrderUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("order:manage")),
    session: AsyncSession = Depends(get_db),
):
    order = await svc.get_visible_order(session, user, order_id)
    before = svc.serialize_order(order)

    data = payload.model_dump(exclude_unset=True)
    # 交期**不允许在这里直接改**（P1）：这条路径不会重排跟单节点与发货批次，
    # 改完交期与计划日就静默脱节——页面上看着改了，实际计划还按老交期排，
    # 逾期提醒也跟着错。要走「交期变更单」：先预览受影响面、再由责任人确认。
    if "delivery_date" in data and data["delivery_date"] != order.delivery_date:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            "改交期请走「交期变更」：先预览受影响的节点与批次，再由责任人确认，"
            "系统会在确认时一并重排计划日期。直接改交期不会重排计划，"
            "会造成交期与计划日脱节",
            422,
        )
    # 改的是「当前负责人」（谁跟进、谁看得见），**不动 sales_owner_id**：
    # 签单归属创建时写死，换人跟进不改变这张单的业绩算谁的（文档 :61）。
    #
    # ⚠️ 这里必须用 `"owner_id" in data` 而不是 `data.get("owner_id") is not None`
    # （审查 B2-01）。原来的写法只看"值非空"，于是：
    #   - `{"owner_id": null}` 直接**跳过整个权限分支**，
    #   - 而下面那个 `for field, value in data.items(): setattr(...)` 是无差别写入，
    #     照样把 `owner_id` 置成 NULL —— **等于用"清空"绕过了 order:assign**。
    # 实测：只有 order:manage 的账号改成别人 → 403（对），但传 null → 200 且负责人被清空。
    if "owner_id" in data:
        if data["owner_id"] is None:
            # 订单**不允许没有负责人**：负责人是"谁跟进、谁看得见"的依据，
            # 清空之后原负责人自己都查不到这张单，等于把单据丢进黑洞。
            # 不打算换人就别传这个字段。
            raise AppError(
                ErrorCode.PARAM_ERROR,
                "订单必须有人负责，不能清空；要换人请传新的 owner_id",
                422,
            )
        # 转移负责人需要**独立授权**（P1）：这是归属类动作，能把单子划到任何人名下。
        # 日常 order:manage 不该自带这个能力——否则"能改单"就等于"能抢单"。
        if not user.has("order:assign"):
            raise AppError(
                ErrorCode.FORBIDDEN, "转移订单负责人需要「转移订单负责人」权限", 403
            )
        owner = await session.get(User, data["owner_id"])
        if owner is None:
            raise AppError(ErrorCode.NOT_FOUND, f"负责人 id={data['owner_id']} 不存在", 404)
        if owner.status != "active":
            raise AppError(
                ErrorCode.PARAM_ERROR, f"负责人「{owner.name}」已停用，不能接收订单", 422
            )

    for field, value in data.items():
        setattr(order, field, value)
    # 兜底：任何路径都不许把已有订单的负责人留空（数据库列可空，靠代码守住）
    if order.owner_id is None:
        raise AppError(
            ErrorCode.PARAM_ERROR, "订单必须有人负责，不能清空负责人", 422
        )
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="update",
        business_type="order",
        business_id=order.id,
        before=before,
        after=svc.serialize_order(order),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_order(order), "已保存")


@router.get("/orders/{order_id}/items")
async def list_order_items(
    order_id: int,
    user: CurrentUser = Depends(require_permission("order:view")),
    session: AsyncSession = Depends(get_db),
):
    await svc.get_visible_order(session, user, order_id)
    rows = (
        await session.execute(
            select(SalesOrderItem, Sku.sku_code)
            .outerjoin(Sku, Sku.id == SalesOrderItem.sku_id)
            .where(SalesOrderItem.order_id == order_id)
            .order_by(SalesOrderItem.id.asc())
        )
    ).all()
    return ok([svc.serialize_item(item, code) for item, code in rows])


@router.get("/orders/{order_id}/milestones")
async def list_milestones(
    order_id: int,
    user: CurrentUser = Depends(require_permission("order:view")),
    session: AsyncSession = Depends(get_db),
):
    """跟单里程碑（领导模块⑤）：首次访问建立六个空计划节点；计划必须经责任人确认。"""
    order = await svc.get_visible_order(session, user, order_id)
    rows = await milestones_svc.ensure_initialized(
        session, order.id, order.delivery_date, created_by=user.id
    )
    await session.commit()  # 初始化行要落库，否则下次访问会重复初始化
    today = today_business()
    items = [
        {
            "id": r.id,
            "node": r.node,
            "label": milestones_svc.node_label(r.node),
            "planned_date": r.planned_date,
            "actual_date": r.actual_date,
            "status": milestones_svc.row_status(r, today),
            "status_label": milestones_svc.STATUS_LABELS[
                milestones_svc.row_status(r, today)
            ],
            # 方案 :103：责任人 / 来源证据 / 逾期原因
            "owner_id": r.owner_id,
            "evidence": r.evidence,
            "overdue_reason": r.overdue_reason,
            "remark": r.remark,
            "skip_reason": r.skip_reason, "skipped_by": r.skipped_by, "skipped_at": r.skipped_at,
        }
        for r in rows
    ]
    return ok(items)


@router.patch("/orders/{order_id}/milestones/{milestone_id}")
async def update_milestone(
    order_id: int,
    milestone_id: int,
    payload: MilestoneUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("order:manage")),
    session: AsyncSession = Depends(get_db),
):
    """登记实际日期 / 调整计划日期 / 备注（exclude_unset：不传的字段不动）。"""
    order = await svc.get_visible_order(session, user, order_id)
    from app.modules.order.schedule import lock_order
    await lock_order(session, order)
    row = (await session.execute(select(OrderMilestone).where(OrderMilestone.id == milestone_id)
           .with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
    if row is None or row.order_id != order.id:
        raise AppError(ErrorCode.NOT_FOUND, "里程碑不存在", 404)
    data = payload.model_dump(exclude_unset=True)
    if "owner_id" in data and data["owner_id"] is not None:
        owner = await session.get(User, data["owner_id"])
        if owner is None:
            raise AppError(ErrorCode.NOT_FOUND, f"责任人 id={data['owner_id']} 不存在", 404)
        if owner.status != "active":
            raise AppError(
                ErrorCode.STATUS_NOT_ALLOWED,
                f"责任人「{owner.name}」已停用，请选择在职责任人",
                422,
            )
    # 动态批次节点（「第 N 批发货」）的计划日/实际日是**批次那一份事实的投影**，
    # 只能由批次的创建、交期变更、发货登记来改。这里放开就会两边各存一份日期：
    # 之后按期平移各走各的，越差越远；还会出现"节点显示已发货、批次仍待发"。
    if row.node.startswith(milestones_svc.BATCH_NODE_PREFIX):
        blocked = [field for field in ("planned_date", "actual_date") if field in data]
        if blocked:
            raise AppError(
                ErrorCode.STATUS_NOT_ALLOWED,
                "「第 N 批发货」节点的计划日/实际日跟随对应批次，"
                "请在批次上调整计划或登记发货，不要单独改这个节点",
                422,
            )
    # 固定节点里也有几个是**真实事实的投影**（issue #11 实测的反例：
    # 手填「首批发货」「收款」的实际日，节点显示完成，而同一张单实发 0、到账 0）。
    # 与动态批次节点同一条道理：一件事只能有一个来源。计划日仍可调。
    if row.node in milestones_svc.DERIVED_NODES and "actual_date" in data:
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"「{milestones_svc.node_label(row.node)}」的实际日期不能直接填 —— "
            f"{milestones_svc.DERIVED_NODES[row.node]}。"
            "（计划日期仍可调整；确属历史补录请在备注里写明依据）",
            422,
        )
    # 没有系统事实可派生的节点（签约、产前样）只能人工登记，
    # 但登记后要**标明是人工声明**，不能与已验证事实混成同一类。
    if (
        row.node in milestones_svc.MANUAL_NODES
        and data.get("actual_date") is not None
        and "evidence" not in data
    ):
        data["evidence"] = milestones_svc.MANUAL_EVIDENCE_TAG
    before = {
        "node": row.node,
        "planned_date": str(row.planned_date),
        "actual_date": str(row.actual_date),
        "owner_id": row.owner_id,
        "evidence": row.evidence,
        "overdue_reason": row.overdue_reason,
        "remark": row.remark,
        "skipped_at": str(row.skipped_at),
        "skip_reason": row.skip_reason,
        "skipped_by": row.skipped_by,
    }
    skipped = data.pop("skipped", None)
    if skipped is True:
        if row.actual_date or data.get("actual_date"):
            raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "已实际完成的节点不能标为不适用", 422)
        reason = (data.get("skip_reason") or "").strip()
        if not reason:
            raise AppError(ErrorCode.PARAM_ERROR, "跳过节点必须填写原因", 422)
        data["skip_reason"] = reason
        row.skip_reason = reason
        row.skipped_by = user.id
        row.skipped_at = datetime.now(UTC)
    elif skipped is False:
        row.skip_reason = None
        row.skipped_by = None
        row.skipped_at = None
        data.pop("skip_reason", None)
    elif "skip_reason" in data:
        raise AppError(ErrorCode.PARAM_ERROR, "修改跳过原因时请同时提交 skipped=true", 422)
    if row.skipped_at and data.get("actual_date"):
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "请先恢复适用，再登记实际完成日期", 422)
    for field, value in data.items():
        setattr(row, field, value)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="update",
        business_type="order_milestone",
        # 审计统一记订单 id（replan 也是订单 id）：按订单查"跟单改动史"才查得全
        business_id=order.id,
        before=before,
        after={
            "node": row.node,
            "planned_date": str(row.planned_date),
            "actual_date": str(row.actual_date),
            "owner_id": row.owner_id,
            "evidence": row.evidence,
            "overdue_reason": row.overdue_reason,
            "remark": row.remark,
            "skipped_at": str(row.skipped_at),
            "skip_reason": row.skip_reason,
            "skipped_by": row.skipped_by,
        },
        ip=client_ip(request),
    )
    await session.commit()
    return ok(
        {
            "id": row.id,
            "node": row.node,
            "label": milestones_svc.node_label(row.node),
            "planned_date": row.planned_date,
            "actual_date": row.actual_date,
            "status": milestones_svc.row_status(row, today_business()),
            "status_label": milestones_svc.STATUS_LABELS[
                milestones_svc.row_status(row, today_business())
            ],
            "owner_id": row.owner_id,
            "evidence": row.evidence,
            "overdue_reason": row.overdue_reason,
            "remark": row.remark,
            "skip_reason": row.skip_reason, "skipped_by": row.skipped_by, "skipped_at": row.skipped_at,
        },
        "里程碑已更新",
    )


@router.post("/orders/{order_id}/schedule-changes/preview")
async def preview_schedule_change(
    order_id: int,
    payload: ScheduleChangeCreate,
    user: CurrentUser = Depends(require_permission("order:view")),
    session: AsyncSession = Depends(get_db),
):
    """改交期前先看"会动到谁"（方案 :105「展示受影响节点及批次」）。只算不改。"""
    from app.modules.order import schedule as schedule_svc

    order = await svc.get_visible_order(session, user, order_id)
    return ok(await schedule_svc.preview(session, order, payload.new_delivery_date,
        delivery_kind=payload.delivery_kind, transit_days=payload.transit_days, plan_offsets=payload.plan_offsets))


@router.post("/orders/{order_id}/schedule-changes")
async def create_schedule_change(
    order_id: int,
    payload: ScheduleChangeCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("order:manage")),
    session: AsyncSession = Depends(get_db),
):
    """生成交期变更单（待责任人确认）。**未确认前不动任何计划日期。**"""
    from app.modules.order import schedule as schedule_svc

    order = await svc.get_visible_order(session, user, order_id)
    row = await schedule_svc.create_change(
        session, order, user=user,
        new_delivery_date=payload.new_delivery_date, reason=payload.reason,
        delivery_kind=payload.delivery_kind, transit_days=payload.transit_days, plan_offsets=payload.plan_offsets,
    )
    await write_audit(
        session, operator_id=user.id, action="create_schedule_change",
        business_type="order", business_id=order.id,
        after={"change_id": row.id, "new_delivery_date": str(row.new_delivery_date)},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(
        schedule_svc.serialize_change(row),
        "已生成交期变更单，待责任人确认后才重排计划",
    )


@router.get("/orders/{order_id}/schedule-changes")
async def list_schedule_changes(
    order_id: int,
    user: CurrentUser = Depends(require_permission("order:view")),
    session: AsyncSession = Depends(get_db),
):
    """变更历史：每张单都留着当时的前后版本对比。"""
    from app.modules.order import schedule as schedule_svc
    from app.modules.order.model import OrderScheduleChange

    order = await svc.get_visible_order(session, user, order_id)
    rows = list(
        (
            await session.execute(
                select(OrderScheduleChange)
                .where(OrderScheduleChange.order_id == order.id)
                .order_by(OrderScheduleChange.id.desc())
            )
        ).scalars().all()
    )
    return ok([schedule_svc.serialize_change(r) for r in rows])


@router.post("/orders/{order_id}/schedule-changes/{change_id}/confirm")
async def confirm_schedule_change(
    order_id: int,
    change_id: int,
    payload: ScheduleChangeConfirm,
    request: Request,
    user: CurrentUser = Depends(require_permission("order:manage")),
    session: AsyncSession = Depends(get_db),
):
    """责任人确认：这一刻才真正改交期、重排节点与批次计划日。"""
    from app.modules.order import schedule as schedule_svc
    from app.modules.order.model import OrderScheduleChange

    order = await svc.get_visible_order(session, user, order_id)
    row = await session.get(OrderScheduleChange, change_id)
    if row is None or row.order_id != order.id:
        raise AppError(ErrorCode.NOT_FOUND, "交期变更单不存在", 404)
    # 「责任人确认」要真的是责任人：只查 order:manage + 可见性的话，
    # 任何能改订单的同事都能替别人确认，这个动作就成了摆设。
    # 例外是主管（order:assign 是归属类独立授权）：交接/离职后需要有人收口。
    if row.owner_id and user.id != row.owner_id and not user.has("order:assign"):
        raise AppError(
            ErrorCode.FORBIDDEN,
            "该交期变更单应由责任人确认（当前记录的责任人不是您）；"
            "若已交接，请让有「转移订单负责人」权限的主管确认",
            403,
        )
    row = await schedule_svc.confirm_change(
        session, order, row, user=user, remark=payload.remark
    )
    applied = (row.affected or {}).get("applied", {})
    await followup_service.record_and_notify(
        session, customer_id=order.customer_id, operator_id=user.id, owner_id=order.owner_id,
        title="确认交期变更",
        content=f"订单 {order.order_no} 交期变更 #{row.id} 已确认："
                f"客户要求日期 {applied.get('old_delivery_date') or '未设置'} → {row.new_delivery_date}"
                f"；类型：{'到货日' if order.delivery_kind == 'arrival' else '发货日'}"
                f"；运输 {order.transit_days} 天；计划发货日 {applied.get('new_shipment_date')}"
                + (f"；原因：{row.reason}" if row.reason else ""),
        business_type="order", business_id=order.id, order_id=order.id,
        event_key=f"order:schedule_confirm:{row.id}",
    )
    await write_audit(
        session, operator_id=user.id, action="confirm_schedule_change",
        business_type="order", business_id=order.id,
        after={"change_id": row.id, "delivery_date": str(order.delivery_date)},
        ip=client_ip(request),
    )
    await session.commit()
    await notification_service.dispatch_pending(session)
    return ok(schedule_svc.serialize_change(row), "已确认，节点与批次计划日已重排")


@router.post("/orders/{order_id}/schedule-changes/{change_id}/cancel")
async def cancel_schedule_change(
    order_id: int,
    change_id: int,
    payload: ScheduleChangeCancel,
    request: Request,
    user: CurrentUser = Depends(require_permission("order:manage")),
    session: AsyncSession = Depends(get_db),
):
    """作废待确认的交期变更单。

    没有这条出口，库级"一单只允许一张 pending"的部分唯一索引会把该订单之后的
    交期变更**永久堵死**。权限与确认一致：责任人本人，或有 order:assign 的主管。
    """
    from app.modules.order import schedule as schedule_svc
    from app.modules.order.model import OrderScheduleChange

    order = await svc.get_visible_order(session, user, order_id)
    row = await session.get(OrderScheduleChange, change_id)
    if row is None or row.order_id != order.id:
        raise AppError(ErrorCode.NOT_FOUND, "交期变更单不存在", 404)
    if row.owner_id and user.id != row.owner_id and not user.has("order:assign"):
        raise AppError(
            ErrorCode.FORBIDDEN,
            "该交期变更单应由责任人处理；若已交接，请让有「转移订单负责人」权限的主管处理",
            403,
        )
    row = await schedule_svc.cancel_change(
        session, order, row, user=user, reason=payload.reason
    )
    await write_audit(
        session, operator_id=user.id, action="cancel_schedule_change",
        business_type="order", business_id=order.id,
        after={"change_id": row.id, "reason": row.cancel_reason},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(schedule_svc.serialize_change(row), "已作废，该订单可以重新发起交期变更")


@router.post("/orders/{order_id}/milestones/replan")
async def replan_milestones(
    order_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("order:manage")),
    session: AsyncSession = Depends(get_db),
):
    """旧重排入口已停用，统一使用预览及责任人确认。"""
    await svc.get_visible_order(session, user, order_id)
    raise AppError(ErrorCode.STATUS_NOT_ALLOWED,
                   "请使用交期与计划预览，生成待确认记录后由责任人确认生效", 422)


@router.get("/orders/{order_id}/status-history")
async def status_history(
    order_id: int,
    user: CurrentUser = Depends(require_permission("order:view")),
    session: AsyncSession = Depends(get_db),
):
    # 先校验订单在数据范围内（实测确认此前能直接读别人的履约变更历史）
    await svc.get_visible_order(session, user, order_id)
    rows = (
        await session.execute(
            select(OrderStatusHistory, User.name)
            .outerjoin(User, User.id == OrderStatusHistory.operator_id)
            .where(OrderStatusHistory.order_id == order_id)
            .order_by(OrderStatusHistory.id.asc())
        )
    ).all()
    return ok(
        [
            {
                "id": row.id,
                "old_status": row.old_status,
                "old_status_label": ORDER_STATUS_LABEL.get(row.old_status or "", row.old_status),
                "new_status": row.new_status,
                "new_status_label": ORDER_STATUS_LABEL.get(row.new_status, row.new_status),
                "source": row.source,
                "operator_name": name,
                "remark": row.remark,
                "created_at": row.created_at,
            }
            for row, name in rows
        ]
    )


@router.post("/orders/{order_id}/status")
async def change_status(
    order_id: int,
    payload: OrderStatusChange,
    request: Request,
    user: CurrentUser = Depends(require_permission("order:manage")),
    session: AsyncSession = Depends(get_db),
):
    """更新订单的**履约**状态（待生产 / 生产中 / 已发货 / 已签收 / 已完成）。

    ⚠️ **不接受 `cancelled`**（2026-10-06 收口）：把状态改成"已取消"看起来只是
    改一个字段，但取消订单要连带处理钱和账 —— 专用接口 `POST /orders/{id}/cancel`
    会做四件事：已确认回款直接拒绝取消、待确认回款随单驳回、未回清的应收计划
    置为 `cancelled` 以停止催收、未发货批次随单取消。

    之前前端把这个下拉里也放了"已取消"，等于告诉用户"这也是一种状态"，
    点下去却只改了状态位：订单显示已取消，**催收和逾期提醒照旧发**，
    已收到钱的订单也能取消。所以这里从服务端也堵一道 —— 换个入口也绕不过去，
    规则只认一个出口。
    """
    if payload.status == "cancelled":
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            "取消订单会连带作废应收计划、驳回待确认回款，请走「取消订单」"
            "（POST /orders/{id}/cancel），不要用更新履约状态代替",
            422,
        )
    # 状态切换先锁整单：与"登记发货"并发时，两边都必须串行化，
    # 否则同一时刻可能既取消成功又发货成功（第一批返修 §3.5）。
    order = await svc.get_visible_order(session, user, order_id, for_update=True)
    await svc.change_status(
        session, order, new_status=payload.status, operator_id=user.id, remark=payload.remark
    )
    await write_audit(
        session,
        operator_id=user.id,
        action="change_status",
        business_type="order",
        business_id=order.id,
        after={"status": payload.status},
        ip=client_ip(request),
    )
    await session.commit()
    # 文案带上订单号：这句话是全局浮层，用户切到别的页面还会挂几秒，
    # 只写"已更新为「已发货」"根本对不上是哪个订单。
    return ok(
        svc.serialize_order(order),
        f"{order.order_no} 的履约状态已更新为「{ORDER_STATUS_LABEL.get(payload.status)}」",
    )


@router.post("/orders/{order_id}/cancel")
async def cancel_order(
    order_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("order:manage")),
    session: AsyncSession = Depends(get_db),
):
    """取消订单（方案 §5：取消规则显式化，默认口径如下——要改先改这里）。

    1. 已有**已确认**回款 → 拒绝取消：钱不能随订单静默作废，先人工处理回款；
    2. 待确认回款 → 随订单一并驳回（登记原因）；
    3. 未回清的应收计划 → 状态置 `cancelled`，不再派催收/逾期提醒；
    4. 商机成交状态**不自动回退**：成交是已发生的商业事实，撤销成交走
       `POST /opportunities/{id}/lose`（先失单再重建）由人工评估——
       这条默认口径如与业务不符，改这里并在方案 §8 补一条 D 决策。
    """
    # 取消是不可逆的终态动作，先锁整单：与"登记发货"并发时只能有一个成功
    # （第一批返修 §3.5：「同一时刻不能既取消又发货成功」）。
    order = await svc.get_visible_order(session, user, order_id, for_update=True)
    before_status = order.status

    from app.modules.payment.model import PaymentRecord, ReceivablePlan

    plans = (
        await session.execute(
            select(ReceivablePlan).where(ReceivablePlan.order_id == order.id)
        )
    ).scalars().all()
    plan_ids = [plan.id for plan in plans]

    confirmed = 0
    if plan_ids:
        confirmed = (
            await session.execute(
                select(func.count())
                .select_from(PaymentRecord)
                .where(
                    PaymentRecord.receivable_plan_id.in_(plan_ids),
                    PaymentRecord.status == "confirmed",
                )
            )
        ).scalar_one()
    if confirmed:
        raise AppError(
            ErrorCode.STATUS_NOT_ALLOWED,
            f"该订单有 {confirmed} 笔已确认回款，不能直接取消；请先人工处理回款",
            422,
        )

    # 待确认回款随订单驳回
    if plan_ids:
        pending_payments = (
            await session.execute(
                select(PaymentRecord).where(
                    PaymentRecord.receivable_plan_id.in_(plan_ids),
                    PaymentRecord.status == "pending",
                )
            )
        ).scalars().all()
        for payment in pending_payments:
            payment.status = "rejected"
            payment.voucher_note = "订单取消，随单驳回" + (
                f"（原备注：{payment.voucher_note}）" if payment.voucher_note else ""
            )
        # 未回清的应收计划置为已取消
        for plan in plans:
            if plan.status != "paid":
                plan.status = "cancelled"

    await svc.change_status(session, order, new_status="cancelled", operator_id=user.id)
    # 未发货的批次随单取消；已发货批次是既成事实，保留原状
    planned_batches = (
        await session.execute(
            select(OrderShipmentBatch).where(
                OrderShipmentBatch.order_id == order.id,
                OrderShipmentBatch.status == "planned",
            )
        )
    ).scalars().all()
    for batch in planned_batches:
        batch.status = "cancelled"
    await write_audit(
        session,
        operator_id=user.id,
        action="cancel",
        business_type="order",
        business_id=order.id,
        before={"status": before_status},
        after={
            "status": "cancelled",
            "rejected_pending_payments": len(pending_payments) if plan_ids else 0,
            "cancelled_receivable_plans": sum(1 for p in plans if p.status == "cancelled"),
            "opportunity_id": order.opportunity_id,
            "note": "商机成交状态未自动回退，如需撤销请人工评估",
        },
        ip=client_ip(request),
    )
    await session.commit()
    # 文案里原来写着"**回款未受影响**"—— 那句话是错的，而且把缺陷说成了设计意图：
    # 实际语义是「**已在途/已确认的回款不动**」（不删记录、不回退状态），
    # 但取消之后**不能再新登记、也不能再确认**（C5-01，2026-10-10 修）。
    # 两件事必须分开说，否则下一轮审查还会把它当"设计如此"放过。
    return ok(
        svc.serialize_order(order),
        "订单已取消（应收计划已同步取消；已有的回款记录保留，"
        "但不能再新登记或确认回款）",
    )


@router.post("/orders/{order_id}/sync-erp")
async def sync_erp(
    order_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("order:manage")),
    session: AsyncSession = Depends(get_db),
):
    """推送到 ERP/MES（订单详情页的「推送 ERP」按钮走这里）。

    原来这里只写一条 skipped 日志、返回"对方系统未接入"。现在接到真正的
    `erp` 模块：配置齐了就真推，没配就返回 50203 并说明缺哪个变量，
    两种情况都**不会**把订单标成已推送。
    实现见 `app/modules/erp/service.py`，调用方无感。
    """
    order = await svc.get_visible_order(session, user, order_id)
    try:
        result = await erp_service.push_order(session, order=order, operator_id=user.id)
    except (ErpNotConfigured, ErpError) as error:
        await session.rollback()
        raise erp_translate(error) from error
    await write_audit(
        session,
        operator_id=user.id,
        action="sync_erp",
        business_type="order",
        business_id=order.id,
        after={"pushed": result["pushed"], "erp_order_id": result.get("erp_order_id")},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(result, result["message"])


@router.post("/orders/{order_id}/repurchase")
async def repurchase(
    order_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("opportunity:manage")),
    session: AsyncSession = Depends(get_db),
):
    """复购：以老订单的明细为基础，直接开一个新商机（不重建客户）。"""
    order = await svc.get_visible_order(session, user, order_id)
    stage = await get_first_stage(session)
    opportunity = Opportunity(
        customer_id=order.customer_id,
        title=f"{order.order_no} 复购",
        source="复购",
        stage_id=stage.id,
        owner_id=order.owner_id or user.id,
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
            remark="由历史订单复购生成",
            entered_at=datetime.now(UTC),
        )
    )
    items = (
        await session.execute(
            select(SalesOrderItem).where(SalesOrderItem.order_id == order.id)
        )
    ).scalars().all()
    copied_items = 0
    skipped_custom = 0
    for item in items:
        # 定制件（无 SKU）不能复购进商机明细：opportunity_items.sku_id 是 NOT NULL，
        # 写 None 直接 IntegrityError → 定制成交单永远复购不了。商机明细是"可复用
        # 的商品"，本来也承载不了无 SKU 的定制行；跳过并计数，别整单失败。
        if item.sku_id is None:
            skipped_custom += 1
            continue
        session.add(
            OpportunityItem(
                opportunity_id=opportunity.id,
                sku_id=item.sku_id,
                quantity=item.quantity,
                target_price=item.unit_price,
                specification=item.specification,
                destination=None,
                remark="复购带入",
            )
        )
        copied_items += 1
    await write_audit(
        session,
        operator_id=user.id,
        action="repurchase",
        business_type="order",
        business_id=order.id,
        after={
            "new_opportunity_id": opportunity.id,
            "copied_items": copied_items,
            "skipped_custom_items": skipped_custom,
        },
        ip=client_ip(request),
    )
    await session.commit()
    return ok({"opportunity_id": opportunity.id}, "已生成复购商机")


@router.get("/orders/{order_id}/receivables")
async def list_receivables(
    order_id: int,
    user: CurrentUser = Depends(require_permission("payment:view")),
    session: AsyncSession = Depends(get_db),
):
    from app.modules.payment import service as payment_service

    await svc.get_visible_order(session, user, order_id)
    rows = (
        await session.execute(
            select(ReceivablePlan)
            .where(ReceivablePlan.order_id == order_id)
            .order_by(ReceivablePlan.due_date.asc())
        )
    ).scalars().all()
    return ok([await payment_service.serialize_plan(session, plan) for plan in rows])


@router.get("/orders/{order_id}/payments")
async def list_order_payments(
    order_id: int,
    user: CurrentUser = Depends(require_permission("payment:view")),
    session: AsyncSession = Depends(get_db),
):
    from app.modules.payment import service as payment_service

    await svc.get_visible_order(session, user, order_id)
    rows = (
        await session.execute(
            select(PaymentRecord)
            .where(PaymentRecord.order_id == order_id)
            .order_by(PaymentRecord.received_date.desc())
        )
    ).scalars().all()
    return ok([await payment_service.serialize_payment(session, row) for row in rows])


# ---- 发货批次（§3.5/场景13：分批发货，首批不结束整单）----


@router.get("/orders/{order_id}/shipments")
async def list_shipments(
    order_id: int,
    user: CurrentUser = Depends(require_permission("order:view")),
    session: AsyncSession = Depends(get_db),
):
    """批次与未发量：跟单看承诺/事实分开的数字。"""
    order = await svc.get_visible_order(session, user, order_id)
    return ok(await svc.order_shipments(session, order))


@router.post("/orders/{order_id}/shipments")
async def create_shipment(
    order_id: int,
    payload: ShipmentBatchCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("order:manage")),
    session: AsyncSession = Depends(get_db),
):
    order = await svc.get_visible_order(session, user, order_id)
    if order.status == "cancelled":
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "已取消的订单不能再排发货批次")
    batch = await svc.create_shipment_batch(session, order, payload=payload, user_id=user.id)
    await write_audit(
        session,
        operator_id=user.id,
        action="create_shipment_batch",
        business_type="order",
        business_id=order.id,
        after={"batch_id": batch.id, "batch_no": batch.batch_no,
               "items": [{"order_item_id": i.order_item_id, "planned_qty": str(i.planned_qty)} for i in payload.items]},
        ip=client_ip(request),
    )
    await session.commit()
    return ok({"batch_id": batch.id, "batch_no": batch.batch_no}, "发货批次已排")


@router.post("/orders/{order_id}/shipments/{batch_id}/ship")
async def ship_batch(
    order_id: int,
    batch_id: int,
    payload: ShipmentBatchShip,
    request: Request,
    user: CurrentUser = Depends(require_permission("order:manage")),
    session: AsyncSession = Depends(get_db),
):
    """登记实发。只推进订单到"已发货"——整单完成由未发量闸门把关（场景13）。"""
    order = await svc.get_visible_order(session, user, order_id)
    batch = await session.get(OrderShipmentBatch, batch_id)
    if batch is None:
        raise AppError(ErrorCode.NOT_FOUND, "发货批次不存在", 404)
    await svc.ship_shipment_batch(session, order, batch, payload=payload, operator_id=user.id)
    await write_audit(
        session,
        operator_id=user.id,
        action="ship_batch",
        business_type="order",
        business_id=order.id,
        after={"batch_id": batch.id, "batch_no": batch.batch_no,
               "actual_ship_date": str(batch.actual_ship_date),
               "tracking_no": batch.tracking_no},
        ip=client_ip(request),
    )
    await session.commit()
    await notification_service.dispatch_pending(session)
    overview = await svc.order_shipments(session, order)
    return ok(
        {"order_status": order.status, "summary": overview["summary"]},
        f"第 {batch.batch_no} 批已登记发货",
    )


@router.delete("/orders/{order_id}/shipments/{batch_id}")
async def delete_shipment(
    order_id: int,
    batch_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("order:manage")),
    session: AsyncSession = Depends(get_db),
):
    order = await svc.get_visible_order(session, user, order_id)
    batch = await session.get(OrderShipmentBatch, batch_id)
    if batch is None or batch.order_id != order.id:
        raise AppError(ErrorCode.NOT_FOUND, "发货批次不存在", 404)
    await svc.cancel_shipment_batch(session, batch)
    await write_audit(
        session,
        operator_id=user.id,
        action="cancel_shipment_batch",
        business_type="order",
        business_id=order.id,
        after={"batch_id": batch.id, "batch_no": batch.batch_no},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(None, "批次已取消")
