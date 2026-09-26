"""价格中心与核价接口（对齐 03-API §16 ~ §19）。"""

from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.response import ok, page_data, paginate
from app.modules.customer.model import Customer
from app.modules.pricing import service as svc
from app.modules.pricing.model import (
    CustomerPriceRule,
    ExchangeRate,
    LogisticsRate,
    PricePermission,
    PriceRule,
    ProductCost,
)
from app.modules.pricing.schema import (
    CostCreate,
    CostUpdate,
    CustomerPriceCreate,
    ExchangeRateCreate,
    LogisticsRateCreate,
    PricePermissionCreate,
    PricePermissionUpdate,
    PriceRuleCreate,
    PriceRuleUpdate,
    PricingRequest,
)
from app.modules.product.model import Product, Sku
from app.modules.user.model import Role

router = APIRouter(tags=["Pricing"])


async def _sku_code_map(session: AsyncSession, sku_ids: list[int]) -> dict[int, str]:
    if not sku_ids:
        return {}
    rows = (await session.execute(select(Sku.id, Sku.sku_code).where(Sku.id.in_(sku_ids)))).all()
    return {int(sid): code for sid, code in rows}


# ---------------------------------------------------------------- 成本

@router.get("/skus/{sku_id}/costs")
async def list_costs(
    sku_id: int,
    _: CurrentUser = Depends(require_permission("product:view")),
    session: AsyncSession = Depends(get_db),
):
    sku = await session.get(Sku, sku_id)
    if sku is None:
        raise AppError(ErrorCode.NOT_FOUND, "SKU 不存在", 404)
    rows = (
        await session.execute(
            select(ProductCost)
            .where(ProductCost.sku_id == sku_id)
            .order_by(ProductCost.effective_from.desc(), ProductCost.id.desc())
        )
    ).scalars().all()
    return ok([svc.serialize_cost(row, sku.sku_code) for row in rows])


@router.post("/skus/{sku_id}/costs")
async def create_cost(
    sku_id: int,
    payload: CostCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("price:manage")),
    session: AsyncSession = Depends(get_db),
):
    sku = await session.get(Sku, sku_id)
    if sku is None:
        raise AppError(ErrorCode.NOT_FOUND, "SKU 不存在", 404)
    cost = ProductCost(**payload.model_dump(), sku_id=sku_id, created_by=user.id)
    session.add(cost)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="create",
        business_type="product_cost",
        business_id=cost.id,
        after=svc.serialize_cost(cost, sku.sku_code),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_cost(cost, sku.sku_code), "成本已保存")


@router.patch("/costs/{cost_id}")
async def update_cost(
    cost_id: int,
    payload: CostUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("price:manage")),
    session: AsyncSession = Depends(get_db),
):
    cost = await session.get(ProductCost, cost_id)
    if cost is None:
        raise AppError(ErrorCode.NOT_FOUND, "成本记录不存在", 404)
    before = svc.serialize_cost(cost)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(cost, field, value)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="update",
        business_type="product_cost",
        business_id=cost.id,
        before=before,
        after=svc.serialize_cost(cost),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_cost(cost), "已保存")


@router.post("/costs/{cost_id}/expire")
async def expire_cost(
    cost_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("price:manage")),
    session: AsyncSession = Depends(get_db),
):
    cost = await session.get(ProductCost, cost_id)
    if cost is None:
        raise AppError(ErrorCode.NOT_FOUND, "成本记录不存在", 404)
    cost.effective_to = datetime.now(UTC).date()
    await write_audit(
        session,
        operator_id=user.id,
        action="expire",
        business_type="product_cost",
        business_id=cost.id,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_cost(cost), "该成本已失效")


# ---------------------------------------------------------------- 价格规则

@router.get("/price-rules")
async def list_price_rules(
    sku_id: int | None = None,
    keyword: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    _: CurrentUser = Depends(require_permission("product:view")),
    session: AsyncSession = Depends(get_db),
):
    stmt = select(PriceRule).join(Sku, Sku.id == PriceRule.sku_id)
    if sku_id:
        stmt = stmt.where(PriceRule.sku_id == sku_id)
    if keyword:
        stmt = stmt.where(Sku.sku_code.ilike(f"%{keyword.strip()}%"))
    stmt = stmt.order_by(PriceRule.id.desc())
    rows, total = await paginate(session, stmt, page, page_size)
    codes = await _sku_code_map(session, [rule.sku_id for rule in rows])
    items = [svc.serialize_price_rule(rule, codes.get(rule.sku_id)) for rule in rows]
    return ok(page_data(items, total, page, page_size))


