"""价格中心与核价接口（对齐 03-API §16 ~ §19）。"""

from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import Text, cast, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.database import get_db
from app.core.deps import CurrentUser, client_ip, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.importing import RowErrors
from app.core.response import ok, page_data, paginate
from app.core.timebase import today_business
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
    CustomerPriceUpdate,
    ExchangeRateCreate,
    LogisticsRateCreate,
    PricePermissionCreate,
    PricePermissionCheck,
    PricePermissionUpdate,
    PricingSimulation,
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
    _: CurrentUser = Depends(require_permission("price:manage")),
    session: AsyncSession = Depends(get_db),
):
    """SKU 成本明细。成本是公司敏感数据（方案 §4.3）：只对价格管理员开放，
    不能停留在 product:view——那是产品资料的查看权限，不是成本的。"""
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
    # 与导入同一套校验（第七批 7.2/7.4：导入、预览、页面维护不能各校验一套，
    # 否则"页面拦得住、导入绕得过"永远修不干净）
    values = payload.model_dump()
    filled = [
        key
        for key in ("purchase_cost", "production_cost", "package_cost", "processing_cost")
        if values.get(key) is not None
    ]
    if not filled:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            "四项成本都为空，不能保存：这会造出一条全零成本，核价会据此算出假毛利。"
            "请至少填一项；确实为零请显式填 0",
            422,
        )
    if (
        payload.effective_to is not None
        and payload.effective_to < payload.effective_from
    ):
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"生效截止日（{payload.effective_to.isoformat()}）不能早于"
            f"生效起始日（{payload.effective_from.isoformat()}）",
            422,
        )
    cost = ProductCost(**values, sku_id=sku_id, created_by=user.id)
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
    changes = payload.model_dump(exclude_unset=True)
    # 允许把某一项改成"未提供"（NULL），但不允许把四项一起清空 ——
    # 那等于把一条有内容的成本改成"什么都不知道"，核价却仍当它成本已知
    cost_columns = ("purchase_cost", "production_cost", "package_cost", "processing_cost")
    if any(key in changes for key in cost_columns):
        merged = {
            key: (changes[key] if key in changes else getattr(cost, key))
            for key in cost_columns
        }
        if all(value is None for value in merged.values()):
            raise AppError(
                ErrorCode.PARAM_ERROR,
                "四项成本不能全部清空：核价会把这条成本当成已知的零成本。"
                "如该成本不再适用，请改用「失效」而不是清空数值",
                422,
            )
    effective_from = changes.get("effective_from", cost.effective_from)
    effective_to = changes.get("effective_to", cost.effective_to)
    if effective_from is not None and effective_to is not None and effective_to < effective_from:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"生效截止日（{effective_to.isoformat()}）不能早于"
            f"生效起始日（{effective_from.isoformat()}）",
            422,
        )
    for field, value in changes.items():
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
    # 截止日期按**业务日期**写（第九批复审 §9.10）：取价那边判"这条成本还生不生效"
    # 用的是北京时间，这里若写 UTC 的今天，凌晨做的失效操作会少一天。
    cost.effective_to = today_business()
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
    user: CurrentUser = Depends(require_permission("product:view")),
    session: AsyncSession = Depends(get_db),
):
    stmt = select(PriceRule).join(Sku, Sku.id == PriceRule.sku_id)
    # 软删 SKU 上的价目不再列出（§7.3 复审：与专属价列表同一份有效引用条件）
    stmt = stmt.where(Sku.deleted_at.is_(None))
    if sku_id:
        stmt = stmt.where(PriceRule.sku_id == sku_id)
    if keyword:
        stmt = stmt.where(Sku.sku_code.ilike(f"%{keyword.strip()}%"))
    stmt = stmt.order_by(PriceRule.id.desc())
    rows, total = await paginate(session, stmt, page, page_size)
    codes = await _sku_code_map(session, [rule.sku_id for rule in rows])
    # 最低保护价按权限隐藏（与核价/查价同判据；`_` 用不上权限，这里要判）
    can_see_cost = user.has("price:manage")
    items = [
        svc.serialize_price_rule(rule, codes.get(rule.sku_id), can_see_cost=can_see_cost)
        for rule in rows
    ]
    return ok(page_data(items, total, page, page_size))


def _reject_bad_price_rule(**values) -> None:
    """数值 / 区间 / 有效期有问题就一次性拒绝（判据与导入**共用同一份**）。

    为什么要在维护接口也校验（审查 2026-10-07 实测）：导入会被拦的数据，
    换 `POST/PATCH /price-rules` 就能存下 —— 负指导价、数量下限大于上限、
    有效期倒置、利润率 2 全都能落库，而取价/核价读的就是这些数字。
    """
    errs = RowErrors(0, "")
    svc.check_price_rule_values(errs, **values)
    if errs:
        raise AppError(ErrorCode.PARAM_ERROR, "；".join(errs.reasons), 400)


