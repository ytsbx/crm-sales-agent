"销售退货接口：登记、取消、填退款金额、退款登记、作废。"

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.response import ok
from app.modules.order.service import get_visible_order
from app.modules.returns import service as svc
from app.modules.returns.schema import (
    ReturnCancel,
    ReturnCreate,
    ReturnRefund,
    ReturnReview,
    ReturnSettle,
    ReturnVoid,
)

router = APIRouter(tags=["销售退货"])


async def _lock_return_with_order(session, user, return_id: int):
    """统一锁序：先订单、后退货单。"""
    row = await svc.get_visible_return(session, user, return_id)
    await get_visible_order(session, user, row.order_id, for_update=True)
    return await svc.get_visible_return(session, user, return_id, for_update=True)


@router.get("/orders/{order_id}/returns")
async def list_order_returns(
    order_id: int,
    user: CurrentUser = Depends(require_permission("order:view")),
    session: AsyncSession = Depends(get_db),
):
    await get_visible_order(session, user, order_id)
    return ok(await svc.list_returns_for_order(session, order_id))


@router.get("/orders/{order_id}/returnable")
async def returnable(
    order_id: int,
    user: CurrentUser = Depends(require_permission("order:view")),
    session: AsyncSession = Depends(get_db),
):
    await get_visible_order(session, user, order_id)
    return ok(await svc.returnable_overview(session, order_id))


@router.post("/orders/{order_id}/returns")
async def create_return(
    order_id: int,
    payload: ReturnCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("order:manage")),
    session: AsyncSession = Depends(get_db),
):
    order = await get_visible_order(session, user, order_id, for_update=True)
    row = await svc.create_return(
        session, user, order, payload, ip=client_ip(request)
    )
    data = await svc.serialize_return(session, row)
    await session.commit()
    return ok(data, f"退货单 {row.return_no} 已登记，等待主管复核")


@router.get("/returns")
async def list_returns(
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=200),
    user: CurrentUser = Depends(require_permission("order:view", "payment:view")),
    session: AsyncSession = Depends(get_db),
):
    return ok(await svc.list_returns(session, user, status=status, limit=limit))


@router.get("/returns/{return_id}")
async def get_return(
    return_id: int,
    user: CurrentUser = Depends(require_permission("order:view", "payment:view")),
    session: AsyncSession = Depends(get_db),
):
    row = await svc.get_visible_return(session, user, return_id)
    return ok(await svc.serialize_return(session, row))


@router.post("/returns/{return_id}/review")
async def review_return(
    return_id: int,
    payload: ReturnReview,
    request: Request,
    user: CurrentUser = Depends(require_permission("order:assign")),
    session: AsyncSession = Depends(get_db),
):
    """主管复核退货：`order:assign` 是主管级权限，财务/业务员没有。"""
    row = await _lock_return_with_order(session, user, return_id)
    await svc.review_return(
        session,
        user,
        row,
        decision=payload.decision,
        note=payload.note,
        ip=client_ip(request),
    )
    data = await svc.serialize_return(session, row)
    await session.commit()
    message = (
        f"退货单 {row.return_no} 已通过复核，等待填退款金额"
        if row.status == "pending"
        else f"退货单 {row.return_no} 已驳回，可退数量已释放"
    )
    return ok(data, message)


@router.post("/returns/{return_id}/cancel")
async def cancel_return(
    return_id: int,
    payload: ReturnCancel,
    request: Request,
    user: CurrentUser = Depends(require_permission("order:manage")),
    session: AsyncSession = Depends(get_db),
):
    row = await _lock_return_with_order(session, user, return_id)
    await svc.cancel_return(
        session, user, row, payload.reason, ip=client_ip(request)
    )
    data = await svc.serialize_return(session, row)
    await session.commit()
    return ok(data, f"退货单 {row.return_no} 已取消")


@router.post("/returns/{return_id}/settle")
async def settle_return(
    return_id: int,
    payload: ReturnSettle,
    request: Request,
    user: CurrentUser = Depends(require_permission("order:manage", "payment:manage")),
    session: AsyncSession = Depends(get_db),
):
    row = await _lock_return_with_order(session, user, return_id)
    await svc.settle_return(
        session,
        user,
        row,
        settled_goods_amount=payload.settled_goods_amount,
        freight_refund_amount=payload.freight_refund_amount,
        settlement_note=payload.settlement_note,
        ip=client_ip(request),
    )
    data = await svc.serialize_return(session, row)
    await session.commit()
    message = (
        f"退货单 {row.return_no} 已确认退款总额 {row.settled_goods_amount:.2f} 元，"
        f"冲抵客户未付款 {row.offset_amount:.2f} 元"
        + (f"，退还客户 {row.refund_amount:.2f} 元" if row.refund_amount > 0 else "，无需向客户退款")
    )
    return ok(data, message)


@router.post("/returns/{return_id}/refund")
async def refund_return(
    return_id: int,
    payload: ReturnRefund,
    request: Request,
    user: CurrentUser = Depends(require_permission("payment:manage")),
    session: AsyncSession = Depends(get_db),
):
    row = await _lock_return_with_order(session, user, return_id)
    await svc.refund_return(
        session, user, row, payload.refund_reference, ip=client_ip(request)
    )
    data = await svc.serialize_return(session, row)
    await session.commit()
    return ok(data, f"退货单 {row.return_no} 已登记退款")


@router.post("/returns/{return_id}/void")
async def void_return(
    return_id: int,
    payload: ReturnVoid,
    request: Request,
    user: CurrentUser = Depends(require_permission("payment:manage")),
    session: AsyncSession = Depends(get_db),
):
    row = await _lock_return_with_order(session, user, return_id)
    await svc.void_return(
        session, user, row, payload.reason, ip=client_ip(request)
    )
    data = await svc.serialize_return(session, row)
    await session.commit()
    return ok(data, f"退货单 {row.return_no} 已作废，原冲抵的客户未付款已恢复")
