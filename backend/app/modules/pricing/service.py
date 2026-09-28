"""核价引擎。

输入：SKU、数量、客户（可选）、运费（可选）、目标利润率（可选）
输出：成本结构、建议价、建议区间、最低允许价、利润、利润率、是否需要审批

兜底规则（业务确认前先跑起来，数值可在价格中心界面直接改）：
- 无成本记录 → 按 0 处理并在结果里给出 warning，不静默骗人；
- 无价格规则 → 标准价 = 成本 ÷ (1 − 默认目标利润率 30%)；
- 无价格权限 → 默认最低利润率 15%（与 07 计划的占位值一致）。
"""

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.modules.customer.model import Customer
from app.modules.pricing.model import (
    CustomerPriceRule,
    LogisticsRate,
    PricePermission,
    PriceRule,
    ProductCost,
)
from app.modules.product.model import Product, Sku
from app.modules.settings import service as settings_service
from app.modules.user.model import Role

ZERO = Decimal("0")


def _f(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def serialize_cost(cost: ProductCost, sku_code: str | None = None) -> dict:
    return {
        "id": cost.id,
        "sku_id": cost.sku_id,
        "sku_code": sku_code,
        "purchase_cost": _f(cost.purchase_cost),
        "production_cost": _f(cost.production_cost),
        "package_cost": _f(cost.package_cost),
        "processing_cost": _f(cost.processing_cost),
        "total_cost": _f(cost.total_cost),
        "currency": cost.currency,
        "effective_from": cost.effective_from,
        "effective_to": cost.effective_to,
        "remark": cost.remark,
        "created_at": cost.created_at,
    }


def serialize_price_rule(rule: PriceRule, sku_code: str | None = None) -> dict:
    return {
        "id": rule.id,
        "sku_id": rule.sku_id,
        "sku_code": sku_code,
        "customer_level": rule.customer_level,
        "min_qty": _f(rule.min_qty),
        "max_qty": _f(rule.max_qty),
        "standard_price": _f(rule.standard_price),
        "guide_price": _f(rule.guide_price),
        "minimum_price": _f(rule.minimum_price),
        "target_margin": _f(rule.target_margin),
        "currency": rule.currency,
        "effective_from": rule.effective_from,
        "effective_to": rule.effective_to,
        "status": rule.status,
        "remark": rule.remark,
    }


def serialize_customer_price(rule: CustomerPriceRule, sku_code: str | None = None,
                             customer_name: str | None = None) -> dict:
    return {
        "id": rule.id,
        "customer_id": rule.customer_id,
        "customer_name": customer_name,
        "sku_id": rule.sku_id,
        "sku_code": sku_code,
        "min_qty": _f(rule.min_qty),
        "max_qty": _f(rule.max_qty),
        "agreed_price": _f(rule.agreed_price),
        "minimum_price": _f(rule.minimum_price),
        "currency": rule.currency,
        "effective_from": rule.effective_from,
        "effective_to": rule.effective_to,
        "remark": rule.remark,
    }


def serialize_permission(permission: PricePermission, role_name: str | None = None) -> dict:
    return {
        "id": permission.id,
        "role_id": permission.role_id,
        "role_name": role_name,
        "minimum_margin": _f(permission.minimum_margin),
        "discount_limit": _f(permission.discount_limit),
        "can_approve": permission.can_approve,
        "status": permission.status,
        "remark": permission.remark,
    }


def serialize_logistics_rate(rate: LogisticsRate) -> dict:
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


async def get_effective_cost(
    session: AsyncSession, sku_id: int, on_date: date | None = None
) -> ProductCost | None:
    """取生效中的成本。成本带生效区间，改价不影响历史报价。"""
    today = on_date or datetime.now(UTC).date()
    stmt = (
        select(ProductCost)
        .where(
            ProductCost.sku_id == sku_id,
            ProductCost.effective_from <= today,
            or_(ProductCost.effective_to.is_(None), ProductCost.effective_to >= today),
        )
        .order_by(ProductCost.effective_from.desc(), ProductCost.id.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def find_price_rule(
    session: AsyncSession,
    *,
    sku_id: int,
    quantity: Decimal,
    customer_level: str | None,
    on_date: date | None = None,
) -> PriceRule | None:
    """按「客户等级 + 数量区间」匹配价格规则；等级专属规则优先于通用规则。"""
    today = on_date or datetime.now(UTC).date()
    stmt = select(PriceRule).where(
        PriceRule.sku_id == sku_id,
        PriceRule.status == "active",
        PriceRule.min_qty <= quantity,
        or_(PriceRule.max_qty.is_(None), PriceRule.max_qty >= quantity),
        or_(PriceRule.effective_from.is_(None), PriceRule.effective_from <= today),
        or_(PriceRule.effective_to.is_(None), PriceRule.effective_to >= today),
    )
    if customer_level:
        stmt = stmt.where(
            or_(PriceRule.customer_level == customer_level, PriceRule.customer_level.is_(None))
        )
    else:
        stmt = stmt.where(PriceRule.customer_level.is_(None))

    rows = (await session.execute(stmt.order_by(PriceRule.min_qty.desc()))).scalars().all()
    if not rows:
        return None
    # 有等级专属规则就优先用它
    for rule in rows:
        if rule.customer_level == customer_level:
            return rule
    return rows[0]


async def find_customer_price(
    session: AsyncSession,
    *,
    customer_id: int,
    sku_id: int,
    quantity: Decimal,
    on_date: date | None = None,
) -> CustomerPriceRule | None:
    """匹配客户专属价。有效期过滤与等级价一致：过期/未来价不命中（方案 §4.1 A02）。"""
    today = on_date or datetime.now(UTC).date()
    stmt = (
        select(CustomerPriceRule)
        .where(
            CustomerPriceRule.customer_id == customer_id,
            CustomerPriceRule.sku_id == sku_id,
            # A14：historical 是历史资料，不参与取价
            CustomerPriceRule.status == "active",
            CustomerPriceRule.min_qty <= quantity,
            or_(CustomerPriceRule.max_qty.is_(None), CustomerPriceRule.max_qty >= quantity),
            or_(
                CustomerPriceRule.effective_from.is_(None),
                CustomerPriceRule.effective_from <= today,
            ),
            or_(
                CustomerPriceRule.effective_to.is_(None),
                CustomerPriceRule.effective_to >= today,
            ),
        )
        .order_by(CustomerPriceRule.min_qty.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


def _ranges_overlap(a_min, a_max, b_min, b_max) -> bool:
    """两个闭区间是否重叠；max 为 None 视为无上界。

    边界口径沿用现有匹配语义（上下限都包含）：[100,999] 与 [1000,∞) 不重叠，
    整数件数按 100至999、1000起 填写即可避免边界重复命中（方案 §4.1）。
    """
    if a_max is not None and b_min is not None and b_min > a_max:
        return False
    if b_max is not None and a_min is not None and a_min > b_max:
        return False
    return True


async def find_price_rule_conflict(
    session: AsyncSession,
    *,
    sku_id: int,
    customer_level: str | None,
    min_qty: Decimal,
    max_qty: Decimal | None,
    effective_from: date | None,
    effective_to: date | None,
    exclude_id: int | None = None,
) -> PriceRule | None:
    """同 SKU、同等级、数量区间与有效期都重叠的现有规则（方案 §4.1 冲突检查）。

    数量小、规则少，取全量在 Python 里比 NULL 参与的 SQL 比较更不容易写错。
    """
    rows = (
        await session.execute(
            select(PriceRule).where(PriceRule.sku_id == sku_id, PriceRule.status == "active")
        )
    ).scalars().all()
    for rule in rows:
        if exclude_id is not None and rule.id == exclude_id:
            continue
        # 历史资料（A14）不参与冲突检查
        if rule.status != "active":
            continue
        if (rule.customer_level or None) != (customer_level or None):
            continue
        if _ranges_overlap(min_qty, max_qty, rule.min_qty, rule.max_qty) and _ranges_overlap(
            effective_from, effective_to, rule.effective_from, rule.effective_to
        ):
            return rule
    return None


async def find_customer_price_conflict(
    session: AsyncSession,
    *,
    customer_id: int,
    sku_id: int,
    min_qty: Decimal,
    max_qty: Decimal | None,
    effective_from: date | None,
    effective_to: date | None,
    exclude_id: int | None = None,
) -> CustomerPriceRule | None:
    rows = (
        await session.execute(
            select(CustomerPriceRule).where(
                CustomerPriceRule.customer_id == customer_id,
                CustomerPriceRule.sku_id == sku_id,
                # A14：历史资料不参与冲突检查
                CustomerPriceRule.status == "active",
            )
        )
    ).scalars().all()
    for rule in rows:
        if exclude_id is not None and rule.id == exclude_id:
            continue
        if _ranges_overlap(min_qty, max_qty, rule.min_qty, rule.max_qty) and _ranges_overlap(
            effective_from, effective_to, rule.effective_from, rule.effective_to
        ):
            return rule
    return None


# 查价结果的来源标识（方案 §4.1：客户专属价 → 客户等级价 → 通用指导价 → 待定价）
SOURCE_LABELS = {
    "customer_specific": "客户专属价",
    "level": "客户等级价",
    "general": "通用指导价",
}


async def lookup_applicable_price(
    session: AsyncSession,
    *,
    customer,
    sku_id: int,
    quantity: Decimal,
    on_date: date | None = None,
    include_customer_specific: bool = True,
) -> dict:
    """统一查价（方案 §4：只回答「卖多少、依据是什么」）。

    - 顺序：客户专属价 → 客户等级价 → 通用指导价；
    - 等级价存在但没维护指导价 → 回退通用价并**明确标注**（D4 已确认）；
    - 都没有 → `status=pending`（待定价），不做成本推算兜底（D4/D5）；
    - 成本/最低保护价由路由层按 `price:manage` 权限决定是否附加，这里不输出；
    - `include_customer_specific=False` 用于公海客户（无负责人）：专属价是
      协议价，只该让负责人看，数据范围放行不等于专属价放行。
    """
    level = (customer.level or "").strip() or None

    async def rule_payload(rule: PriceRule, source: str, note: str | None = None) -> dict:
        return {
            "status": "ok",
            "source": source,
            "source_label": SOURCE_LABELS[source],
            "unit_price": _f(rule.guide_price),
            "standard_price": _f(rule.standard_price),
            "minimum_price": _f(rule.minimum_price),
            "currency": rule.currency,
            "rule_id": rule.id,
            "effective_from": rule.effective_from,
            "effective_to": rule.effective_to,
            "fallback_note": note,
        }

    customer_price = (
        await find_customer_price(
            session, customer_id=customer.id, sku_id=sku_id, quantity=quantity, on_date=on_date
        )
        if include_customer_specific
        else None
    )
    if customer_price is not None:
        return {
            "status": "ok",
            "source": "customer_specific",
            "source_label": SOURCE_LABELS["customer_specific"],
            "unit_price": _f(customer_price.agreed_price),
            "standard_price": None,
            "minimum_price": _f(customer_price.minimum_price),
            "currency": customer_price.currency,
            "rule_id": customer_price.id,
            "effective_from": customer_price.effective_from,
            "effective_to": customer_price.effective_to,
            "fallback_note": None,
        }

    level_rule = await find_price_rule(
        session, sku_id=sku_id, quantity=quantity, customer_level=level, on_date=on_date
    )
    if level and level_rule is not None and level_rule.customer_level == level:
        # 命中等级专属规则（level 为空的客户走通用价，不能被标成"客户等级价"）
        if level_rule.guide_price is not None:
            return await rule_payload(level_rule, "level")
        # 等级价没维护指导价：按 D4 回退通用价并标注
        general = await find_price_rule(
            session, sku_id=sku_id, quantity=quantity, customer_level=None, on_date=on_date
        )
        if general is not None and general.guide_price is not None:
            return await rule_payload(
                general, "general", f"{level} 级价未维护指导价，已回退通用指导价"
            )
        return {"status": "pending", "source": None, "source_label": None,
                "unit_price": None, "fallback_note": f"{level} 级价未维护指导价，且无通用指导价"}

    if level_rule is not None and level_rule.guide_price is not None:
        # 没有等级规则（或客户无等级），find_price_rule 直接回了通用规则。
        # 客户有等级但没维护该级规则时，回退必须标注（A03/D4：回退须可解释）
        note = (
            f"{level} 级价未维护，已回退通用指导价"
            if level and level_rule.customer_level != level
            else None
        )
        return await rule_payload(level_rule, "general", note)

    return {"status": "pending", "source": None, "source_label": None,
            "unit_price": None, "fallback_note": None}


async def resolve_discount_limit(session: AsyncSession, role_codes: list[str]) -> float | None:
    """取当前用户角色中最严格的折扣上限（%）；没配置返回 None（不参与判定）。

    discount_limit 此前只在价格权限页能配、计算从不读——配置形同虚设。
    语义与最低利润率一致：按角色取，多个角色取最严格的一个。
    """
    if not role_codes:
        return None
    rows = (
        await session.execute(
            select(PricePermission.discount_limit)
            .join(Role, Role.id == PricePermission.role_id)
            .where(
                Role.code.in_(role_codes),
                PricePermission.status == "active",
                PricePermission.discount_limit.isnot(None),
            )
        )
    ).scalars().all()
    return min(rows) if rows else None


async def resolve_min_margin(session: AsyncSession, role_codes: list[str]) -> tuple[Decimal, bool]:
    """取当前用户角色中最宽松的最低利润率，以及他是否有审批权。

    角色没配价格权限时，退回系统配置里的 default_min_margin（默认 15%）。
    """
    fallback = Decimal(
        str(await settings_service.get_number(session, "default_min_margin", "ratio", 0.15))
    )
    if not role_codes:
        return fallback, False
    rows = (
        await session.execute(
            select(PricePermission, Role.code)
            .join(Role, Role.id == PricePermission.role_id)
            .where(Role.code.in_(role_codes), PricePermission.status == "active")
        )
    ).all()
    if not rows:
        return fallback, False
    margin = min((perm.minimum_margin for perm, _ in rows), default=fallback)
    can_approve = any(perm.can_approve for perm, _ in rows)
    return margin, can_approve


async def estimate_logistics(
    session: AsyncSession,
    *,
    sku: Sku,
    quantity: Decimal,
    destination_region: str | None = None,
    shipping_method: str | None = None,
) -> tuple[Decimal | None, str | None]:
    """按物流模块估算**单件**运费（与物流试算同一份实现，不维护第二套算法）。

    计费重取「实际重量 / 体积重」较大者（抛货不再低估运费），费用取
    「重量计价 / 体积计价」较大者，最低收费按数量摊到单件——核价全程按
    「单价」计算，运费必须是单件口径，否则利润完全失真。

    兼容旧口径：SKU 没维护单重时返回 None 请手工填写（与历史行为一致）；
    没录体积数据的 SKU 计费重=实际重量，结果与旧算法相同（行为不变）。
    取费率时在匹配到的方案里选**最便宜**的一条（估算口径），并在备注里
    说明计价方式与时效。
    """
    from app.modules.pricing import logistics as logistics_calc

    # 没维护单重但**有体积数据**的 SKU（典型抛货）仍可按体积计价——
    # 在此直接退出会让体积功能对这类 SKU 失效；单重与体积都缺才请手工填写
    if sku.weight is None and logistics_calc.sku_unit_volume(sku)[0] is None:
        return None, "该 SKU 没有维护单重与体积数据，无法自动估算运费，请手工填写"

    try:
        prepared = await logistics_calc.prepare(
            session,
            sku_id=sku.id,
            quantity=quantity,
            destination=destination_region,
            shipping_method=shipping_method,
        )
    except AppError as exc:
        return None, f"运费自动估算失败：{exc.message}"

    options = prepared["options"]
    if not options:
        return None, "还没有维护运费费率，请先在价格中心配置或手工填写运费"

    chosen = options[0]  # prepare 已按金额升序排列，估算取最便宜方案
    notes: list[str] = []
    for warning in prepared["warnings"]:
        if "体积" in warning or "单重" in warning:
            notes.append(warning)
    notes.append(
        f"已按{chosen['pricing_basis']}计价（{chosen['provider']} {chosen['shipping_method']}，"
        f"{chosen['eta_text'] or '时效未配'}）"
    )
    if not chosen["above_minimum"]:
        notes.append("触发最低收费，已按数量摊入单件")

    unit = (Decimal(str(chosen["amount"])) / quantity).quantize(Decimal("0.0001"))
    return unit, "；".join(notes)


async def calculate_price(
    session: AsyncSession,
    *,
    sku_id: int,
    quantity: Decimal,
    customer_id: int | None = None,
    logistics_cost: Decimal | None = None,
    target_margin: Decimal | None = None,
    target_profit_amount: Decimal | None = None,
    quoted_price: Decimal | None = None,
    role_codes: list[str] | None = None,
    currency: str = "CNY",
    exchange_rate: Decimal | None = None,
    tax_refund_rate: Decimal | None = None,
    # PRD §13 要求的其余输入项
    customer_level: str | None = None,
    country: str | None = None,
    package_type: str | None = None,
    shipping_method: str | None = None,
    payment_terms: str | None = None,
) -> dict:
    """核价。

    内贸口径（默认）：全人民币，无退税，行为与以前完全一致。
    外贸口径（可选）：传入 currency ≠ CNY 且给 exchange_rate 时，
    报价按外币计价、成本按汇率折算；再叠加出口退税。
    **两种口径共用同一套代码，不需要切换模式**——传了参数就是外贸，不传就是内贸。

    PRD §13 的两类底价在这里**分开返回**，不再合并：
    - `protection_price`：最低保护价，来自价格规则/客户特殊价，是「公司规定不能低于」；
    - `minimum_price`：当前用户授权底价，是「以你的权限不能低于」。
    审批判定两个都要看（PRD §16「低于业务员授权价」「低于保护价」是两条触发条件）。
    """
    sku = await session.get(Sku, sku_id)
    if sku is None or sku.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "SKU 不存在", 404)

    warnings: list[str] = []
    customer = await session.get(Customer, customer_id) if customer_id else None
    # 显式传入的等级优先，否则按客户档案推
    resolved_level = customer_level or (customer.level if customer else None)

    cost = await get_effective_cost(session, sku_id)
    has_cost = cost is not None
    if cost is None:
        # A06/D5：没有成本就谈不上利润——宁可"利润不可计算"，
        # 也不能拿 0 成本算出 100% 的假毛利
        warnings.append(
            "该 SKU 无生效成本：利润不可计算，利润类审批判定停用；"
            "按已维护售价报价不受影响"
        )
    purchase = cost.purchase_cost if cost else ZERO
    production = cost.production_cost if cost else ZERO
    package = cost.package_cost if cost else ZERO
    processing = cost.processing_cost if cost else ZERO
    goods_cost = purchase + production + package + processing

    if package_type and package_type != (sku.package_type or None):
        # 指定了与 SKU 默认不同的包装：目前没有分包装的成本表，
        # 只能明确告诉用户「按 SKU 默认包装算的」，避免静默用错口径
        warnings.append(
            f"已指定包装「{package_type}」，但系统尚未维护分包装成本，"
            f"成本仍按 SKU 默认包装（{sku.package_type or '未填'}）计算"
        )

    rule = await find_price_rule(
        session, sku_id=sku_id, quantity=quantity, customer_level=resolved_level
    )
    if rule is None:
        warnings.append("该 SKU 没有匹配的价格规则，已按默认利润率反推标准价")
    customer_rule = (
        await find_customer_price(
            session, customer_id=customer_id, sku_id=sku_id, quantity=quantity
        )
        if customer_id
        else None
    )

    margin = (
        target_margin
        or (rule.target_margin if rule and rule.target_margin is not None else None)
        or Decimal(
            str(
                await settings_service.get_number(
                    session, "default_target_margin", "ratio", 0.30
                )
            )
        )
    )
    if margin >= 1:
        raise AppError(ErrorCode.PARAM_ERROR, "目标利润率必须小于 100%")

    if logistics_cost is None:
        logistics_cost, logistics_warning = await estimate_logistics(
            session,
            sku=sku,
            quantity=quantity,
            destination_region=country,
            shipping_method=shipping_method,
        )
        if logistics_warning:
            warnings.append(logistics_warning)
    logistics = logistics_cost or ZERO

    base_cost = goods_cost + logistics
    standard_price = (
        (rule.standard_price if rule else None) or (base_cost / (Decimal(1) - margin))
    )
    recommended = (
        (customer_rule.agreed_price if customer_rule else None)
        or (rule.guide_price if rule and rule.guide_price else None)
        or (base_cost / (Decimal(1) - margin) if has_cost else None)
    )
    if recommended is None:
        warnings.append("无成本也无已维护售价，无法给出建议价——请先维护成本或价格规则")

    # 利润要求（绝对金额口径）：反推一个等效的目标利润率，便于统一走后面的算法
    if target_profit_amount is not None and target_margin is None:
        if recommended > 0 and target_profit_amount < recommended:
            margin = (recommended - target_profit_amount) / recommended
        else:
            warnings.append(
                f"要求的单件利润 ¥{target_profit_amount} 高于建议价，已忽略该约束"
            )

    min_margin, can_approve = await resolve_min_margin(session, role_codes or [])
    # 授权底价由利润率反推——没有成本就没有授权底价（利润类判定同步停用）
    floor_from_margin = (
        base_cost / (Decimal(1) - min_margin)
        if has_cost and min_margin < 1
        else None
    )

    # 公司口径的保护价（价格规则 / 客户特殊价），与"我的授权底价"分开
    protection_candidates = [
        value
        for value in [
            rule.minimum_price if rule else None,
            customer_rule.minimum_price if customer_rule else None,
        ]
        if value is not None
    ]
    protection_price = max(protection_candidates) if protection_candidates else None

    # 当前用户的授权底价 = max(利润率反推价, 保护价)；两者都拿不到时为 None
    floor_values = [
        value
        for value in [floor_from_margin, protection_price]
        if value is not None
    ]
    floor_price = max(floor_values) if floor_values else None

    # ---- 绝对底价（D7 判定层 C 步）----
    # 由生效成本推出来（成本 / 成本×(1+X)），系统设置里开关与比例。
    # 与保护价的区别：保护价低过 → 触发审批（可被批准）；绝对底价低过 → 硬拒绝。
    hard_floor_mode = await settings_service.get_text(session, "hard_floor", "mode", "off")
    hard_floor_price = None
    if has_cost and hard_floor_mode in ("cost", "cost_markup"):
        markup = Decimal(
            str(await settings_service.get_number(session, "hard_floor", "markup_ratio", 0.0))
        )
        hard_floor_price = (
            base_cost if hard_floor_mode == "cost" else base_cost * (Decimal(1) + markup)
        )

    range_ratio = Decimal(
        str(await settings_service.get_number(session, "price_range_ratio", "ratio", 0.04))
    )
    recommended_range = (
        [
            recommended * (Decimal(1) - range_ratio),
            recommended * (Decimal(1) + range_ratio),
        ]
        if recommended is not None
        else [None, None]
    )

    # ---- 外贸口径（可选）：把价格类数字统一折成计价币种，避免"美元价 vs 人民币底线"这种错比 ----
    is_foreign = currency.upper() != "CNY"
    fx = exchange_rate
    if is_foreign and (fx is None or fx <= 0):
        warnings.append(f"报价币种是 {currency}，但没有提供汇率，暂按人民币口径核价")
        fx = None
    cost_for_profit = base_cost
    if is_foreign and fx:
        convert = lambda value: (value / fx).quantize(Decimal("0.0001"))  # noqa: E731
        standard_price = convert(standard_price)
        recommended = convert(recommended)
        floor_price = convert(floor_price)
        # 授权底价同样必须折成计价币种：下面 below_authorized 拿它和 check_price 比，
        # 不折就会拿美元价 vs 人民币底线（10 号文档第八节踩过的坑，这里别改回去）
        floor_from_margin = convert(floor_from_margin)
        if hard_floor_price is not None:
            hard_floor_price = convert(hard_floor_price)
        if protection_price is not None:
            protection_price = convert(protection_price)
        recommended_range = [convert(recommended_range[0]), convert(recommended_range[1])]
        cost_for_profit = convert(base_cost)

    def metrics(price: Decimal) -> tuple[Decimal | None, Decimal | None]:
        if not has_cost:
            # A06：无成本不给"利润"，让界面显示不可计算
            return None, None
        profit = price - cost_for_profit
        rate = profit / price if price else ZERO
        return profit, rate

    recommended_profit, recommended_rate = metrics(recommended)
    check_price = quoted_price if quoted_price is not None else recommended
    profit, profit_rate = metrics(check_price)

    # ---- 出口退税（可选）：内贸退税额恒为 0 ----
    refund_rate = tax_refund_rate
    if refund_rate is None and is_foreign:
        refund_rate = Decimal(
            str(await settings_service.get_number(session, "export_tax_refund_rate", "ratio", 0.0))
        )
    refund_rate = refund_rate or ZERO

    # 退税：简化按「商品成本 × 退税率」估算（生产企业免抵退更复杂，待财务给准确口径）
    tax_refund_cny = (goods_cost * refund_rate).quantize(Decimal("0.0001")) if refund_rate else ZERO
    tax_refund = tax_refund_cny if not (is_foreign and fx) else (tax_refund_cny / fx).quantize(
        Decimal("0.0001")
    )
    profit_with_refund = (profit + tax_refund) if profit is not None else None
    profit_rate_with_refund = (
        (profit_with_refund / check_price) if profit_with_refund is not None and check_price else None
    )

    # PRD §16 的四条独立触发条件：低于保护价 / 低于本人授权价 / 折扣超权限 / 利润不足
    # 无成本时利润类三条（授权价/负利润/利润率）无从判定，自动停用（A06/D5）
    below_protection = (
        protection_price is not None and check_price < protection_price - Decimal("0.0001")
    )
    below_authorized = bool(
        has_cost
        and floor_from_margin is not None
        and check_price < floor_from_margin - Decimal("0.0001")
    )
    below_profit = bool(has_cost and profit_with_refund is not None and profit_with_refund < ZERO)
    below_margin = bool(
        has_cost
        and profit_rate_with_refund is not None
        and profit_rate_with_refund < min_margin - Decimal("0.000001")
    )

    # 折扣上限（price_permissions.discount_limit，此前只存不用）：
    # 折扣 = 1 - 报价/标准价；角色配了上限且折扣超出 → 需审批。
    # 没配上限（None）不参与判定——与最低利润率的"配了才生效"同口径。
    discount_pct = (
        (1 - check_price / standard_price) * 100
        if standard_price and standard_price > 0 and check_price < standard_price
        else ZERO
    )
    role_discount_limit = await resolve_discount_limit(session, role_codes or [])
    # 库里存的是 0-1 比例（0.3 = 最多让 30%），与百分数口径换算后比较
    discount_exceeded = (
        role_discount_limit is not None
        and discount_pct > Decimal(str(role_discount_limit)) * 100
    )

    approval_required = bool(
        below_protection or below_authorized or below_profit or below_margin or discount_exceeded
    )

    # 绝对底价触发器：不算进 approval_required（它根本不是"需审批"，是"不可批"），
    # 提交审批/核价权限检查会单独立拿这个标志做硬拒绝（D7）。
    # 外币但无汇率时整个口径是混的（上面已 warning"暂按人民币口径"），
    # 此时拿美元数字硬比人民币底价会假阳性硬拒——宁可不判，也不误杀
    below_hard_floor = bool(
        hard_floor_price is not None
        and check_price is not None
        and not (is_foreign and fx is None)
        and check_price < hard_floor_price - Decimal("0.0001")
    )
    if quoted_price is not None and below_hard_floor:
        warnings.append(
            "该报价低于公司绝对底价：任何审批都无法通过，提交会被直接拒绝——"
            "如业务确需该价格，请联系价格管理员调整价格档位（留审计），"
            "或改走样品 / 清库存等特殊通道"
        )

    if quoted_price is not None and approval_required:
        reason = []
        if below_protection:
            reason.append(f"低于最低保护价 ¥{protection_price:.2f}")
        if below_authorized:
            reason.append(
                f"低于你权限内的最低允许价 ¥{floor_price:.2f}"
                f"（授权利润率 {min_margin * 100:.0f}%）"
            )
        if below_profit:
            reason.append("单件利润为负")
        if below_margin:
            reason.append(f"利润率 {profit_rate * 100:.2f}% 低于授权 {min_margin * 100:.0f}%")
        if discount_exceeded:
            reason.append(
                f"折扣 {discount_pct:.1f}% 超过角色上限 {float(role_discount_limit) * 100:.0f}%"
            )
        warnings.append("该报价需要审批：" + "，".join(reason))

    return {
        "sku": {
            "id": sku.id,
            "sku_code": sku.sku_code,
            "name": sku.name,
            "specification": sku.specification,
            "unit": sku.unit,
            "moq": sku.moq,
        },
        "quantity": _f(quantity),
        "has_cost": has_cost,
        "customer_id": customer_id,
        "customer_name": customer.name if customer else None,
        "customer_level": resolved_level,
        # PRD §13 的输入项回显，便于界面上让用户看清"这次是按什么算的"
        "inputs": {
            "country": country,
            "package_type": package_type,
            "shipping_method": shipping_method,
            "payment_terms": payment_terms,
            "target_margin": _f(target_margin),
            "target_profit_amount": _f(target_profit_amount),
        },
        "cost": {
            "purchase_cost": _f(purchase),
            "production_cost": _f(production),
            "package_cost": _f(package),
            "processing_cost": _f(processing),
            "goods_cost": _f(goods_cost),
            "logistics_cost": _f(logistics),
            "base_cost": _f(base_cost),
            "source": "价格中心成本表（生效中）" if cost else "无成本记录",
        },
        "price_rule": serialize_price_rule(rule, sku.sku_code) if rule else None,
        "customer_price_rule": serialize_customer_price(customer_rule, sku.sku_code) if customer_rule else None,
        "target_margin": _f(margin),
        "standard_price": _f(standard_price),
        "recommended_price": _f(recommended),
        "recommended_range": [_f(recommended_range[0]), _f(recommended_range[1])],
        # 三个底价分开给：保护价是公司口径，minimum_price 是你权限内的口径，
        # hard_floor_price 是不可审批的绝对底线（D7）；数值对无 price:manage 者脱敏
        "protection_price": _f(protection_price),
        "minimum_price": _f(floor_price),
        "hard_floor_price": _f(hard_floor_price),
        "authorized_min_margin": _f(min_margin),
        "can_approve": can_approve,
        # 没传报价时 check_price 只是建议价的别名，回显出去等于把成本推算价原样给出去
        "quoted_price": _f(check_price) if quoted_price is not None else None,
        "currency": currency.upper(),
        "exchange_rate": _f(fx),
        "cost_in_quote_currency": _f(cost_for_profit),
        "tax_refund_rate": _f(refund_rate),
        "tax_refund": _f(tax_refund),
        "profit_with_refund": _f(profit_with_refund),
        "profit_rate_with_refund": _f(profit_rate_with_refund),
        "profit": _f(profit),
        "profit_rate": _f(profit_rate),
        "recommended_profit": _f(recommended_profit),
        "recommended_profit_rate": _f(recommended_rate),
        "approval_required": approval_required,
        "approval_triggers": {
            "below_protection_price": below_protection,
            "below_authorized_price": below_authorized,
            "negative_profit": below_profit,
            "below_authorized_margin": below_margin,
            "below_hard_floor": below_hard_floor,
        },
        "warnings": warnings,
    }


async def price_summary(session: AsyncSession, sku_id: int) -> dict:
    """SKU 价格概览：生效成本 + 全部价格规则，供价格中心页面展示。"""
    sku = await session.get(Sku, sku_id)
    if sku is None or sku.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "SKU 不存在", 404)
    product = await session.get(Product, sku.product_id)
    cost = await get_effective_cost(session, sku_id)
    rules = (
        await session.execute(
            select(PriceRule).where(PriceRule.sku_id == sku_id).order_by(PriceRule.id.asc())
        )
    ).scalars().all()
    return {
        "sku": {
            "id": sku.id,
            "sku_code": sku.sku_code,
            "specification": sku.specification,
            "product_name": product.name if product else None,
        },
        "cost": serialize_cost(cost, sku.sku_code) if cost else None,
        "price_rules": [serialize_price_rule(rule, sku.sku_code) for rule in rules],
    }


__all__ = [
    "calculate_price",
    "estimate_logistics",
    "find_customer_price",
    "find_price_rule",
    "get_effective_cost",
    "price_summary",
    "resolve_min_margin",
    "serialize_cost",
    "serialize_customer_price",
    "serialize_logistics_rate",
    "serialize_permission",
    "serialize_price_rule",
]