@router.post("/price-rules")
async def create_price_rule(
    payload: PriceRuleCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("price:manage")),
    session: AsyncSession = Depends(get_db),
):
    _reject_bad_price_rule(
        min_qty=payload.min_qty,
        max_qty=payload.max_qty,
        standard_price=payload.standard_price,
        guide_price=payload.guide_price,
        minimum_price=payload.minimum_price,
        target_margin=payload.target_margin,
        effective_from=payload.effective_from,
        effective_to=payload.effective_to,
    )
    sku = await session.get(Sku, payload.sku_id)
    if sku is None:
        raise AppError(ErrorCode.NOT_FOUND, "SKU 不存在", 404)
    # 冲突检查（方案 §4.1）：同 SKU 同等级的数量区间与有效期都重叠时拒绝，
    # 宁可让人处理冲突资料，也不能让取价随机命中一条
    conflict = await svc.find_price_rule_conflict(
        session,
        sku_id=payload.sku_id,
        customer_level=payload.customer_level,
        min_qty=payload.min_qty,
        max_qty=payload.max_qty,
        effective_from=payload.effective_from,
        effective_to=payload.effective_to,
    )
    if conflict is not None:
        raise AppError(
            ErrorCode.DUPLICATE,
            f"与现有规则 #{conflict.id}（数量 {conflict.min_qty}-{conflict.max_qty or '∞'}、"
            f"有效期 {conflict.effective_from or '∞'} ~ {conflict.effective_to or '∞'}）区间重叠，请调整数量或有效期",
        )
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
    # 校验**合并后的完整记录**（2026-10-07 修）：只校验传进来的那几个字段不够 ——
    # "把数量下限改到上限之上"这种，单看每个传入字段都是合法的。
    try:
        _reject_bad_price_rule(
            min_qty=rule.min_qty,
            max_qty=rule.max_qty,
            standard_price=rule.standard_price,
            guide_price=rule.guide_price,
            minimum_price=rule.minimum_price,
            target_margin=rule.target_margin,
            effective_from=rule.effective_from,
            effective_to=rule.effective_to,
        )
    except AppError:
        await session.rollback()
        raise
    # 改动后仍不能与其他规则重叠（排除自己）
    conflict = await svc.find_price_rule_conflict(
        session,
        sku_id=rule.sku_id,
        customer_level=rule.customer_level,
        min_qty=rule.min_qty,
        max_qty=rule.max_qty,
        effective_from=rule.effective_from,
        effective_to=rule.effective_to,
        exclude_id=rule.id,
    )
    if conflict is not None:
        await session.rollback()
        raise AppError(
            ErrorCode.DUPLICATE,
            f"与现有规则 #{conflict.id}（数量 {conflict.min_qty}-{conflict.max_qty or '∞'}）区间重叠，请调整数量或有效期",
        )
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
    user: CurrentUser = Depends(require_permission("product:view")),
    session: AsyncSession = Depends(get_db),
):
    """客户特殊价列表（03-API §17）。

    数据范围（审查 2026-10-07 第二轮实测）：只看得到"客户在我范围内"（或无负责人的
    公海客户）的专属价。原来直接全表查、只按 customer_id 过滤，还把客户名称和
    专属价一起返回 —— 业务员翻一页就把全公司的特殊价看光了。
    范围在**分页与计数之前**生效（条件进 SQL，不是取回来再筛）。

    §7.3 复审（第三轮）又补两条：
    - **软删客户/SKU 的规则不再列出**（客户侧条件已在 `customer_scope_condition` 里
      加了 `deleted_at`，SKU 侧在这里补）—— 指定客户分支原本会拒绝已删客户，
      只有这条分支漏了；
    - **最低保护价按权限隐藏**：与核价/查价同一判据（`price:manage`）。
    """
    stmt = select(CustomerPriceRule)
    # 软删 SKU 上的专属价同样是"失效引用"，与指定客户分支同口径
    stmt = stmt.where(
        CustomerPriceRule.sku_id.in_(select(Sku.id).where(Sku.deleted_at.is_(None)))
    )
    if customer_id:
        # 指定客户也不能绕过：走与增删改**同一份判据**
        await svc.ensure_customer_in_scope_for_price(
            session, user=user, customer_id=customer_id
        )
        stmt = stmt.where(CustomerPriceRule.customer_id == customer_id)
    else:
        from app.core.data_scope import scoped_owner_ids

        condition = svc.customer_scope_condition(await scoped_owner_ids(session, user))
        if condition is not None:
            visible_customers = select(Customer.id).where(condition)
            stmt = stmt.where(CustomerPriceRule.customer_id.in_(visible_customers))
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
        svc.serialize_customer_price(
            rule,
            codes.get(rule.sku_id),
            names.get(rule.customer_id),
            can_see_cost=user.has("price:manage"),
        )
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
    # 引用与范围（2026-10-07 修）：客户与 SKU 必须真实存在且未删，客户还要在数据范围内。
    # 原来直接 `CustomerPriceRule(**payload.model_dump())` —— 一个客户权限查询都没有，
    # 于是"导入被拦、换个接口就能给任意客户（含已删、别人家的）建专属价"。
    await svc.ensure_customer_price_targets(
        session, user=user, customer_id=payload.customer_id, sku_id=payload.sku_id
    )
    _reject_bad_price_rule(
        min_qty=payload.min_qty,
        max_qty=payload.max_qty,
        agreed_price=payload.agreed_price,
        minimum_price=payload.minimum_price,
        effective_from=payload.effective_from,
        effective_to=payload.effective_to,
    )
    rule = CustomerPriceRule(**payload.model_dump())
    # 冲突检查：同客户同 SKU 的数量/有效期重叠直接拒绝（方案 §4.1）
    conflict = await svc.find_customer_price_conflict(
        session,
        customer_id=payload.customer_id,
        sku_id=payload.sku_id,
        min_qty=payload.min_qty,
        max_qty=payload.max_qty,
        effective_from=payload.effective_from,
        effective_to=payload.effective_to,
    )
    if conflict is not None:
        raise AppError(
            ErrorCode.DUPLICATE,
            f"该客户此 SKU 已有规则 #{conflict.id}（数量 {conflict.min_qty}-{conflict.max_qty or '∞'}）区间重叠，请调整数量或有效期",
        )
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


