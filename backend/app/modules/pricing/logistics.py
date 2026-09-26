"""物流试算（PRD §14、02-ER §10）。

内贸口径：起运地 + 目的地 + 运输方式 → 匹配运费费率 → 算计费重 → 出费用与时效。

为什么不能只按重量算：抛货（体积大、重量轻）按重量报价会严重低估成本。
所以计费重取「实际重量」与「体积重」的较大者，费用再取「重量计价」与
「体积计价」的较大者——这正是承运商的实际做法。

体积重换算系数（每立方米折多少公斤）按行业惯例默认 167（≈6000 cm³/kg），
放在系统配置 `logistics_volumetric_ratio` 里，业务可改。
"""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.data_scope import scoped_owner_ids
from app.core.deps import CurrentUser
from app.core.errors import AppError, ErrorCode
from app.modules.pricing.model import LogisticsQuote, LogisticsRate
from app.modules.product.model import Sku
from app.modules.settings import service as settings_service

ZERO = Decimal(0)
_THOUSAND = Decimal(1000)
_MILLION = Decimal(1000000)


def _f(value) -> float | None:
    return None if value is None else round(float(value), 4)


def serialize_rate(rate: LogisticsRate) -> dict:
    return {
        "id": rate.id,
        "provider": rate.provider,
        "origin_region": rate.origin_region,
        "destination_region": rate.destination_region,
        "shipping_method": rate.shipping_method,
        "unit_price_per_kg": _f(rate.unit_price_per_kg),
        "unit_price_per_volume": _f(rate.unit_price_per_volume),
        "min_charge": _f(rate.min_charge),
        "eta_days": rate.eta_days,
        "eta_days_max": rate.eta_days_max,
        "status": rate.status,
        "remark": rate.remark,
    }


def serialize_quote(quote: LogisticsQuote) -> dict:
    return {
        "id": quote.id,
        "customer_id": quote.customer_id,
        "opportunity_id": quote.opportunity_id,
        "sku_id": quote.sku_id,
        "quantity": _f(quote.quantity),
        "origin": quote.origin,
        "destination": quote.destination,
        "shipping_method": quote.shipping_method,
        "chargeable_weight": _f(quote.chargeable_weight),
        "actual_weight": _f(quote.actual_weight),
        "volume": _f(quote.volume),
        "currency": quote.currency,
        "amount": _f(quote.amount),
        "unit_price": _f(quote.unit_price),
        "eta_days": quote.eta_days,
        "provider": quote.provider,
        "created_by": quote.created_by,
        "created_at": quote.created_at.isoformat() if quote.created_at else None,
    }


# ------------------------------------------------------------------ 体积与计费重

async def volumetric_ratio(session: AsyncSession) -> Decimal:
    """每立方米折算多少公斤（体积重系数）。

    返回 0 表示**不启用体积重**，计费重只取实际重量。默认就是 0：
    这个系数强依赖货物形态（纸箱轻抛货 vs 嵌套周转箱差一个数量级），
    没拿到业务口径前不该替业务拍板。
    """
    return Decimal(
        str(await settings_service.get_number(session, "logistics_volumetric_ratio", "number", 0))
    )


def sku_unit_volume(sku: Sku) -> tuple[Decimal | None, str | None]:
    """单件体积（立方米）与其来源说明。

    两个来源，优先级如下：
    1. 箱规体积 ÷ 箱装数。SKU 上的 `carton_volume` 是**整箱**体积
       （种子里 0.144 m³ 对应 carton_qty=20），必须摊到单件，否则会放大 20 倍。
    2. 长宽高推算。按毫米记，mm³ → m³ 除以 1e9。
    """
    if sku.carton_volume is not None and sku.carton_volume > 0:
        if sku.carton_qty and sku.carton_qty > 0:
            per_unit = (sku.carton_volume / Decimal(sku.carton_qty)).quantize(
                Decimal("0.000001")
            )
            return per_unit, f"箱规体积 {sku.carton_volume} m³ ÷ {sku.carton_qty} 件/箱"
        return sku.carton_volume, "SKU 箱规体积（未填箱装数，按单件计）"
    if sku.length and sku.width and sku.height:
        cubic_mm = sku.length * sku.width * sku.height
        return (cubic_mm / Decimal("1000000000")).quantize(Decimal("0.000001")), "SKU 长宽高推算"
    return None, None


