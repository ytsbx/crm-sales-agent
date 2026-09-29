"""ERP/MES 集成接口（03-API §28，6 个接口）。

  POST /integrations/erp/orders                  按订单号推送（外部系统调用入口）
  POST /integrations/erp/orders/{id}/sync        按 CRM 订单 id 推送
  GET  /integrations/erp/orders/{id}/status      拉取并回写履约状态
  GET  /integrations/erp/sync-logs               集成日志
  POST /webhooks/erp/order-status                订单状态回调（无需登录）
  POST /webhooks/erp/shipment-status             发货状态回调（无需登录）

额外加了 GET /integrations/erp/readiness：配置缺什么、已推送几张单。

与企微同一套原则：未配置时返回 50203 并说明缺哪个变量，
**不把订单标成已推送**（否则 ERP 里没有单、CRM 却显示已同步）。
"""

import secrets

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.database import get_db
from app.core.deps import CurrentUser, require_permission
from app.core.data_scope import ensure_in_scope
from app.core.errors import AppError, ErrorCode
from app.core.response import ok, page_data
from app.modules.erp import service as svc
from app.modules.erp.adapter import ErpError, ErpNotConfigured
from app.modules.order import service as order_service
from app.modules.order.model import SalesOrder
from app.modules.erp.schema import OrderPushRequest, StatusWebhookRequest

router = APIRouter(tags=["ERP"])


def _translate(error: Exception) -> AppError:
    if isinstance(error, ErpNotConfigured):
        return AppError(ErrorCode.ERP_SYNC_FAILED, f"{error}；配置位置：backend/.env", 422)
    if isinstance(error, ErpError):
        return AppError(ErrorCode.ERP_SYNC_FAILED, str(error), 502)
    return AppError(ErrorCode.ERP_SYNC_FAILED, f"ERP/MES 同步失败：{error}", 502)


def translate_erp_error(error: Exception) -> AppError:
    """公开的异常翻译，供 order 模块的 `/orders/{id}/sync-erp` 复用。"""
    return _translate(error)


@router.get("/integrations/erp/readiness")
async def erp_readiness(
    user: CurrentUser = Depends(require_permission("order:view")),
    session: AsyncSession = Depends(get_db),
):
    return ok(await svc.readiness(session))


@router.post("/integrations/erp/orders")
async def push_by_order_no(
    payload: OrderPushRequest,
    user: CurrentUser = Depends(require_permission("order:manage")),
    session: AsyncSession = Depends(get_db),
):
    """按 CRM 订单号推送。给外部系统或批量脚本调用，等价于 /orders/{id}/sync。"""
    if not payload.order_no:
        raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "order_no 必填", 422)
    order = (
        await session.execute(
            select(SalesOrder).where(SalesOrder.order_no == payload.order_no.strip())
        )
    ).scalars().first()
    if order is None:
        raise AppError(ErrorCode.NOT_FOUND, f"订单 {payload.order_no} 不存在", 404)
    # 按单号推送也要过数据范围，否则知道单号就能推别人的订单
    await ensure_in_scope(session, user, owner_id=order.owner_id, label="订单")
    try:
        result = await svc.push_order(session, order=order, operator_id=user.id)
    except (ErpNotConfigured, ErpError) as error:
        await session.rollback()
        raise _translate(error) from error
    await write_audit(
        session,
        operator_id=user.id,
        action="erp_push_order",
        business_type="order",
        business_id=order.id,
        after={"pushed": result["pushed"], "erp_order_id": result.get("erp_order_id")},
        ip=None,
    )
    await session.commit()
    return ok(result, result["message"])


@router.post("/integrations/erp/orders/{order_id}/sync")
async def sync_order(
    order_id: int,
    user: CurrentUser = Depends(require_permission("order:manage")),
    session: AsyncSession = Depends(get_db),
):
    """把订单推送到 ERP/MES（幂等：推过的直接返回，不重复建单）。"""
    order = await order_service.get_visible_order(session, user, order_id)
    try:
        result = await svc.push_order(session, order=order, operator_id=user.id)
    except (ErpNotConfigured, ErpError) as error:
        await session.rollback()
        raise _translate(error) from error
    await write_audit(
        session,
        operator_id=user.id,
        action="erp_push_order",
        business_type="order",
        business_id=order.id,
        after={"pushed": result["pushed"], "erp_order_id": result.get("erp_order_id")},
        ip=None,
    )
    await session.commit()
    return ok(result, result["message"])