@router.post("/price-rules")
async def create_price_rule(
    payload: PriceRuleCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("price:manage")),
    session: AsyncSession = Depends(get_db),
):
    sku = await session.get(Sku, payload.sku_id)
    if sku is None:
        raise AppError(ErrorCode.NOT_FOUND, "SKU 不存在", 404)
    rule = PriceRule(**payload.model_dump(), status="active")
    session.add(rule)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="create",
        business_type="price_rule",
        business_id=rule.id,
        after=svc.serialize_price_rule(rule, sku.sku_code),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_price_rule(rule, sku.sku_code), "价格规则已创建")


@router.patch("/price-rules/{rule_id}")
async def update_price_rule(
    rule_id: int,
    payload: PriceRuleUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("price:manage")),
    session: AsyncSession = Depends(get_db),
):
    rule = await session.get(PriceRule, rule_id)
    if rule is None:
        raise AppError(ErrorCode.NOT_FOUND, "价格规则不存在", 404)
    before = svc.serialize_price_rule(rule)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(rule, field, value)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="update",
        business_type="price_rule",
        business_id=rule.id,
        before=before,
        after=svc.serialize_price_rule(rule),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_price_rule(rule), "已保存")


@router.delete("/price-rules/{rule_id}")
async def delete_price_rule(
    rule_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("price:manage")),
    session: AsyncSession = Depends(get_db),
):
    rule = await session.get(PriceRule, rule_id)
    if rule is None:
        raise AppError(ErrorCode.NOT_FOUND, "价格规则不存在", 404)
    rule.status = "disabled"
    await write_audit(
        session,
        operator_id=user.id,
        action="disable",
        business_type="price_rule",
        business_id=rule.id,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(None, "价格规则已停用")


# ---------------------------------------------------------------- 客户特殊价

@router.get("/customer-price-rules")
async def list_customer_price_rules(
    customer_id: int | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    _: CurrentUser = Depends(require_permission("product:view")),
    session: AsyncSession = Depends(get_db),
):
    stmt = select(CustomerPriceRule)
    if customer_id:
        stmt = stmt.where(CustomerPriceRule.customer_id == customer_id)
    rows, total = await paginate(session, stmt.order_by(CustomerPriceRule.id.desc()), page, page_size)
    codes = await _sku_code_map(session, [rule.sku_id for rule in rows])
    customer_ids = {rule.customer_id for rule in rows}
    names: dict[int, str] = {}
    if customer_ids:
        name_rows = (
            await session.execute(
                select(Customer.id, Customer.name).where(Customer.id.in_(customer_ids))
            )
        ).all()
        names = {int(cid): name for cid, name in name_rows}
    items = [
        svc.serialize_customer_price(rule, codes.get(rule.sku_id), names.get(rule.customer_id))
        for rule in rows
    ]
    return ok(page_data(items, total, page, page_size))


@router.post("/customer-price-rules")
async def create_customer_price_rule(
    payload: CustomerPriceCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("price:manage")),
    session: AsyncSession = Depends(get_db),
):
    rule = CustomerPriceRule(**payload.model_dump())
    session.add(rule)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="create",
        business_type="customer_price_rule",
        business_id=rule.id,
        after=svc.serialize_customer_price(rule),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_customer_price(rule), "客户特殊价已创建")