@router.patch("/customer-price-rules/{rule_id}")
async def update_customer_price_rule(
    rule_id: int,
    payload: CustomerPriceUpdate,
    request: Request,
    user: CurrentUser = Depends(require_permission("price:manage")),
    session: AsyncSession = Depends(get_db),
):
    """改客户特殊价（03-API §15）。

    部分更新：只改传进来的字段。不支持改 customer_id / sku_id ——
    那等于换一条规则，删除重建更清楚。
    """
    rule = await session.get(CustomerPriceRule, rule_id)
    if rule is None:
        raise AppError(ErrorCode.NOT_FOUND, "客户特殊价不存在", 404)

    before = svc.serialize_customer_price(rule)
    changes = payload.model_dump(exclude_unset=True)

    if changes.get("min_qty") is not None:
        rule.min_qty = changes["min_qty"]
    if "max_qty" in changes:
        rule.max_qty = changes["max_qty"]
    if changes.get("agreed_price") is not None:
        rule.agreed_price = changes["agreed_price"]
    if "minimum_price" in changes:
        rule.minimum_price = changes["minimum_price"]
    if "effective_from" in changes:
        rule.effective_from = changes["effective_from"]
    if "effective_to" in changes:
        rule.effective_to = changes["effective_to"]
    if "remark" in changes:
        rule.remark = changes["remark"]

    # 数据范围与引用、数值与区间、有效期 —— 全部按**合并后的完整记录**校验
    # （2026-10-07 修）。原来这里只查 min > max 一条：改 max_qty 把区间弄倒置、
    # 把约定价填成负数、改有效期弄倒置，都能存下去（静默失效比报错难查得多）。
    try:
        await svc.ensure_customer_price_targets(
            session, user=user, customer_id=rule.customer_id, sku_id=rule.sku_id
        )
        _reject_bad_price_rule(
            min_qty=rule.min_qty,
            max_qty=rule.max_qty,
            agreed_price=rule.agreed_price,
            minimum_price=rule.minimum_price,
            effective_from=rule.effective_from,
            effective_to=rule.effective_to,
        )
    except AppError:
        await session.rollback()
        raise

    # 改动后仍不能与其他规则重叠（排除自己）
    conflict = await svc.find_customer_price_conflict(
        session,
        customer_id=rule.customer_id,
        sku_id=rule.sku_id,
        min_qty=rule.min_qty,
        max_qty=rule.max_qty,
        effective_from=rule.effective_from,
        effective_to=rule.effective_to,
        exclude_id=rule.id,
    )
    if conflict is not None:
        await session.rollback()
        raise AppError(
            ErrorCode.DUPLICATE,
            f"该客户此 SKU 已有规则 #{conflict.id}（数量 {conflict.min_qty}-{conflict.max_qty or '∞'}）区间重叠，请调整数量或有效期",
        )

    await session.flush()
    await write_audit(
        session,
        operator_id=user.id,
        action="update",
        business_type="customer_price_rule",
        business_id=rule.id,
        before=before,
        after=svc.serialize_customer_price(rule),
        ip=client_ip(request),
    )
    await session.commit()
    return ok(svc.serialize_customer_price(rule), "已保存")


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
    # 与新增 / 修改 / 列表**同一份判据**：这条规则属于哪个客户，那个客户就得在
    # 操作人的数据范围内。原来只查 price:manage —— "本人仅自己"范围的业务员拿 id
    # 就能删掉别人客户的专属价，删完那个客户后续报价改用通用价，等于悄悄改价。
    # 必须在 delete 与审计**之前**判：越权时原规则仍在、也不许留下"删除成功"的痕迹。
    await svc.ensure_customer_in_scope_for_price(
        session, user=user, customer_id=rule.customer_id
    )
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