@router.get("/integrations/erp/orders/{order_id}/status")
async def order_status(
    order_id: int,
    user: CurrentUser = Depends(require_permission("order:view")),
    session: AsyncSession = Depends(get_db),
):
    """拉取履约状态并回写（写入 order_status_history，source=ERP）。"""
    order = await order_service.get_visible_order(session, user, order_id)
    try:
        result = await svc.refresh_status(session, order=order, operator_id=user.id)
    except (ErpNotConfigured, ErpError) as error:
        await session.rollback()
        raise _translate(error) from error
    await session.commit()
    return ok(result, "状态已同步" if result["changed"] else "状态没有变化")


@router.get("/integrations/erp/sync-logs")
async def list_sync_logs(
    direction: str | None = None,
    status: str | None = None,
    business_id: int | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user: CurrentUser = Depends(require_permission("order:view")),
    session: AsyncSession = Depends(get_db),
):
    items, total = await svc.sync_logs(
        session,
        user=user,
        direction=direction,
        status=status,
        business_id=business_id,
        page=page,
        page_size=page_size,
    )
    return ok(page_data(items, total, page, page_size))


# ---- 回调：无需登录，由对方系统调用 ---------------------------------------


def _verify_webhook_secret(request: Request) -> None:
    """回调来源校验（P0）。

    这两个回调**直接改订单状态**（取消/签收/完成），此前**没有任何鉴权**
    ——原注释写着"对方系统尚未提供签名"，但"对方没提供"不等于"我们可以不验"：
    入口是公开的，任何人都能伪造一张"已签收"。

    现在改成共享密钥（`X-ERP-Secret`），并且**没配密钥就关闭入口**（fail closed）。
    不这么做的话，最容易被忽略的恰恰是"忘了配密钥"这个状态——
    那时它又变成匿名开放，等于没修。

    （完整整改还应有防重放与合法状态转换校验，见 issue；这一步先堵住匿名写入。）
    """
    from app.core.config import settings as app_settings
    from app.core.errors import AppError, ErrorCode

    secret = (app_settings.erp_webhook_secret or "").strip()
    if not secret:
        raise AppError(
            ErrorCode.FORBIDDEN,
            "ERP/MES 回调未启用：未配置 ERP_WEBHOOK_SECRET。"
            "配置密钥前该入口一律拒绝，避免匿名改写订单状态",
            403,
        )
    provided = (request.headers.get("X-ERP-Secret") or "").strip()
    if not provided or not secrets.compare_digest(provided, secret):
        raise AppError(ErrorCode.FORBIDDEN, "回调密钥校验失败", 403)


@router.post("/webhooks/erp/order-status")
async def order_status_webhook(
    payload: StatusWebhookRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    """订单状态回调。

    需要 `X-ERP-Secret` 头与 `ERP_WEBHOOK_SECRET` 一致；未配置密钥则入口直接关闭。
    只接受"能找到的订单 + 已知状态"，其余记一条失败日志并返回 200
    —— 让对方别因为我们反复重试。
    """
    _verify_webhook_secret(request)
    result = await svc.apply_status_webhook(
        session,
        order_no=payload.order_no,
        erp_order_id=payload.erp_order_id,
        raw_status=payload.status,
        remark=payload.remark,
    )
    return ok(result, "已处理" if result["matched"] else result["message"])


@router.post("/webhooks/erp/shipment-status")
async def shipment_status_webhook(
    payload: StatusWebhookRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    """发货状态回调：与订单状态共用处理逻辑（发货对应 shipped/delivered）。"""
    _verify_webhook_secret(request)
    result = await svc.apply_status_webhook(
        session,
        order_no=payload.order_no,
        erp_order_id=payload.erp_order_id,
        raw_status=payload.status,
        remark=payload.remark or "ERP/MES 发货状态回调",
    )
    return ok(result, "已处理" if result["matched"] else result["message"])
