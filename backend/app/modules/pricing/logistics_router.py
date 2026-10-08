"""物流试算接口（03-API §19、PRD §14）。

六个接口与文档一一对应：
  GET  /logistics/providers   可选承运商
  GET  /logistics/routes      起运地→目的地→运输方式 的可选线路
  POST /logistics/calculate   试算（可顺带落一条记录）
  POST /logistics/compare     多方案对比
  GET  /logistics/quotes      试算历史
  GET  /logistics/quotes/{id} 试算详情
"""

from decimal import Decimal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok, page_data
from app.modules.pricing import logistics as svc
from app.modules.pricing.logistics_schema import (
    LogisticsCalculateRequest,
    LogisticsCompareRequest,
)
from app.modules.pricing.model import LogisticsRate

router = APIRouter(tags=["Logistics"])


@router.get("/logistics/providers")
async def list_providers(
    _: CurrentUser = Depends(require_permission("product:view")),
    session: AsyncSession = Depends(get_db),
):
    """可选承运商列表（去重）。"""
    rows = (
        await session.execute(
            select(LogisticsRate.provider)
            .where(LogisticsRate.status == "active")
            .distinct()
            .order_by(LogisticsRate.provider.asc())
        )
    ).scalars().all()
    return ok([{"provider": name} for name in rows])


@router.get("/logistics/routes")
async def list_routes(
    _: CurrentUser = Depends(require_permission("product:view")),
    session: AsyncSession = Depends(get_db),
):
    """可选线路：起运地 → 目的地 → 运输方式，附带承运商与时效。

    只读现有费率表推导，不额外建表——线路本来就是费率的组合。
    """
    rates = (
        await session.execute(
            select(LogisticsRate)
            .where(LogisticsRate.status == "active")
            .order_by(LogisticsRate.id.asc())
        )
    ).scalars().all()

    routes: dict[tuple, dict] = {}
    for rate in rates:
        key = (
            rate.origin_region or "不限",
            rate.destination_region or "全国",
            rate.shipping_method,
        )
        entry = routes.setdefault(
            key,
            {
                "origin": rate.origin_region,
                "destination": rate.destination_region,
                "shipping_method": rate.shipping_method,
                "providers": [],
                "eta_days_min": None,
                "eta_days_max": None,
            },
        )
        if rate.provider not in entry["providers"]:
            entry["providers"].append(rate.provider)
        if rate.eta_days is not None:
            current_min = entry["eta_days_min"]
            entry["eta_days_min"] = (
                rate.eta_days if current_min is None else min(current_min, rate.eta_days)
            )
        eta_max = rate.eta_days_max if rate.eta_days_max is not None else rate.eta_days
        if eta_max is not None:
            current_max = entry["eta_days_max"]
            entry["eta_days_max"] = eta_max if current_max is None else max(current_max, eta_max)

    return ok(list(routes.values()))