@router.delete("/logistics/rates/{rate_id}")
async def delete_logistics_rate(
    rate_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("price:manage")),
    session: AsyncSession = Depends(get_db),
):
    """删除运费费率（此前该端点不存在：配错费率删不掉，测试清理也一直空转）。"""
    rate = await session.get(LogisticsRate, rate_id)
    if rate is None:
        raise AppError(ErrorCode.NOT_FOUND, "费率不存在", 404)
    before = svc.serialize_logistics_rate(rate)
    await session.delete(rate)
    await write_audit(
        session,
        operator_id=user.id,
        action="delete",
        business_type="logistics_rate",
        business_id=rate_id,
        before=before,
        ip=client_ip(request),
    )
    await session.commit()
    return ok(None, "费率已删除")


# ---------------------------------------------------------------- 查价（产品报价中心 · 第一批）


async def _ensure_customer_visible(
    session: AsyncSession, user: CurrentUser, customer_id: int | None
) -> None:
    """核价/查价带客户时，客户必须在该用户数据范围内（与客户列表同口径）。"""
    if customer_id is None:
        return
    from app.core.data_scope import ensure_in_scope

    customer = await session.get(Customer, customer_id)
    if customer is None:
        raise AppError(ErrorCode.NOT_FOUND, "客户不存在", 404)
    await ensure_in_scope(
        session, user, owner_id=customer.owner_id, label="客户", allow_unowned=True
    )


async def _guard_level_override(
    session: AsyncSession,
    user: CurrentUser,
    *,
    customer_id: int | None,
    requested_level: str | None,
) -> None:
    """方案 §4.1：临时覆盖客户价格等级需要 `price:manage`。

    普通查价请求不得自行指定等级——传个 A 级就能拿到 A 级价，
    等于等级价形同虚设。与客户档案等级一致的传值视为无覆盖，放行。
    """
    if not requested_level:
        return
    if user.has("price:manage"):
        return
    archive_level = None
    if customer_id:
        customer = await session.get(Customer, customer_id)
        if customer is not None:
            archive_level = (customer.level or "").strip().upper() or None
    if requested_level.strip().upper() != (archive_level or "").upper():
        raise AppError(
            ErrorCode.FORBIDDEN,
            f"指定的价格等级（{requested_level}）与客户档案不符，覆盖等级需要价格管理权限",
            403,
        )


def _sanitize_pricing_result(result: dict, user: CurrentUser) -> dict:
    """方案 §4.3 / §7：成本与底价只向获授权角色返回。

    对没有 `price:manage` 的调用方，把响应里的成本、保护价、授权底价、
    利润全部置空（保留建议价与审批判定，销售据此知道"能不能报"）。
    `price_rule` / `customer_price_rule` 里也内嵌了最低价，一并清掉。
    """
    if user.has("price:manage"):
        return result
    for key in (
        "protection_price",
        "minimum_price",
        # 绝对底价由成本推出（成本×(1+X)），同样能反推成本（D7 判定层）
        "hard_floor_price",
        "profit",
        "profit_rate",
        "profit_with_refund",
        "profit_rate_with_refund",
        "cost_in_quote_currency",
        "authorized_min_margin",
    ):
        if key in result:
            result[key] = None
    # 成本可倒推：无价格规则时 standard/recommended = cost/(1-margin)，
    # 销售拿它乘 (1-margin) 就还原了成本——无规则来源时一律不给（方案 §7）
    rule = result.get("price_rule")
    customer_rule = result.get("customer_price_rule")
    if not (isinstance(rule, dict) and rule.get("standard_price") is not None):
        result["standard_price"] = None
    if not (
        (isinstance(rule, dict) and rule.get("guide_price") is not None)
        or (isinstance(customer_rule, dict) and customer_rule.get("agreed_price") is not None)
    ):
        for key in (
            "recommended_price",
            "recommended_range",
            "recommended_profit",
            "recommended_profit_rate",
        ):
            if key in result:
                result[key] = None
    cost = result.get("cost")
    if isinstance(cost, dict):
        for key in list(cost):
            if key != "source":
                cost[key] = None
        result["cost"]["source"] = "成本与保护价仅价格管理员可见"
    for rule_key in ("price_rule", "customer_price_rule"):
        rule = result.get(rule_key)
        if isinstance(rule, dict):
            rule["minimum_price"] = None
    return result