async def compute_weight_and_volume(
    session: AsyncSession,
    *,
    sku: Sku,
    quantity: Decimal,
    volume_override: Decimal | None = None,
    weight_override: Decimal | None = None,
) -> dict:
    """算出实际重量、体积、体积重、计费重。"""
    warnings: list[str] = []

    unit_weight = weight_override if weight_override is not None else sku.weight
    if unit_weight is None:
        warnings.append("该 SKU 没有维护单重，无法计算实际重量")
        unit_weight = ZERO

    unit_volume, volume_source = sku_unit_volume(sku)
    if volume_override is not None:
        unit_volume, volume_source = volume_override, "本次指定"
    if unit_volume is None:
        warnings.append("该 SKU 没有维护体积（箱规体积或长宽高），无法计算体积重")
        unit_volume = ZERO

    actual_weight = (unit_weight * quantity).quantize(Decimal("0.0001"))
    volume = (unit_volume * quantity).quantize(Decimal("0.0001"))

    ratio = await volumetric_ratio(session)
    volumetric_weight = (volume * ratio).quantize(Decimal("0.0001"))
    volumetric_enabled = ratio > 0

    if volumetric_enabled:
        chargeable = max(actual_weight, volumetric_weight)
        basis = "体积重" if volumetric_weight > actual_weight else "实际重量"
    else:
        # 没配体积重系数：只用实际重量计费，体积重仍返回供参考
        chargeable = actual_weight
        basis = "实际重量"
        if volumetric_weight > 0:
            warnings.append(
                f"体积重 {volumetric_weight} kg 高于实际重量，但系统未配置体积重系数"
                f"（logistics_volumetric_ratio），本次仍按实际重量计费"
            )

    return {
        "actual_weight": actual_weight,
        "volume": volume,
        "volumetric_weight": volumetric_weight,
        "volumetric_enabled": volumetric_enabled,
        "chargeable_weight": chargeable,
        "chargeable_basis": basis,
        "volume_source": volume_source,
        "volumetric_ratio": ratio,
        "warnings": warnings,
    }


# ------------------------------------------------------------------ 费率匹配

def rate_query() -> Select:
    return select(LogisticsRate).where(LogisticsRate.status == "active")


async def match_rates(
    session: AsyncSession,
    *,
    origin: str | None,
    destination: str | None,
    shipping_method: str | None,
) -> tuple[list[LogisticsRate], list[str]]:
    """按用户给了哪些条件，逐级放宽地匹配费率，返回 (费率列表, 提示)。

    匹配各级的口径（这是关键，写错了就会出现"明明有华东费率却说没匹配到"）：
      第 1 级（精确）：用户给的条件全部命中，且**不给条件时不做限制**
      第 2 级（放宽）：用户给了目的地，但运输方式没命中 → 只按目的地收窄
      第 3 级（兜底）：按用户给的条件收窄后仍为空 → 列出全部启用费率

    每一级内部"某条件的匹配"定义为：费率的该字段 == 用户值，或该字段为空（表示不限）。
    这样填了目的地就会优先拿专属费率，而不会和"不限"的费率混成一个并集。
    """
    warnings: list[str] = []

    def _narrow(stmt: Select, *, use_origin: bool, use_destination: bool, use_method: bool) -> Select:
        if use_origin and origin:
            stmt = stmt.where(
                (LogisticsRate.origin_region == origin) | (LogisticsRate.origin_region.is_(None))
            )
        if use_destination and destination:
            stmt = stmt.where(
                (LogisticsRate.destination_region == destination)
                | (LogisticsRate.destination_region.is_(None))
            )
        if use_method and shipping_method:
            stmt = stmt.where(LogisticsRate.shipping_method == shipping_method)
        return stmt

    async def _fetch(*, use_origin: bool, use_destination: bool, use_method: bool) -> list[LogisticsRate]:
        rows = await session.execute(
            _narrow(
                rate_query(),
                use_origin=use_origin,
                use_destination=use_destination,
                use_method=use_method,
            ).order_by(LogisticsRate.id.asc())
        )
        return list(rows.scalars().all())

    # 第 0 级：用户什么都没给 —— 就是全部，不算"降级"，不该提示
    if not any([origin, destination, shipping_method]):
        return await _fetch(use_origin=False, use_destination=False, use_method=False), warnings

    # 第 1 级：能用的条件全用上
    rows = await _fetch(use_origin=True, use_destination=True, use_method=True)
    if rows:
        return rows, warnings

    # 第 2 级：丢掉"起运地"（业务往往只维护目的地与运输方式）
    if origin:
        rows = await _fetch(use_origin=False, use_destination=True, use_method=True)
        if rows:
            warnings.append(f"没有起运地「{origin}」的专属费率，已按目的地 + 运输方式匹配")
            return rows, warnings

    # 第 3 级：丢掉"目的地"，保留运输方式。
    # 顺序很重要：**目的地比运输方式更本质**，而且丢掉运输方式等于换了承运方式
    # （用户说"走陆运"却给一条"专线"是更大的语义改变）。
    # 所以先放宽目的地，再考虑放宽运输方式。
    if shipping_method and destination:
        rows = await _fetch(use_origin=False, use_destination=False, use_method=True)
        if rows:
            warnings.append(
                f"没有发往「{destination}」的费率，已放宽为只看运输方式「{shipping_method}」"
            )
            return rows, warnings

    # 第 4 级：只按目的地（用户没指定运输方式，或该方式确实没有任何费率）
    if destination:
        rows = await _fetch(use_origin=False, use_destination=True, use_method=False)
        if rows:
            if shipping_method:
                warnings.append(
                    f"没有运输方式「{shipping_method}」的费率，已放宽为只看目的地「{destination}」"
                )
            return rows, warnings

    # 第 5 级：兜底 —— 列出全部启用费率供对比
    rows = await _fetch(use_origin=False, use_destination=False, use_method=False)
    if rows:
        warnings.append("没有匹配到目的地/运输方式的费率，已列出全部启用中的费率供对比")
    else:
        warnings.append("还没有维护任何运费费率，请先在价格中心配置")
    return rows, warnings