@router.post("/logistics/calculate")
async def calculate(
    payload: LogisticsCalculateRequest,
    request: Request,
    user: CurrentUser = Depends(require_permission("product:view")),
    session: AsyncSession = Depends(get_db),
):
    """物流试算：算出计费重与费用。

    默认只算不落库；`save=true` 时把选中方案落成一条记录（PRD 要求"方案列表"，
    ER §10 要求留痕），并把该费用回填给调用方，供报价明细使用。
    """
    prepared = await svc.prepare(
        session,
        sku_id=payload.sku_id,
        quantity=payload.quantity,
        origin=payload.origin,
        destination=payload.destination,
        shipping_method=payload.shipping_method,
        volume_override=payload.volume_override,
        weight_override=payload.weight_override,
    )

    options = prepared["options"]
    if not options:
        return ok(
            {
                **prepared,
                "selected": None,
                "quoted_id": None,
            },
            "没有可用的运费费率",
        )

    selected = None
    if payload.selected_provider:
        selected = next(
            (opt for opt in options if opt["provider"] == payload.selected_provider), None
        )
        if selected is None:
            raise AppError(
                ErrorCode.PARAM_ERROR,
                f"承运商「{payload.selected_provider}」没有匹配到方案",
                422,
            )
    else:
        selected = options[0]

    quoted_id = None
    if payload.save:
        quote = await svc.save_quote(
            session,
            user=user,
            prepared=prepared,
            option=selected,
            customer_id=payload.customer_id,
            opportunity_id=payload.opportunity_id,
        )
        quoted_id = quote.id
        await write_audit(
            session,
            operator_id=user.id,
            action="create",
            business_type="logistics_quote",
            business_id=quote.id,
            after=svc.serialize_quote(quote),
            ip=client_ip(request),
        )
        await session.commit()

    return ok(
        {
            "sku": prepared["sku"],
            "quantity": prepared["quantity"],
            "origin": prepared["origin"],
            "destination": prepared["destination"],
            "shipping_method": payload.shipping_method,
            "package_type": payload.package_type,
            "measures": prepared["measures"],
            "options": options,
            "selected": selected,
            "quoted_id": quoted_id,
            "warnings": prepared["warnings"],
            # 命中级别（见 `logistics.match_rates`）：1 = 精确命中，≥2 = 放宽过。
            # 页面据此提示"这条可能不属于这次要发的地方"；核价估算也认它
            # （别让调用方去认提示文字 —— 文案一改就悄悄失效）。
            "match_level": prepared["match_level"],
        }
    )


@router.post("/logistics/compare")
async def compare(
    payload: LogisticsCompareRequest,
    _: CurrentUser = Depends(require_permission("product:view")),
    session: AsyncSession = Depends(get_db),
):
    """多方案对比：列出全部匹配费率，按费用升序，附带时效与计价口径。"""
    prepared = await svc.prepare(
        session,
        sku_id=payload.sku_id,
        quantity=payload.quantity,
        origin=payload.origin,
        destination=payload.destination,
        shipping_method=payload.shipping_method,
        volume_override=payload.volume_override,
        weight_override=payload.weight_override,
    )
    options = prepared["options"]
    cheapest = options[0]["provider"] if options else None
    fastest = None
    timed = [opt for opt in options if opt["eta_days"] is not None]
    if timed:
        fastest = min(timed, key=lambda opt: opt["eta_days"])["provider"]

    return ok(
        {
            "measures": prepared["measures"],
            "options": options,
            "option_count": len(options),
            "cheapest_provider": cheapest,
            "fastest_provider": fastest,
            "warnings": prepared["warnings"],
            # 与试算同口径：1 = 精确命中，≥2 = 放宽过（见 `logistics.match_rates`）
            "match_level": prepared["match_level"],
        }
    )


@router.get("/logistics/quotes")
async def list_quotes(
    customer_id: int | None = None,
    opportunity_id: int | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user: CurrentUser = Depends(require_permission("product:view")),
    session: AsyncSession = Depends(get_db),
):
    rows, total = await svc.list_quotes(
        session,
        user,
        customer_id=customer_id,
        opportunity_id=opportunity_id,
        page=page,
        page_size=page_size,
    )
    return ok(page_data([svc.serialize_quote(row) for row in rows], total, page, page_size))


@router.get("/logistics/quotes/{quote_id}")
async def get_quote(
    quote_id: int,
    user: CurrentUser = Depends(require_permission("product:view")),
    session: AsyncSession = Depends(get_db),
):
    quote = await svc.get_quote_or_404(session, quote_id)
    # 运费试算单带着报价金额与地址，而 get_quote_or_404 不接收 user
    # ——函数签名上就不可能做范围判定，守的只是 product:view（业务岗都有）。
    # 按它挂的客户补数据范围：没有客户归属的试算单（纯比价）不拦。
    if getattr(quote, "customer_id", None):
        from app.modules.customer import service as customer_service

        await customer_service.get_visible_customer(session, user, quote.customer_id)
    return ok(svc.serialize_quote(quote))


__all__ = ["router", "Decimal"]