@router.delete("/customer-price-rules/{rule_id}")
async def delete_customer_price_rule(
    rule_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("price:manage")),
    session: AsyncSession = Depends(get_db),
):
    rule = await session.get(CustomerPriceRule, rule_id)
    if rule is None:
        raise AppError(ErrorCode.NOT_FOUND, "客户特殊价不存在", 404)
    await session.delete(rule)
    await write_audit(
        session,
        operator_id=user.id,
        action="delete",
        business_type="customer_price_rule",
        business_id=rule_id,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(None, "已删除")


# ---------------------------------------------------------------- 价格权限

@router.get("/price-permissions")
async def list_price_permissions(
    _: CurrentUser = Depends(require_permission("product:view")),
    session: AsyncSession = Depends(get_db),
):
    rows = (
        await session.execute(
            select(PricePermission, Role.name, Role.code)
            .join(Role, Role.id == PricePermission.role_id)
            .order_by(PricePermission.id.asc())
        )
    ).all()
    return ok(
        [
            {**svc.serialize_permission(permission, role_name), "role_code": role_code}
            for permission, role_name, role_code in rows
        ]
    )


@router.put("/price-permissions/{role_id}")
async def upsert_price_permission(
    role_id: int,
    payload: PricePermissionUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("price:manage")),
    session: AsyncSession = Depends(get_db),
):
    role = await session.get(Role, role_id)
    if role is None:
        raise AppError(ErrorCode.NOT_FOUND, "角色不存在", 404)
    permission = (
        await session.execute(
            select(PricePermission).where(PricePermission.role_id == role_id)
        )
    ).scalar_one_or_none()
    if permission is None:
        permission = PricePermission(role_id=role_id, status="active")
        session.add(permission)
    permission.minimum_margin = payload.minimum_margin
    permission.discount_limit = payload.discount_limit
    permission.can_approve = payload.can_approve
    permission.remark = payload.remark
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="update",
        business_type="price_permission",
        business_id=permission.id,
        after=svc.serialize_permission(permission, role.name),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_permission(permission, role.name), "价格权限已保存")


# ---------------------------------------------------------------- 运费费率

@router.get("/logistics/rates")
async def list_logistics_rates(
    _: CurrentUser = Depends(require_permission("product:view")),
    session: AsyncSession = Depends(get_db),
):
    rows = (
        await session.execute(select(LogisticsRate).order_by(LogisticsRate.id.asc()))
    ).scalars().all()
    return ok([svc.serialize_logistics_rate(rate) for rate in rows])


@router.post("/logistics/rates")
async def create_logistics_rate(
    payload: LogisticsRateCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("price:manage")),
    session: AsyncSession = Depends(get_db),
):
    rate = LogisticsRate(**payload.model_dump(), status="active")
    session.add(rate)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="create",
        business_type="logistics_rate",
        business_id=rate.id,
        after=svc.serialize_logistics_rate(rate),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_logistics_rate(rate), "运费费率已创建")


# ---------------------------------------------------------------- 核价

@router.post("/pricing/calculate")
async def calculate(
    payload: PricingRequest,
    user: CurrentUser = Depends(require_permission("product:view")),
    session: AsyncSession = Depends(get_db),
):
    return ok(
        await svc.calculate_price(
            session,
            sku_id=payload.sku_id,
            quantity=payload.quantity,
            customer_id=payload.customer_id,
            logistics_cost=payload.logistics_cost,
            target_margin=payload.target_margin,
            target_profit_amount=payload.target_profit_amount,
            quoted_price=payload.quoted_price,
            role_codes=user.roles,
            currency=payload.currency,
            exchange_rate=payload.exchange_rate,
            tax_refund_rate=payload.tax_refund_rate,
            customer_level=payload.customer_level,
            country=payload.country,
            package_type=payload.package_type,
            shipping_method=payload.shipping_method,
            payment_terms=payload.payment_terms,
        )
    )


@router.post("/pricing/batch-calculate")
async def batch_calculate(
    items: list[PricingRequest],
    user: CurrentUser = Depends(require_permission("product:view")),
    session: AsyncSession = Depends(get_db),
):
    results = []
    for item in items:
        results.append(
            await svc.calculate_price(
                session,
                sku_id=item.sku_id,
                quantity=item.quantity,
                customer_id=item.customer_id,
                logistics_cost=item.logistics_cost,
                target_margin=item.target_margin,
                target_profit_amount=item.target_profit_amount,
                quoted_price=item.quoted_price,
                role_codes=user.roles,
                currency=item.currency,
                exchange_rate=item.exchange_rate,
                tax_refund_rate=item.tax_refund_rate,
                customer_level=item.customer_level,
                country=item.country,
                package_type=item.package_type,
                shipping_method=item.shipping_method,
                payment_terms=item.payment_terms,
            )
        )
    return ok(results)