@router.get("/pricing/lookup")
async def lookup_price(
    customer_id: int = Query(...),
    sku_id: int = Query(...),
    quantity: Decimal = Query(..., gt=0),
    user: CurrentUser = Depends(require_permission("product:view")),
    session: AsyncSession = Depends(get_db),
):
    """统一查价（方案 §4）：返回本次条件的适用价与命中来源。

    - 只读，不落库；选了客户+数量才标为「该客户本次适用价」；
    - 客户必须在当前用户数据范围内（与客户列表同一口径，公海客户放行）；
    - 成本/最低保护价只对有 `price:manage` 的角色返回（方案 §7 字段脱敏）；
    - 缺价返回 `status=pending`（待定价），不做成本推算兜底（D4/D5 已确认）。
    """
    from app.core.data_scope import ensure_in_scope

    sku = await session.get(Sku, sku_id)
    if sku is None:
        raise AppError(ErrorCode.NOT_FOUND, "SKU 不存在", 404)
    customer = await session.get(Customer, customer_id)
    if customer is None:
        raise AppError(ErrorCode.NOT_FOUND, "客户不存在", 404)
    await ensure_in_scope(
        session, user, owner_id=customer.owner_id, label="客户", allow_unowned=True
    )

    # 公海客户（无负责人）不参与专属价匹配：协议价只对负责人可见，
    # 数据范围放行只代表"这个客户你能看"，不代表"他的协议价你能看"
    result = await svc.lookup_applicable_price(
        session,
        customer=customer,
        sku_id=sku_id,
        quantity=quantity,
        include_customer_specific=customer.owner_id is not None,
    )
    result.update(
        {
            "customer": {"id": customer.id, "name": customer.name, "level": customer.level},
            "sku": {"id": sku.id, "sku_code": sku.sku_code, "name": sku.name},
            "quantity": float(quantity),
        }
    )

    # 成本与保护价按角色脱敏：没有 price:manage 的销售只看到适用价。
    # 必须在**后端**剥掉 minimum_price——留在响应里靠前端隐藏等于没防
    can_see_cost = user.has("price:manage")
    result["can_see_cost"] = can_see_cost
    if not can_see_cost:
        result["minimum_price"] = None
    if can_see_cost:
        cost = await svc.get_effective_cost(session, sku_id)
        if cost is None:
            result["cost"] = None
            result["cost_note"] = "该 SKU 无生效成本，利润不可计算"
        else:
            # 货成本口径与核价一致：采购+生产+包装+加工。用模型上的 total_cost，
            # 不要在这里手写四项相加 —— 第七批 7.4 之后这四列可空，
            # 手写相加遇到 NULL 会直接 TypeError。
            result["cost"] = float(cost.total_cost)
            # 未提供的成本项按 0 计入合计，必须一并说出"缺哪几项"，
            # 否则查价页会把它当成完整成本，毛利率看着偏高却没人知道原因
            missing = cost.missing_components
            result["cost_note"] = (
                None
                if not missing
                else "成本不完整（缺：" + "、".join(missing) + "），合计按 0 计入，毛利率会偏高"
            )
            result["cost_missing_components"] = missing
    else:
        result["cost"] = None
        result["cost_note"] = None
    # 必须走统一信封（03-API §1.1）：前端 api.get 一律按 {code,message,data} 拆包，
    # 这里直接返回裸 dict 会被 `code !== 0`（实际是 undefined !== 0）判成失败，
    # 页面表现是"弹一个没有文字的红条、结果不渲染"。
    return ok(result)


# ---------------------------------------------------------------- 核价

@router.post("/pricing/calculate")
async def calculate(
    payload: PricingRequest,
    user: CurrentUser = Depends(require_permission("product:view")),
    session: AsyncSession = Depends(get_db),
):
    await _ensure_customer_visible(session, user, payload.customer_id)
    await _guard_level_override(session, user, customer_id=payload.customer_id, requested_level=payload.customer_level)
    result = await svc.calculate_price(
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
    return ok(_sanitize_pricing_result(result, user))


@router.post("/pricing/batch-calculate")
async def batch_calculate(
    items: list[PricingRequest],
    user: CurrentUser = Depends(require_permission("product:view")),
    session: AsyncSession = Depends(get_db),
):
    results = []
    for item in items:
        await _ensure_customer_visible(session, user, item.customer_id)
        await _guard_level_override(session, user, customer_id=item.customer_id, requested_level=item.customer_level)
        results.append(
            _sanitize_pricing_result(
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
                ),
                user,
            )
        )
    return ok(results)