def quote_rate(
    rate: LogisticsRate,
    *,
    chargeable_weight: Decimal,
    volume: Decimal,
    min_charge_override: Decimal | None = None,
) -> dict:
    """按一条费率算出费用。

    费用取「重量计价」与「体积计价」的较大者：
    承运商不会两个都收，但会按对己方更有利的那个收。
    """
    by_weight = (chargeable_weight * rate.unit_price_per_kg).quantize(Decimal("0.01"))
    by_volume = ZERO
    if rate.unit_price_per_volume:
        by_volume = (volume * rate.unit_price_per_volume).quantize(Decimal("0.01"))

    amount = max(by_weight, by_volume)
    minimum = min_charge_override if min_charge_override is not None else rate.min_charge
    below_minimum = False
    if minimum and amount < minimum:
        amount = minimum
        below_minimum = True

    eta = rate.eta_days
    eta_max = rate.eta_days_max
    eta_text = None
    if eta is not None and eta_max is not None and eta_max != eta:
        eta_text = f"{eta}-{eta_max} 天"
    elif eta is not None:
        eta_text = f"约 {eta} 天"

    return {
        "provider": rate.provider,
        "rate_id": rate.id,
        "shipping_method": rate.shipping_method,
        "origin_region": rate.origin_region,
        "destination_region": rate.destination_region,
        "amount": float(amount),
        "currency": "CNY",
        "by_weight_amount": float(by_weight),
        "by_volume_amount": float(by_volume),
        "pricing_basis": "体积" if by_volume > by_weight else "重量",
        "above_minimum": not below_minimum,
        "min_charge": _f(minimum),
        "eta_days": eta,
        "eta_days_max": eta_max,
        "eta_text": eta_text,
        "unit_price_per_kg": _f(rate.unit_price_per_kg),
        "unit_price_per_volume": _f(rate.unit_price_per_volume),
    }