@router.get("/skus/{sku_id}/price-summary")
async def price_summary(
    sku_id: int,
    _: CurrentUser = Depends(require_permission("product:view")),
    session: AsyncSession = Depends(get_db),
):
    return ok(await svc.price_summary(session, sku_id))


@router.get("/pricing/sku-options")
async def products_for_pricing(
    _: CurrentUser = Depends(require_permission("product:view")),
    session: AsyncSession = Depends(get_db),
):
    """给价格中心与核价页的下拉用：SKU + 所属产品名，一次取全。

    注意：不要挂在 /products/xxx 下面——那会被 /products/{product_id} 抢先匹配。
    """
    rows = (
        await session.execute(
            select(Sku, Product.name)
            .join(Product, Product.id == Sku.product_id)
            .where(Sku.deleted_at.is_(None), Sku.status == "active")
            .order_by(Sku.id.asc())
        )
    ).all()
    return ok(
        [
            {
                "id": sku.id,
                "sku_code": sku.sku_code,
                "specification": sku.specification,
                "product_name": product_name,
                "moq": sku.moq,
                "unit": sku.unit,
                "weight": float(sku.weight) if sku.weight is not None else None,
            }
            for sku, product_name in rows
        ]
    )


__all__ = ["Decimal", "router"]


# ------------------------- 03-API §16 补齐：汇率、单条查询与价格权限创建
#
# 这一组多数是"文档有路径、代码有等价能力"的补齐，但**汇率是真缺口**：
# 汇率表此前只有模型没有接口，外贸报价要么报"没有维护汇率"要么只能改库。


def _serialize_exchange_rate(row: ExchangeRate) -> dict:
    return {
        "id": row.id,
        "base_currency": row.base_currency,
        "quote_currency": row.quote_currency,
        "rate": float(row.rate),
        "source": row.source,
        "effective_at": row.effective_at,
    }


@router.get("/exchange-rates")
async def list_exchange_rates(
    quote_currency: str | None = None,
    _: CurrentUser = Depends(require_permission("product:view")),
    session: AsyncSession = Depends(get_db),
):
    """汇率列表。按生效时间倒序，同币种最新的在最前。"""
    stmt = select(ExchangeRate)
    if quote_currency:
        stmt = stmt.where(ExchangeRate.quote_currency == quote_currency.upper())
    rows = (
        await session.execute(
            stmt.order_by(ExchangeRate.effective_at.desc(), ExchangeRate.id.desc())
        )
    ).scalars().all()
    return ok([_serialize_exchange_rate(row) for row in rows])


@router.post("/exchange-rates")
async def create_exchange_rate(
    payload: ExchangeRateCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("price:manage")),
    session: AsyncSession = Depends(get_db),
):
    """新增一条汇率。

    允许同一币种有多条（按 effective_at 取最新）—— 汇率是**时点数据**，
    覆盖历史会让已经落快照的旧报价与当时的汇率对不上。
    所以这里不做"同币种只留一条"的约束。
    """
    base = (payload.base_currency or "CNY").strip().upper()
    quote = payload.quote_currency.strip().upper()
    if not quote:
        raise AppError(ErrorCode.PARAM_ERROR, "币种不能为空白", 422)
    if quote == base:
        raise AppError(ErrorCode.PARAM_ERROR, f"{quote} 与基准币种相同，不需要汇率", 422)
    if payload.rate <= 0:
        raise AppError(ErrorCode.PARAM_ERROR, "汇率必须大于 0", 422)

    row = ExchangeRate(
        base_currency=base,
        quote_currency=quote,
        rate=payload.rate,
        source=payload.source or "手工维护",
        effective_at=payload.effective_at or datetime.now(UTC),
    )
    session.add(row)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="create",
        business_type="exchange_rate",
        business_id=row.id,
        after=_serialize_exchange_rate(row),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(_serialize_exchange_rate(row), f"已维护 {base} → {quote} 汇率")


@router.get("/costs/{cost_id}")
async def get_cost(
    cost_id: int,
    _: CurrentUser = Depends(require_permission("product:view")),
    session: AsyncSession = Depends(get_db),
):
    """单条成本记录（03-API §16）。"""
    row = await session.get(ProductCost, cost_id)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "成本记录不存在", 404)
    return ok(svc.serialize_cost(row))


