"""订单中心接口（对齐 03-API §27 / §28）。"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.data_scope import scoped_owner_ids
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok, page_data, paginate
from app.modules.erp import service as erp_service
from app.modules.erp.adapter import ErpError, ErpNotConfigured
from app.modules.erp.router import translate_erp_error as erp_translate
from app.modules.opportunity.model import Opportunity, OpportunityItem, OpportunityStageHistory
from app.modules.opportunity.service import get_first_stage
from app.modules.order import service as svc
from app.modules.order.model import ORDER_STATUS_LABEL, OrderStatusHistory, SalesOrder, SalesOrderItem
from app.modules.order.schema import OrderFromQuote, OrderStatusChange, OrderUpdate
from app.modules.payment.model import PaymentRecord, ReceivablePlan
from app.modules.product.model import Sku
from app.modules.quote.model import QuoteVersion
from app.modules.user.model import User

router = APIRouter(tags=["Order"])


@router.get("/orders")
async def list_orders(
    keyword: str | None = None,
    status: str | None = None,
    customer_id: int | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user: CurrentUser = Depends(require_permission("order:view")),
    session: AsyncSession = Depends(get_db),
):
    stmt = select(SalesOrder)
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
            received_amount=ctx["received"].get(order.id, 0),
            item_count=ctx["counts"].get(order.id, 0),
        )
        for order in rows
    ]
    return ok(page_data(items, total, page, page_size))


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
    return ok({"order_id": order.id, "order_no": order.order_no}, "已生成销售订单")


@router.get("/orders/{order_id}")
async def get_order(
    order_id: int,
    _: CurrentUser = Depends(require_permission("order:view")),
    session: AsyncSession = Depends(get_db),
):
    order = await svc.get_order_or_404(session, order_id)
    ctx = await svc.order_context(session, [order])
    return ok(
        svc.serialize_order(
            order,
            customer_name=ctx["customers"].get(order.customer_id),
            owner_name=ctx["owners"].get(order.owner_id) if order.owner_id else None,
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
    order = await svc.get_order_or_404(session, order_id)
    before = svc.serialize_order(order)

    data = payload.model_dump(exclude_unset=True)
    # 换负责人要校验目标存在且在职，否则订单会挂到一个空负责人上
    if data.get("owner_id") is not None:
        owner = await session.get(User, data["owner_id"])
        if owner is None:
            raise AppError(ErrorCode.NOT_FOUND, f"负责人 id={data['owner_id']} 不存在", 404)
        if owner.status != "active":
            raise AppError(
                ErrorCode.PARAM_ERROR, f"负责人「{owner.name}」已停用，不能接收订单", 422
            )

    for field, value in data.items():
        setattr(order, field, value)
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
    _: CurrentUser = Depends(require_permission("order:view")),
    session: AsyncSession = Depends(get_db),
):
    await svc.get_order_or_404(session, order_id)
    rows = (
        await session.execute(
            select(SalesOrderItem, Sku.sku_code)
            .outerjoin(Sku, Sku.id == SalesOrderItem.sku_id)
            .where(SalesOrderItem.order_id == order_id)
            .order_by(SalesOrderItem.id.asc())
        )
    ).all()
    return ok([svc.serialize_item(item, code) for item, code in rows])


@router.get("/orders/{order_id}/status-history")
async def status_history(
    order_id: int,
    _: CurrentUser = Depends(require_permission("order:view")),
    session: AsyncSession = Depends(get_db),
):
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
    order = await svc.get_order_or_404(session, order_id)
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
    return ok(svc.serialize_order(order), f"已更新为「{ORDER_STATUS_LABEL.get(payload.status)}」")


@router.post("/orders/{order_id}/cancel")
async def cancel_order(
    order_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("order:manage")),
    session: AsyncSession = Depends(get_db),
):
    order = await svc.get_order_or_404(session, order_id)
    before_status = order.status
    await svc.change_status(session, order, new_status="cancelled", operator_id=user.id)
    await write_audit(
        session,
        operator_id=user.id,
        action="cancel",
        business_type="order",
        business_id=order.id,
        before={"status": before_status},
        after={"status": "cancelled"},
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_order(order), "订单已取消")


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
    order = await svc.get_order_or_404(session, order_id)
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
    order = await svc.get_order_or_404(session, order_id)
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
    for item in items:
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
    await write_audit(
        session,
        operator_id=user.id,
        action="repurchase",
        business_type="order",
        business_id=order.id,
        after={"new_opportunity_id": opportunity.id, "copied_items": len(items)},
        ip=client_ip(request),
    )
    await session.commit()
    return ok({"opportunity_id": opportunity.id}, "已生成复购商机")


@router.get("/orders/{order_id}/receivables")
async def list_receivables(
    order_id: int,
    _: CurrentUser = Depends(require_permission("payment:view")),
    session: AsyncSession = Depends(get_db),
):
    from app.modules.payment import service as payment_service

    await svc.get_order_or_404(session, order_id)
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
    _: CurrentUser = Depends(require_permission("payment:view")),
    session: AsyncSession = Depends(get_db),
):
    from app.modules.payment import service as payment_service

    rows = (
        await session.execute(
            select(PaymentRecord)
            .where(PaymentRecord.order_id == order_id)
            .order_by(PaymentRecord.received_date.desc())
        )
    ).scalars().all()
    return ok([await payment_service.serialize_payment(session, row) for row in rows])