async def prepare(
    session: AsyncSession,
    *,
    sku_id: int,
    quantity: Decimal,
    origin: str | None = None,
    destination: str | None = None,
    shipping_method: str | None = None,
    volume_override: Decimal | None = None,
    weight_override: Decimal | None = None,
) -> dict:
    """试算前的公共准备：校验 SKU、算计费重、匹配费率、出方案列表。"""
    sku = await session.get(Sku, sku_id)
    if sku is None or sku.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "SKU 不存在", 404)
    if quantity <= 0:
        raise AppError(ErrorCode.PARAM_ERROR, "数量必须大于 0", 422)

    measures = await compute_weight_and_volume(
        session,
        sku=sku,
        quantity=quantity,
        volume_override=volume_override,
        weight_override=weight_override,
    )
    rates, warnings = await match_rates(
        session,
        origin=origin,
        destination=destination,
        shipping_method=shipping_method,
    )

    options = [
        quote_rate(
            rate,
            chargeable_weight=measures["chargeable_weight"],
            volume=measures["volume"],
        )
        for rate in rates
    ]
    options.sort(key=lambda option: option["amount"])

    return {
        "sku": {
            "id": sku.id,
            "sku_code": sku.sku_code,
            "name": sku.name,
            "specification": sku.specification,
            "unit": sku.unit,
            "package_type": sku.package_type,
        },
        "quantity": _f(quantity),
        "origin": origin,
        "destination": destination,
        "shipping_method": shipping_method,
        "measures": measures,
        "options": options,
        "warnings": measures["warnings"] + warnings,
    }


# ------------------------------------------------------------------ 留痕

async def save_quote(
    session: AsyncSession,
    *,
    user: CurrentUser,
    prepared: dict,
    option: dict,
    customer_id: int | None = None,
    opportunity_id: int | None = None,
) -> LogisticsQuote:
    """把选中的方案落成一条物流试算记录（02-ER §10）。"""
    measures = prepared["measures"]
    quote = LogisticsQuote(
        customer_id=customer_id,
        opportunity_id=opportunity_id,
        sku_id=prepared["sku"]["id"],
        quantity=Decimal(str(prepared["quantity"] or 0)),
        origin=prepared["origin"],
        destination=prepared["destination"],
        shipping_method=option["shipping_method"],
        chargeable_weight=Decimal(str(measures["chargeable_weight"])),
        actual_weight=Decimal(str(measures["actual_weight"])),
        volume=Decimal(str(measures["volume"])),
        currency=option["currency"],
        amount=Decimal(str(option["amount"])),
        unit_price=(
            Decimal(str(option["unit_price_per_kg"]))
            if option.get("unit_price_per_kg") is not None
            else None
        ),
        eta_days=option.get("eta_days"),
        provider=option.get("provider"),
        raw_data={
            "option": option,
            "measures": {k: _f(v) if isinstance(v, Decimal) else v for k, v in measures.items()},
            "warnings": prepared["warnings"],
        },
        created_by=user.id,
        created_at=datetime.now(UTC),
    )
    session.add(quote)
    await session.flush()
    return quote


async def list_quotes(
    session: AsyncSession,
    user: CurrentUser,
    *,
    customer_id: int | None = None,
    opportunity_id: int | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[LogisticsQuote], int]:
    from app.core.response import paginate

    stmt = select(LogisticsQuote)
    if customer_id:
        stmt = stmt.where(LogisticsQuote.customer_id == customer_id)
    if opportunity_id:
        stmt = stmt.where(LogisticsQuote.opportunity_id == opportunity_id)
    # 数据范围：试算记录带 customer_id，按客户可见性收紧；没有客户的行（临时试算）人人可见
    owner_ids = await scoped_owner_ids(session, user)
    if owner_ids is not None:
        from app.modules.customer.model import Customer

        visible_customers = select(Customer.id).where(Customer.owner_id.in_(owner_ids))
        stmt = stmt.where(
            LogisticsQuote.customer_id.is_(None)
            | LogisticsQuote.customer_id.in_(visible_customers)
        )
    stmt = stmt.order_by(LogisticsQuote.id.desc())
    return await paginate(session, stmt, page, page_size)


async def get_quote_or_404(session: AsyncSession, quote_id: int) -> LogisticsQuote:
    quote = await session.get(LogisticsQuote, quote_id)
    if quote is None:
        raise AppError(ErrorCode.NOT_FOUND, "物流试算记录不存在", 404)
    return quote


__all__ = [
    "compute_weight_and_volume",
    "get_quote_or_404",
    "list_quotes",
    "match_rates",
    "prepare",
    "quote_rate",
    "save_quote",
    "serialize_quote",
    "serialize_rate",
    "sku_unit_volume",
    "volumetric_ratio",
]