@router.get("/skus/{sku_id}/cost-history")
async def cost_history(
    sku_id: int,
    _: CurrentUser = Depends(require_permission("product:view")),
    session: AsyncSession = Depends(get_db),
):
    """某 SKU 的成本变更历史。

    与 `/skus/{id}/costs` 的区别：那个是"成本记录列表"（含未来生效的），
    这个按生效时间倒序呈现**变更轨迹**，并标出当前生效的是哪一条 ——
    排查"为什么这次核价用的成本不一样"时看的就是它。
    """
    sku = await session.get(Sku, sku_id)
    if sku is None or sku.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "SKU 不存在", 404)
    rows = (
        await session.execute(
            select(ProductCost)
            .where(ProductCost.sku_id == sku_id)
            .order_by(ProductCost.effective_from.desc(), ProductCost.id.desc())
        )
    ).scalars().all()
    effective = await svc.get_effective_cost(session, sku_id)
    return ok(
        {
            "sku_id": sku_id,
            "sku_code": sku.sku_code,
            "effective_cost_id": effective.id if effective else None,
            "history": [svc.serialize_cost(row) for row in rows],
        }
    )


@router.get("/price-rules/{rule_id}")
async def get_price_rule(
    rule_id: int,
    _: CurrentUser = Depends(require_permission("product:view")),
    session: AsyncSession = Depends(get_db),
):
    """单条价格规则（03-API §16）。"""
    row = await session.get(PriceRule, rule_id)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "价格规则不存在", 404)
    codes = await _sku_code_map(session, [row.sku_id])
    return ok(svc.serialize_price_rule(row, codes.get(row.sku_id)))


@router.post("/price-permissions")
async def create_price_permission(
    payload: PricePermissionCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("price:manage")),
    session: AsyncSession = Depends(get_db),
):
    """新增价格权限（03-API §16 的 POST 写法）。

    与已有的 `PUT /price-permissions/{role_id}` 是**同一件事**：
    一个角色只有一条价格权限，所以"新增"遇到已存在的就直接拒绝，
    让调用方改用 PUT 更新 —— 否则两次 POST 会悄悄覆盖前一次的配置。
    """
    role = await session.get(Role, payload.role_id)
    if role is None:
        raise AppError(ErrorCode.NOT_FOUND, "角色不存在", 404)
    existing = (
        await session.execute(
            select(PricePermission).where(PricePermission.role_id == payload.role_id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise AppError(
            ErrorCode.DUPLICATE,
            f"角色「{role.name}」已有价格权限，请用 PUT /price-permissions/{payload.role_id} 更新",
            409,
        )

    permission = PricePermission(
        role_id=payload.role_id,
        minimum_margin=payload.minimum_margin,
        discount_limit=payload.discount_limit,
        can_approve=payload.can_approve,
        remark=payload.remark,
        status="active",
    )
    session.add(permission)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="create",
        business_type="price_permission",
        business_id=permission.id,
        after=svc.serialize_permission(permission, role.name),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_permission(permission, role.name), "价格权限已创建")


@router.patch("/price-permissions/{role_id}")
async def patch_price_permission(
    role_id: int,
    payload: PricePermissionUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("price:manage")),
    session: AsyncSession = Depends(get_db),
):
    """局部更新价格权限（03-API §16 的 PATCH 写法）。

    只改传入的字段；与 PUT 的区别是 PUT 会把没传的字段也重置为默认值，
    PATCH 不会 —— 这正是两个方法并存的意义，所以这里单独实现而不是转发给 PUT。
    """
    permission = (
        await session.execute(
            select(PricePermission).where(PricePermission.role_id == role_id)
        )
    ).scalar_one_or_none()
    if permission is None:
        raise AppError(
            ErrorCode.NOT_FOUND,
            f"角色 {role_id} 还没有价格权限，请先用 POST /price-permissions 创建",
            404,
        )
    role = await session.get(Role, role_id)
    before = svc.serialize_permission(permission, role.name if role else None)
    data = payload.model_dump(exclude_unset=True, exclude_none=True)
    for field, value in data.items():
        setattr(permission, field, value)
    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="update",
        business_type="price_permission",
        business_id=permission.id,
        before=before,
        after=svc.serialize_permission(permission, role.name if role else None),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(
        svc.serialize_permission(permission, role.name if role else None), "已保存"
    )