@router.post("/pricing/check-permission")
async def check_price_permission(
    payload: PricePermissionCheck,
    user: CurrentUser = Depends(require_permission("product:view")),
    session: AsyncSession = Depends(get_db),
):
    """询价权限校验（03-API §18）。

    `calculate` 回答"建议报多少"，这个回答"我要报的价能不能报"：
    能不能自主定价、不行的话是谁的问题（低于保护价 / 超出我的授权 /
    利润不达标），以及要走到哪一级审批。

    判定逻辑与报价明细用的**是同一份** `calculate_price` ——
    两处各写一遍必然漂移（核价说能报、报价单说不能，业务会不信系统）。
    """
    await _ensure_customer_visible(session, user, payload.customer_id)
    await _guard_level_override(session, user, customer_id=payload.customer_id, requested_level=payload.customer_level)
    result = await svc.calculate_price(
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

    triggers = result["approval_triggers"]
    reasons = []
    if triggers["below_protection_price"]:
        reasons.append("低于公司最低保护价")
    if triggers["below_authorized_price"]:
        reasons.append("低于你角色被授权的最低价")
    if triggers["negative_profit"]:
        reasons.append("负利润")
    if triggers["below_authorized_margin"]:
        reasons.append("利润率低于你角色的授权下限")
    # D7：绝对底价不是"需审批"而是"不可批"——allowed 直接为 False，
    # 不给任何审批出路（区别于保护价触发的审批流程）
    hard_rejected = bool(triggers.get("below_hard_floor"))
    if hard_rejected:
        reasons.append("低于公司绝对底价（任何审批都无法通过，提交会被直接拒绝）")

    _sanitize_pricing_result(result, user)
    approval_required = result["approval_required"]
    return ok(
        {
            "sku_id": payload.sku_id,
            "quantity": result["quantity"],
            "quoted_price": result["quoted_price"],
            "currency": result["currency"],
            # 结论
            "allowed": not approval_required and not hard_rejected,
            "approval_required": approval_required,
            "can_approve": result["can_approve"],
            "reasons": reasons,
            # 依据（界面要能解释"为什么不行"）
            "minimum_price": result["minimum_price"],
            "protection_price": result["protection_price"],
            "authorized_min_margin": result["authorized_min_margin"],
            "standard_price": result["standard_price"],
            "recommended_price": result["recommended_price"],
            "recommended_range": result["recommended_range"],
            "profit": result["profit"],
            "profit_rate": result["profit_rate"],
            "cost_in_quote_currency": result["cost_in_quote_currency"],
            "approval_triggers": triggers,
            "my_roles": user.roles,
        }
    )


@router.post("/pricing/simulate")
async def simulate_pricing(
    payload: PricingSimulation,
    user: CurrentUser = Depends(require_permission("product:view")),
    session: AsyncSession = Depends(get_db),
):
    """报价模拟（03-API §18）：一次算多个候选价，看清让步空间。

    候选来源：显式给的 `candidates`；没给就用 `margins` 各档反推成交价。
    `margins` 也没给时，按"授权下限 / 保护价 / 建议价"三个关键点位试算 ——
    这三个点正好是"能让到哪、再低要审批、正常该报多少"。
    """
    await _ensure_customer_visible(session, user, payload.base.customer_id)
    await _guard_level_override(
        session, user, customer_id=payload.base.customer_id, requested_level=payload.base.customer_level
    )
    base = payload.base
    quantize = Decimal("0.01")

    common = dict(
        sku_id=base.sku_id,
        quantity=base.quantity,
        customer_id=base.customer_id,
        logistics_cost=base.logistics_cost,
        target_margin=base.target_margin,
        target_profit_amount=base.target_profit_amount,
        role_codes=user.roles,
        currency=base.currency,
        exchange_rate=base.exchange_rate,
        tax_refund_rate=base.tax_refund_rate,
        customer_level=base.customer_level,
        country=base.country,
        package_type=base.package_type,
        shipping_method=base.shipping_method,
        payment_terms=base.payment_terms,
    )

    # 先算一次不带报价的基准，拿到建议价/保护价等锚点
    baseline = await svc.calculate_price(session, quoted_price=None, **common)

    candidates = [c for c in payload.candidates if c and c > 0]
    if not candidates and payload.margins:
        cost = Decimal(str(baseline["cost_in_quote_currency"] or 0))
        for margin in payload.margins:
            if margin is None or margin <= 0 or margin >= 1:
                continue
            # 价 = 成本 / (1 - 利润率)
            candidates.append((cost / (Decimal(1) - Decimal(str(margin)))).quantize(quantize))
    if not candidates:
        # 三个关键点位：建议价、最低可自主定价、公司保护价
        for anchor in (
            baseline["recommended_price"],
            baseline["minimum_price"],
            baseline["protection_price"],
        ):
            value = Decimal(str(anchor)) if anchor is not None else None
            if value is not None and value > 0:
                candidates.append(value.quantize(quantize))

    # 去重并保序，避免同一档位算两遍
    seen: set[str] = set()
    unique: list[Decimal] = []
    for item in candidates:
        key = str(item)
        if key not in seen:
            seen.add(key)
            unique.append(item)

    rows = []
    for price in unique:
        result = await svc.calculate_price(session, quoted_price=price, **common)
        triggers = result["approval_triggers"]
        reasons = []
        if triggers["below_protection_price"]:
            reasons.append("低于公司最低保护价")
        if triggers["below_authorized_price"]:
            reasons.append("低于你角色被授权的最低价")
        if triggers["negative_profit"]:
            reasons.append("负利润")
        if triggers["below_authorized_margin"]:
            reasons.append("利润率低于你角色的授权下限")
        rows.append(
            {
                "quoted_price": result["quoted_price"],
                "profit": result["profit"],
                "profit_rate": result["profit_rate"],
                "amount": (
                    float(Decimal(str(result["quoted_price"])) * Decimal(str(base.quantity)))
                    if result["quoted_price"] is not None
                    else None
                ),
                "approval_required": result["approval_required"],
                "reasons": reasons,
            }
        )

    # 候选行的 profit 逐个脱敏，基准响应走统一助手
    if not user.has("price:manage"):
        for row in rows:
            row["profit"] = None
            row["profit_rate"] = None
    _sanitize_pricing_result(baseline, user)

    return ok(
        {
            "sku": baseline["sku"],
            "quantity": baseline["quantity"],
            "currency": baseline["currency"],
            "cost_in_quote_currency": baseline["cost_in_quote_currency"],
            "standard_price": baseline["standard_price"],
            "recommended_price": baseline["recommended_price"],
            "recommended_range": baseline["recommended_range"],
            "minimum_price": baseline["minimum_price"],
            "protection_price": baseline["protection_price"],
            "scenarios": rows,
        }
    )


@router.get("/pricing/history")
async def pricing_history(
    sku_id: int | None = None,
    customer_id: int | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user: CurrentUser = Depends(require_permission("product:view")),
    session: AsyncSession = Depends(get_db),
):
    """核价历史（03-API §18）。

    "这个 SKU 的价格改过几次、谁改的、从多少改到多少" ——
    数据取自审计日志（报价明细的增删改、成本与价格规则的变更），
    不另建一张价格历史表：审计日志本来就是这些变更的事实来源，
    再存一份只会两边不一致。

    **成本与底价只向获授权角色返回**（方案 §4.3/§7，与 `_sanitize_pricing_result`
    同一口径）。这里的 before/after 是各业务类型（成本 / 价格规则 / 客户特殊价 /
    报价明细）的审计快照，字段形状随类型而变；逐字段脱敏得按类型枚举键名，
    漏一个就等于没脱敏，所以对没有 `price:manage` 的调用方**不返回快照本身**——
    保留"谁在什么时候改了什么"，拿不到成本与底价的具体数字。
    """
    from app.core.audit import AuditLog

    can_see_cost = user.has("price:manage")

    stmt = select(AuditLog).where(
        AuditLog.business_type.in_(
            ("quote", "quote_item", "product_cost", "price_rule", "customer_price_rule")
        )
    )
    if sku_id is not None:
        stmt = _filter_history_by_sku(stmt, AuditLog, sku_id)
    if customer_id is not None:
        stmt = stmt.where(
            (AuditLog.business_type == "customer_price_rule")
            & AuditLog.after_data["customer_id"].astext == str(customer_id)
        )
    rows, total = await paginate(session, stmt.order_by(AuditLog.id.desc()), page, page_size)

    operator_ids = {row.operator_id for row in rows if row.operator_id}
    names: dict[int, str] = {}
    if operator_ids:
        from app.modules.user.model import User

        found = (
            await session.execute(
                select(User.id, User.name).where(User.id.in_(operator_ids))
            )
        ).all()
        names = {int(uid): name for uid, name in found}

    # 业务摘要：把 business_id（内部编号）翻成"报价单 Q2026xxxx / 产品编码"这类
    # 人能看懂的东西（返修：核价历史列表未做人性化显示）。
    targets = await _history_targets(session, rows)

    return ok(
        page_data(
            [
                {
                    "id": row.id,
                    "business_type": row.business_type,
                    "business_id": row.business_id,
                    # 展示用："报价单 Q202610060001 · ZX-6040-B"；查不到时为 None
                    "target_label": (targets.get((row.business_type, row.business_id)) or {}).get(
                        "label"
                    ),
                    # 能跳就跳（目前只有报价类有页面可跳），跳不了给 None
                    "target_link": (targets.get((row.business_type, row.business_id)) or {}).get(
                        "link"
                    ),
                    "action": row.action,
                    "operator_id": row.operator_id,
                    "operator_name": names.get(row.operator_id) if row.operator_id else None,
                    "before": row.before_data if can_see_cost else None,
                    "after": row.after_data if can_see_cost else None,
                    "created_at": row.created_at,
                }
                for row in rows
            ],
            total,
            page,
            page_size,
        )
    )


def _filter_history_by_sku(stmt, audit_model, sku_id: int):
    """按 SKU 过滤核价历史。

    审计日志的 `before_data` / `after_data` 是 JSON 列（不是 PostgreSQL
    原生 jsonb 类型，走的是项目自己的 JSONType），所以**没有 `.astext`**。
    统一用 `cast(Text)` 再按文本匹配：

    - 顶层 `sku_id` 用精确串匹配（带引号，避免 1 命中 12）；
    - 报价单的明细数组里 SKU 在 `items[*].sku_id`，用 LIKE 兜住。
    """
    key = f'%"sku_id": {sku_id}'  # JSON 序列化后形如 "sku_id": 12
    return stmt.where(
        (cast(audit_model.before_data, Text).like(f'%{key}%'))
        | (cast(audit_model.after_data, Text).like(f'%{key}%'))
    )


async def _history_targets(session: AsyncSession, rows) -> dict[tuple[str, int], dict]:
    """把 (业务类型, 业务对象编号) 翻成人能看懂的对象描述。

    为什么要反查：审计表只有 `business_type` + `business_id`，**没有业务单号列**。
    列表原先直接渲染 `business_id`（295、4320），业务上根本看不出是哪张单。

    各类型的"单号"在哪（逐个查证过）：
      · quote               → 整对象快照里带 `quote_no`，取不到才回查 quotes 表
      · quote_item          → **明细可能已被硬删**（delete_item 走 session.delete），
                              按 business_id 回查会落空，只能靠快照里的
                              `quote_version_id` → quote_versions.quote_id → quotes.quote_no
      · product_cost        → 快照里的 `sku_id` → skus.sku_code
      · price_rule          → 同上（注意 update 的快照里 sku_code 恒为 None，只能回查）
      · customer_price_rule → sku_id → sku_code，另加 customer_id → customers.name

    返回 {(类型, 编号): {"label": 展示用文字, "link": 可跳转路径(可空)}}。
    查不到的留 None，前端会退回显示编号 —— 不会因为查不到就把那一格变成空白。
    """
    from app.modules.customer.model import Customer
    from app.modules.product.model import Sku
    from app.modules.quote.model import Quote, QuoteVersion

    def from_snapshot(row, key: str):
        """快照里的某个键：先看 after 再看 before（删除动作只有 before 有内容）。"""
        for data in (row.after_data, row.before_data):
            if isinstance(data, dict) and data.get(key) is not None:
                return data[key]
        return None

    sku_ids: set[int] = set()
    customer_ids: set[int] = set()
    version_ids: set[int] = set()
    quote_ids: set[int] = set()

    for row in rows:
        bt = row.business_type
        if bt in ("product_cost", "price_rule", "customer_price_rule", "quote_item"):
            value = from_snapshot(row, "sku_id")
            if value is not None:
                sku_ids.add(int(value))
        if bt == "customer_price_rule":
            value = from_snapshot(row, "customer_id")
            if value is not None:
                customer_ids.add(int(value))
        if bt == "quote_item":
            value = from_snapshot(row, "quote_version_id")
            if value is not None:
                version_ids.add(int(value))
        if bt == "quote" and row.business_id is not None:
            quote_ids.add(int(row.business_id))

    sku_codes: dict[int, str] = {}
    if sku_ids:
        sku_codes = {
            int(i): code
            for i, code in (
                await session.execute(select(Sku.id, Sku.sku_code).where(Sku.id.in_(sku_ids)))
            ).all()
        }
    customer_names: dict[int, str] = {}
    if customer_ids:
        customer_names = {
            int(i): name
            for i, name in (
                await session.execute(
                    select(Customer.id, Customer.name).where(Customer.id.in_(customer_ids))
                )
            ).all()
        }
    version_quote: dict[int, int] = {}
    if version_ids:
        version_quote = {
            int(i): int(q)
            for i, q in (
                await session.execute(
                    select(QuoteVersion.id, QuoteVersion.quote_id).where(
                        QuoteVersion.id.in_(version_ids)
                    )
                )
            ).all()
        }
        quote_ids |= set(version_quote.values())
    quote_nos: dict[int, str] = {}
    if quote_ids:
        quote_nos = {
            int(i): no
            for i, no in (
                await session.execute(
                    select(Quote.id, Quote.quote_no).where(Quote.id.in_(quote_ids))
                )
            ).all()
        }

    out: dict[tuple[str, int], dict] = {}
    for row in rows:
        bt, bid = row.business_type, row.business_id
        if bt is None or bid is None:
            continue
        label: str | None = None
        link: str | None = None
        sku_value = from_snapshot(row, "sku_id")
        code = sku_codes.get(int(sku_value)) if sku_value is not None else None

        if bt == "quote":
            no = from_snapshot(row, "quote_no") or quote_nos.get(int(bid))
            label = f"报价单 {no}" if no else "报价单"
            link = f"/quotes/{int(bid)}"
        elif bt == "quote_item":
            version_value = from_snapshot(row, "quote_version_id")
            quote_id = (
                version_quote.get(int(version_value)) if version_value is not None else None
            )
            no = quote_nos.get(quote_id) if quote_id else None
            pieces = [p for p in ((f"报价单 {no}" if no else "报价单"), code) if p]
            label = " · ".join(pieces)
            link = f"/quotes/{quote_id}" if quote_id else None
        elif bt == "customer_price_rule":
            customer_value = from_snapshot(row, "customer_id")
            name = customer_names.get(int(customer_value)) if customer_value is not None else None
            pieces = [p for p in (name, code) if p]
            label = " · ".join(pieces) if pieces else None
        else:
            label = code

        out[(bt, int(bid))] = {"label": label, "link": link}
    return out


@router.get("/skus/{sku_id}/price-summary")
async def price_summary(
    sku_id: int,
    # 返回体含生效成本与全部价格规则（含最低保护价），同 /skus/{id}/costs 口径，
    # 只对价格管理角色开放（方案 §7 脱敏；此前只要求 product:view 是泄露口子）
    _: CurrentUser = Depends(require_permission("price:manage")),
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
    user: CurrentUser = Depends(require_permission("product:view")),
    session: AsyncSession = Depends(get_db),
):
    """单条价格规则（03-API §16）。"""
    row = await session.get(PriceRule, rule_id)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "价格规则不存在", 404)
    codes = await _sku_code_map(session, [row.sku_id])
    # 单条也要按权限隐藏保护价（§7.3 复审：列表/指定查询/单条一个口径，
    # 否则"列表里看不到保护价，拿 id 单独查就看到了"）
    return ok(
        svc.serialize_price_rule(
            row, codes.get(row.sku_id), can_see_cost=user.has("price:manage")
        )
    )


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
