"""报价业务逻辑：编号、明细快照、金额汇总、审批判定。"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.modules.approval.model import ApprovalDefinition, ApprovalInstance, ApprovalRecord
from app.modules.customer.model import Customer
from app.modules.opportunity.model import Opportunity, OpportunityItem
from app.modules.pricing import service as pricing_service
from app.modules.pricing.model import ExchangeRate
from app.modules.product.model import Product, Sku
from app.modules.quote.model import (
    QUOTE_STATUS_LABEL,
    Quote,
    QuoteCharge,
    QuoteItem,
    QuoteVersion,
)
from app.modules.settings import service as settings_service
from app.modules.user.model import User

ZERO = Decimal("0")


def _f(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def serialize_item(item: QuoteItem) -> dict:
    return {
        "id": item.id,
        "quote_version_id": item.quote_version_id,
        "opportunity_item_id": item.opportunity_item_id,
        "sku_id": item.sku_id,
        "sku_code": item.sku_code_snapshot,
        "sku_name": item.sku_name_snapshot,
        "specification": item.spec_snapshot,
        "quantity": _f(item.quantity),
        "cost_snapshot": _f(item.cost_snapshot),
        "package_cost_snapshot": _f(item.package_cost_snapshot),
        "logistics_cost_snapshot": _f(item.logistics_cost_snapshot),
        "standard_price_snapshot": _f(item.standard_price_snapshot),
        "recommended_price_snapshot": _f(item.recommended_price_snapshot),
        "minimum_price_snapshot": _f(item.minimum_price_snapshot),
        "quoted_price": _f(item.quoted_price),
        "amount": _f(item.quantity * item.quoted_price),
        "profit_snapshot": _f(item.profit_snapshot),
        "profit_rate_snapshot": _f(item.profit_rate_snapshot),
        "approval_required": item.approval_required,
        "approval_reason": item.approval_reason,
        "remark": item.remark,
    }


def serialize_charge(charge: QuoteCharge) -> dict:
    return {
        "id": charge.id,
        "charge_type": charge.charge_type,
        "description": charge.description,
        "amount": _f(charge.amount),
        "is_discount": charge.is_discount,
        "sort_no": charge.sort_no,
    }


def serialize_version(version: QuoteVersion, items_total_profit: Decimal | None = None) -> dict:
    return {
        "id": version.id,
        "quote_id": version.quote_id,
        "version_no": version.version_no,
        "subtotal_amount": _f(version.subtotal_amount),
        "charge_amount": _f(version.charge_amount),
        "discount_amount": _f(version.discount_amount),
        "total_amount": _f(version.total_amount),
        "currency": version.currency,
        "payment_terms": version.payment_terms,
        "delivery_terms": version.delivery_terms,
        "remark": version.remark,
        "approval_status": version.approval_status,
        "approval_required": version.approval_required,
        "created_at": version.created_at,
        "submitted_at": version.submitted_at,
        "approved_at": version.approved_at,
        "sent_at": version.sent_at,
        "accepted_at": version.accepted_at,
        "declined_at": version.declined_at,
        "total_profit": _f(items_total_profit),
    }


def serialize_quote(
    quote: Quote,
    *,
    version: QuoteVersion | None = None,
    customer_name: str | None = None,
    owner_name: str | None = None,
    opportunity_title: str | None = None,
) -> dict:
    return {
        "id": quote.id,
        "quote_no": quote.quote_no,
        "opportunity_id": quote.opportunity_id,
        "opportunity_title": opportunity_title,
        "customer_id": quote.customer_id,
        "customer_name": customer_name,
        "contact_id": quote.contact_id,
        "owner_id": quote.owner_id,
        "owner_name": owner_name,
        "status": quote.status,
        "status_label": QUOTE_STATUS_LABEL.get(quote.status, quote.status),
        "current_version_id": quote.current_version_id,
        "current_version_no": version.version_no if version else None,
        "current_version_amount": _f(version.total_amount) if version else None,
        "approval_status": version.approval_status if version else None,
        "approval_required": version.approval_required if version else False,
        "valid_until": quote.valid_until,
        "created_at": quote.created_at,
        "updated_at": quote.updated_at,
    }


async def generate_quote_no(session: AsyncSession) -> str:
    today = datetime.now(UTC).strftime("%Y%m%d")
    prefix = f"Q{today}"
    count = (
        await session.execute(
            select(func.count(Quote.id)).where(Quote.quote_no.like(f"{prefix}%"))
        )
    ).scalar_one()
    return f"{prefix}{int(count) + 1:04d}"


async def get_quote_or_404(session: AsyncSession, quote_id: int) -> Quote:
    quote = await session.get(Quote, quote_id)
    if quote is None or quote.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "报价单不存在", 404)
    return quote


async def get_version_or_404(session: AsyncSession, version_id: int) -> QuoteVersion:
    version = await session.get(QuoteVersion, version_id)
    if version is None:
        raise AppError(ErrorCode.NOT_FOUND, "报价版本不存在", 404)
    return version


async def ensure_version_editable(version: QuoteVersion) -> None:
    """已发送或已审批通过的版本不可原地修改（总设计文档 §5.8）。"""
    if version.approval_status in ("pending", "approved") or version.sent_at is not None:
        raise AppError(
            ErrorCode.QUOTE_VERSION_LOCKED,
            "该版本已提交审批或已发送，请新建版本再修改",
            422,
        )


async def resolve_exchange_rate(
    session: AsyncSession, currency: str | None
) -> tuple[Decimal | None, str | None]:
    """取该币种对本币（CNY）的当前汇率，用于报价版本快照（02-ER §11）。

    内贸（CNY/空）不需要汇率，直接返回 (None, None)。
    找不到汇率时返回 (None, 说明)，由调用方决定是报错还是提示——
    不静默按 1:1 处理，否则外贸报价会悄悄算错一个数量级。
    """
    code = (currency or "CNY").upper()
    if code == "CNY":
        return None, None
    row = (
        await session.execute(
            select(ExchangeRate)
            .where(
                ExchangeRate.quote_currency == code,
                ExchangeRate.base_currency == "CNY",
            )
            .order_by(ExchangeRate.effective_at.desc(), ExchangeRate.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return None, f"币种 {code} 没有维护汇率，请先在价格中心维护汇率后再报外币价"
    return row.rate, row.source or "手工维护"


async def apply_exchange_rate_snapshot(
    session: AsyncSession, version: QuoteVersion, *, currency: str | None, explicit_rate: Decimal | None
) -> str | None:
    """把币种与汇率快照写进报价版本，返回提示信息（无则 None）。"""
    code = (currency or version.currency or "CNY").upper()
    version.currency = code
    if code == "CNY":
        # 内贸：汇率快照留空，语义清晰
        version.exchange_rate_snapshot = None
        version.exchange_rate_source = None
        version.exchange_rate_time = None
        return None
    if explicit_rate is not None:
        version.exchange_rate_snapshot = explicit_rate
        version.exchange_rate_source = "手工指定"
        version.exchange_rate_time = datetime.now(UTC)
        return None
    rate, source = await resolve_exchange_rate(session, code)
    if rate is None:
        return source
    version.exchange_rate_snapshot = rate
    version.exchange_rate_source = source
    version.exchange_rate_time = datetime.now(UTC)
    return None


def convert_cny_to(
    value: Decimal | None, *, to_currency: str | None, rate: Decimal | None
) -> tuple[Decimal | None, str | None]:
    """把**人民币**金额折成目标币种，返回 (金额, 警告)。

    汇率表约定：`rate` 表示「1 单位目标币种 = rate 元人民币」，所以人民币 → 外币是除。

    只支持「人民币 ↔ 某外币」这一种折算。这是因为系统只做国内业务、
    汇率表也只维护对人民币的汇率；若将来真要报非人民币对非人民币的价，
    必须先补一张交叉汇率表，届时这里应显式报错而不是硬算一个错数字。
    缺汇率时不猜，返回警告交给调用方——静默按 1:1 会把人民币价当成美元价。
    """
    target = (to_currency or "CNY").upper()
    if value is None or target == "CNY":
        return value, None
    if rate is None or rate <= 0:
        return None, f"报价币种是 {target}，但该报价版本没有汇率快照，无法把人民币目标价折算过去，已忽略"
    return (value / rate).quantize(Decimal("0.0001")), None


async def create_quote(
    session: AsyncSession,
    *,
    user,
    opportunity: Opportunity | None = None,
    customer_id: int | None = None,
    contact_id: int | None = None,
    currency: str = "CNY",
    exchange_rate: Decimal | None = None,
    valid_until=None,
    payment_terms: str | None = None,
    delivery_terms: str | None = None,
    remark: str | None = None,
) -> dict:
    """从商机（或直接给客户）生成报价单 + V1 版本 + 明细。

    这段逻辑原本写在路由里；抽到 service 是为了让 Agent 工具
    （`create_quote_draft`）和 HTTP 接口共用同一套实现，
    否则两处各写一遍，改价规则时必然漂移。
    调用方负责审计与 commit。
    """
    if opportunity is None and not customer_id:
        raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "必须指定商机或客户")
    if opportunity is not None:
        customer_id = opportunity.customer_id

    # 报价有效期与默认条款从系统配置读，不写死在代码里
    valid_days = int(
        await settings_service.get_number(session, "quote_valid_days", "days", 30)
    )
    default_payment = await settings_service.get_text(
        session, "default_payment_terms", "text", "款到发货"
    )
    default_delivery = await settings_service.get_text(
        session, "default_delivery_terms", "text", "含运费，送货上门"
    )

    quote = Quote(
        quote_no=await generate_quote_no(session),
        opportunity_id=opportunity.id if opportunity else None,
        customer_id=customer_id,
        contact_id=contact_id or (opportunity.primary_contact_id if opportunity else None),
        owner_id=(opportunity.owner_id if opportunity else None) or user.id,
        status="draft",
        valid_until=valid_until
        or (datetime.now(UTC).date() + timedelta(days=valid_days)),
        created_by=user.id,
    )
    session.add(quote)
    await session.flush()

    version = QuoteVersion(
        quote_id=quote.id,
        version_no=1,
        payment_terms=payment_terms or default_payment,
        delivery_terms=delivery_terms or default_delivery,
        remark=remark,
        approval_status="not_submitted",
        created_by=user.id,
        created_at=datetime.now(UTC),
    )
    session.add(version)
    await session.flush()
    quote.current_version_id = version.id

    # 汇率快照必须在生成明细之前落定：明细要按这个汇率核价（02-ER §11）
    rate_warning = await apply_exchange_rate_snapshot(
        session, version, currency=currency, explicit_rate=exchange_rate
    )
    if rate_warning:
        raise AppError(ErrorCode.PARAM_ERROR, rate_warning, 422)

    item_warnings: list[str] = []
    if opportunity is not None:
        for opp_item in await opportunity_items(session, opportunity.id):
            # 商机需求明细的 target_price 是「商机币种」口径（一般是人民币），
            # 而报价可能以外币计价。不折算就会把人民币价当外币价，利润率算错一个量级。
            target_price, fx_warning = convert_cny_to(
                opp_item.target_price,
                to_currency=version.currency,
                rate=version.exchange_rate_snapshot,
            )
            if fx_warning:
                item_warnings.append(fx_warning)
            item = await build_item_snapshot(
                session,
                version=version,
                sku_id=opp_item.sku_id,
                quantity=opp_item.quantity,
                customer_id=customer_id,
                quoted_price=target_price,
                logistics_cost=None,
                opportunity_item_id=opp_item.id,
                spec_snapshot=opp_item.specification,
                remark=opp_item.remark,
                role_codes=user.roles,
            )
            session.add(item)
        await session.flush()
        await recalc_version(session, version)
    else:
        await session.flush()

    return {
        "quote_id": quote.id,
        "quote_no": quote.quote_no,
        "version_id": version.id,
        "currency": version.currency,
        "exchange_rate_snapshot": (
            float(version.exchange_rate_snapshot) if version.exchange_rate_snapshot else None
        ),
        "item_count": len(await version_items(session, version.id)),
        "total_amount": float(version.total_amount),
        "warnings": item_warnings,
        # ORM 对象单独挂在下划线键下给路由用。
        # **不能混在要写审计/JSON 的字段里**：Agent 工具的返回值会直接进
        # audit_logs 的 JSON 列，带 ORM 对象会 "not JSON serializable"。
        "_quote": quote,
        "_version": version,
    }


async def create_version(
    session: AsyncSession, *, quote: Quote, user
) -> QuoteVersion:
    """在已有报价单上新建版本：复制上一版明细与费用，旧版本原样保留。

    同样抽到 service 供 Agent 工具复用。
    """
    latest = (
        await session.execute(
            select(QuoteVersion)
            .where(QuoteVersion.quote_id == quote.id)
            .order_by(QuoteVersion.version_no.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if latest is None:
        raise AppError(ErrorCode.NOT_FOUND, "报价单没有版本", 404)

    version = QuoteVersion(
        quote_id=quote.id,
        version_no=latest.version_no + 1,
        currency=latest.currency,
        payment_terms=latest.payment_terms,
        delivery_terms=latest.delivery_terms,
        remark=latest.remark,
        approval_status="not_submitted",
        created_by=user.id,
        created_at=datetime.now(UTC),
    )
    session.add(version)
    await session.flush()

    for item in await version_items(session, latest.id):
        session.add(
            QuoteItem(
                quote_version_id=version.id,
                opportunity_item_id=item.opportunity_item_id,
                sku_id=item.sku_id,
                sku_code_snapshot=item.sku_code_snapshot,
                sku_name_snapshot=item.sku_name_snapshot,
                spec_snapshot=item.spec_snapshot,
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
                tax_refund_snapshot=item.tax_refund_snapshot,
                profit_with_refund_snapshot=item.profit_with_refund_snapshot,
                approval_required=item.approval_required,
                approval_reason=item.approval_reason,
                remark=item.remark,
            )
        )
    for charge in await version_charges(session, latest.id):
        session.add(
            QuoteCharge(
                quote_version_id=version.id,
                charge_type=charge.charge_type,
                description=charge.description,
                amount=charge.amount,
                currency=charge.currency,
                is_discount=charge.is_discount,
                sort_no=charge.sort_no,
            )
        )
    await session.flush()
    await recalc_version(session, version)
    quote.current_version_id = version.id
    quote.status = "draft"

    # 版本沿用报价的币种；汇率按「本版本创建时点」重新取快照。
    rate_warning = await apply_exchange_rate_snapshot(
        session, version, currency=latest.currency, explicit_rate=None
    )
    if rate_warning:
        raise AppError(ErrorCode.PARAM_ERROR, rate_warning, 422)
    return version


async def build_item_snapshot(
    session: AsyncSession,
    *,
    version: QuoteVersion,
    sku_id: int,
    quantity: Decimal,
    customer_id: int,
    quoted_price: Decimal | None,
    logistics_cost: Decimal | None,
    opportunity_item_id: int | None,
    spec_snapshot: str | None,
    remark: str | None,
    role_codes: list[str],
) -> QuoteItem:
    """生成一条报价明细：成本、标准价、最低价、利润全部落成快照。

    成本口径（与 02-ER §11 的分层保持一致，别改坏）：
      cost_snapshot            = 商品成本（采购+生产+包装+加工）
      package_cost_snapshot    = 包装成本（是商品成本的组成部分，用于展示拆解，不重复计入）
      logistics_cost_snapshot  = 单件运费（独立一层）
    → 单件总成本 = cost_snapshot + logistics_cost_snapshot（审批判定用的就是这个）

    汇率与退税：按报价版本上快照的币种/汇率核价，并把结果一并落成快照，
    否则外贸报价会静默按人民币口径算（此前汇率快照字段一直没被写入）。
    """
    sku = await session.get(Sku, sku_id)
    if sku is None or sku.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, f"SKU {sku_id} 不存在", 404)
    product = await session.get(Product, sku.product_id)

    result = await pricing_service.calculate_price(
        session,
        sku_id=sku_id,
        quantity=quantity,
        customer_id=customer_id,
        logistics_cost=logistics_cost,
        quoted_price=quoted_price,
        role_codes=role_codes,
        currency=version.currency or "CNY",
        exchange_rate=version.exchange_rate_snapshot,
    )
    price = Decimal(str(quoted_price)) if quoted_price is not None else Decimal(str(result["recommended_price"]))
    # 利润必须与报价同币种，否则会拿美元价减人民币成本（曾算出 -452% 的利润率）。
    # cost_snapshot 等成本类快照仍按本币存：审批判定在人民币口径下做，
    # minimum_price_snapshot 也是人民币，混币种比较会得出错误结论。
    cost_in_quote_currency = Decimal(str(result["cost_in_quote_currency"]))
    profit = price - cost_in_quote_currency
    profit_rate = (profit / price) if price else ZERO

    tax_refund = Decimal(str(result.get("tax_refund") or 0))
    profit_with_refund = profit + tax_refund

    return QuoteItem(
        quote_version_id=version.id,
        opportunity_item_id=opportunity_item_id,
        sku_id=sku_id,
        sku_code_snapshot=sku.sku_code,
        sku_name_snapshot=sku.name or (product.name if product else None),
        spec_snapshot=spec_snapshot or sku.specification,
        quantity=quantity,
        cost_snapshot=Decimal(str(result["cost"]["goods_cost"])),
        package_cost_snapshot=Decimal(str(result["cost"]["package_cost"])),
        logistics_cost_snapshot=Decimal(str(result["cost"]["logistics_cost"] or 0)),
        standard_price_snapshot=Decimal(str(result["standard_price"])),
        recommended_price_snapshot=Decimal(str(result["recommended_price"])),
        minimum_price_snapshot=Decimal(str(result["minimum_price"])),
        quoted_price=price,
        profit_snapshot=profit,
        profit_rate_snapshot=profit_rate.quantize(Decimal("0.000001")),
        tax_refund_snapshot=tax_refund,
        profit_with_refund_snapshot=profit_with_refund,
        approval_required=bool(result["approval_required"]),
        approval_reason="；".join(w for w in result["warnings"] if "审批" in w) or None,
        remark=remark,
    )


async def recalc_version(session: AsyncSession, version: QuoteVersion) -> None:
    """重算版本金额：小计、附加费用、折扣、总额。"""
    items = (
        await session.execute(
            select(QuoteItem).where(QuoteItem.quote_version_id == version.id)
        )
    ).scalars().all()
    subtotal = sum((item.quantity * item.quoted_price for item in items), ZERO)
    charges = (
        await session.execute(
            select(QuoteCharge).where(QuoteCharge.quote_version_id == version.id)
        )
    ).scalars().all()
    discount = sum((charge.amount for charge in charges if charge.is_discount), ZERO)
    charge_amount = sum((charge.amount for charge in charges if not charge.is_discount), ZERO)
    version.subtotal_amount = subtotal
    version.charge_amount = charge_amount
    version.discount_amount = discount
    version.total_amount = subtotal + charge_amount + discount


async def version_items(session: AsyncSession, version_id: int) -> list[QuoteItem]:
    return list(
        (
            await session.execute(
                select(QuoteItem)
                .where(QuoteItem.quote_version_id == version_id)
                .order_by(QuoteItem.id.asc())
            )
        ).scalars().all()
    )


async def version_charges(session: AsyncSession, version_id: int) -> list[QuoteCharge]:
    return list(
        (
            await session.execute(
                select(QuoteCharge)
                .where(QuoteCharge.quote_version_id == version_id)
                .order_by(QuoteCharge.sort_no.asc(), QuoteCharge.id.asc())
            )
        ).scalars().all()
    )


async def submit_for_approval(
    session: AsyncSession,
    *,
    quote: Quote,
    version: QuoteVersion,
    applicant_id: int,
    user_roles: list[str],
    reason: str | None = None,
) -> tuple[ApprovalInstance | None, bool]:
    """提交审批。若没有任何明细超出权限，则直接通过，不进审批流。

    是否需要审批以**申请人的价格权限**重新判定，而不是信前端传来的标志位。
    """
    if version.approval_status in ("pending", "approved"):
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "该版本已提交或已通过，无需重复提交")

    items = await version_items(session, version.id)
    if not items:
        raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, "报价单没有任何明细，不能提交")

    min_margin, can_approve = await pricing_service.resolve_min_margin(session, user_roles)
    # 审批判定统一在**人民币口径**下做：成本快照与 minimum_price_snapshot 都是人民币存的，
    # 而 quoted_price 可能是外币。不折算就会拿 3.92 美元去比 25.44 人民币，必然误判需审批。
    fx = version.exchange_rate_snapshot
    foreign = (version.currency or "CNY").upper() != "CNY" and fx and fx > 0

    offending: list[dict] = []
    for item in items:
        base_cost = item.cost_snapshot + item.logistics_cost_snapshot
        price = item.quoted_price
        price_cny = (price * fx) if foreign else price
        profit = price_cny - base_cost
        profit_rate = (profit / price_cny) if price_cny else ZERO
        floor = max(item.minimum_price_snapshot or ZERO, base_cost)
        if price_cny < floor - Decimal("0.0001") or profit_rate < min_margin - Decimal("0.000001"):
            item.approval_required = True
            item.approval_reason = (
                f"报价 ¥{price_cny:.2f}（折人民币）低于最低允许价 ¥{floor:.2f}，"
                f"利润率 {profit_rate * 100:.2f}%（授权 {min_margin * 100:.0f}%）"
            )
            offending.append(
                {
                    "sku_code": item.sku_code_snapshot,
                    "quoted_price": float(price),
                    "quoted_price_cny": float(price_cny),
                    "minimum_price": float(floor),
                    "profit_rate": float(profit_rate),
                }
            )
        else:
            item.approval_required = False
            item.approval_reason = None

    version.submitted_at = datetime.now(UTC)
    version.approval_required = bool(offending)

    if not offending:
        version.approval_status = "approved"
        version.approved_at = datetime.now(UTC)
        quote.status = "approved"
        return None, False

    definition = (
        await session.execute(
            select(ApprovalDefinition).where(ApprovalDefinition.code == "quote_low_price")
        )
    ).scalar_one_or_none()
    if definition is None:
        definition = ApprovalDefinition(
            code="quote_low_price",
            name="报价低价审批",
            business_type="quote_version",
            status="active",
        )
        session.add(definition)
        await session.flush()

    # 审批分级：按报价总额决定走到哪一级，并记录这一级谁有权批（快照，避免中途改配置影响在途审批）
    from app.modules.settings import service as settings_service

    levels = await settings_service.get_list(session, "approval_levels")
    amount = float(version.total_amount or 0)
    node = None
    for level in levels:
        max_amount = level.get("max_amount")
        if max_amount is None or amount <= float(max_amount):
            node = level
            break
    if node is None and levels:
        node = levels[-1]
    node_code = str((node or {}).get("node") or "manager")
    node_label = str((node or {}).get("label") or "主管")
    node_roles = list((node or {}).get("role_codes") or [])

    instance = ApprovalInstance(
        definition_id=definition.id,
        business_type="quote_version",
        business_id=version.id,
        applicant_id=applicant_id,
        status="pending",
        current_node=node_code,
        summary={
            "quote_no": quote.quote_no,
            "version_no": version.version_no,
            "reason": reason,
            "offending": offending,
            "authorized_min_margin": float(min_margin),
            "total_amount": amount,
            "node_label": node_label,
            "node_role_codes": node_roles,
        },
    )
    session.add(instance)
    await session.flush()
    session.add(
        ApprovalRecord(
            approval_instance_id=instance.id,
            node_code="submit",
            approver_id=applicant_id,
            action="submit",
            comment=reason,
        )
    )
    version.approval_status = "pending"
    quote.status = "pending_approval"
    return instance, True


async def latest_approval(session: AsyncSession, version_id: int) -> ApprovalInstance | None:
    return (
        await session.execute(
            select(ApprovalInstance)
            .where(
                ApprovalInstance.business_type == "quote_version",
                ApprovalInstance.business_id == version_id,
            )
            .order_by(ApprovalInstance.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def approval_records(session: AsyncSession, instance_id: int) -> list[dict]:
    rows = (
        await session.execute(
            select(ApprovalRecord, User.name)
            .outerjoin(User, User.id == ApprovalRecord.approver_id)
            .where(ApprovalRecord.approval_instance_id == instance_id)
            .order_by(ApprovalRecord.id.asc())
        )
    ).all()
    return [
        {
            "id": record.id,
            "action": record.action,
            "comment": record.comment,
            "approver_id": record.approver_id,
            "approver_name": name,
            "created_at": record.created_at,
        }
        for record, name in rows
    ]


async def customer_name(session: AsyncSession, customer_id: int) -> str | None:
    customer = await session.get(Customer, customer_id)
    return customer.name if customer else None


async def opportunity_items(session: AsyncSession, opportunity_id: int) -> list[OpportunityItem]:
    return list(
        (
            await session.execute(
                select(OpportunityItem)
                .where(OpportunityItem.opportunity_id == opportunity_id)
                .order_by(OpportunityItem.id.asc())
            )
        ).scalars().all()
    )


async def version_comparison(session: AsyncSession, quote_id: int) -> dict:
    """报价多方案对比（PRD §15.2 + UI 设计稿 What-if A/B/C）。

    逐版本汇总「数量 / SKU 数 / 报价总额 / 成本 / 毛利 / 综合毛利率 / 均价」，
    并与**上一版**做逐项差异，让业务看清"改了什么导致利润变化"。
    全部基于版本内已落库的快照字段，不重算、不读当前价——历史版本必须可复现。
    """
    versions = list(
        (
            await session.execute(
                select(QuoteVersion)
                .where(QuoteVersion.quote_id == quote_id)
                .order_by(QuoteVersion.version_no.asc())
            )
        ).scalars().all()
    )
    if not versions:
        return {"versions": [], "diffs": [], "latest_version_id": None}

    version_ids = [v.id for v in versions]
    items = list(
        (
            await session.execute(
                select(QuoteItem)
                .where(QuoteItem.quote_version_id.in_(version_ids))
                .order_by(QuoteItem.id.asc())
            )
        ).scalars().all()
    )
    charges = list(
        (
            await session.execute(
                select(QuoteCharge)
                .where(QuoteCharge.quote_version_id.in_(version_ids))
                .order_by(QuoteCharge.sort_no.asc(), QuoteCharge.id.asc())
            )
        ).scalars().all()
    )

    items_by_version: dict[int, list[QuoteItem]] = {}
    for item in items:
        items_by_version.setdefault(item.quote_version_id, []).append(item)
    charges_by_version: dict[int, list[QuoteCharge]] = {}
    for charge in charges:
        charges_by_version.setdefault(charge.quote_version_id, []).append(charge)

    rows: list[dict] = []
    for version in versions:
        v_items = items_by_version.get(version.id, [])
        v_charges = charges_by_version.get(version.id, [])
        quantity = sum((item.quantity for item in v_items), ZERO)
        # 单件总成本 = cost_snapshot（商品成本）+ logistics_cost_snapshot（单件运费），
        # 这与 `build_item_snapshot` 里算 profit_snapshot 的口径以及
        # `pricing` 的 base_cost = goods_cost + logistics 完全一致。
        # 少加运费会让汇总毛利和明细的利润快照对不上。
        cost_total = sum(
            (
                (item.cost_snapshot + item.logistics_cost_snapshot) * item.quantity
                for item in v_items
            ),
            ZERO,
        )
        amount_total = sum((item.quantity * item.quoted_price for item in v_items), ZERO)
        # 毛利按明细的利润快照汇总，保证与报价明细表里逐条显示的数字完全对得上
        profit_total = sum((item.profit_snapshot * item.quantity for item in v_items), ZERO)
        if not v_items:
            profit_total = amount_total - cost_total
        margin = (profit_total / amount_total) if amount_total else ZERO
        skus = {item.sku_id for item in v_items}
        rows.append(
            {
                "version_id": version.id,
                "version_no": version.version_no,
                "approval_status": version.approval_status,
                "currency": version.currency,
                "item_count": len(v_items),
                "sku_count": len(skus),
                "quantity": _f(quantity),
                "amount_total": _f(amount_total),
                "charge_amount": _f(version.charge_amount),
                "discount_amount": _f(version.discount_amount),
                "total_amount": _f(version.total_amount),
                "cost_total": _f(cost_total),
                "profit_total": _f(profit_total),
                "margin": _f(margin),
                "avg_price": _f(amount_total / quantity) if quantity else None,
                "avg_cost": _f(cost_total / quantity) if quantity else None,
                # 单件口径单独给一份，前端做滑杆测算时直接用，避免各自算错成本
                "unit_cost": _f(cost_total / quantity) if quantity else None,
                "unit_price": _f(amount_total / quantity) if quantity else None,
                "unit_profit": _f(profit_total / quantity) if quantity else None,
                # 整版加权的价格基准线：单 SKU 的建议价/最低价跟"整版均价"没法直接比，
                # 加权之后滑杆上的价格就能和它们逐条对照。
                "unit_floor": (
                    _f(
                        sum(
                            ((item.minimum_price_snapshot or ZERO) * item.quantity for item in v_items),
                            ZERO,
                        )
                        / quantity
                    )
                    if quantity
                    else None
                ),
                "unit_standard": (
                    _f(
                        sum(
                            (
                                (item.standard_price_snapshot or ZERO) * item.quantity
                                for item in v_items
                            ),
                            ZERO,
                        )
                        / quantity
                    )
                    if quantity
                    else None
                ),
                "unit_recommended": (
                    _f(
                        sum(
                            (
                                (item.recommended_price_snapshot or ZERO) * item.quantity
                                for item in v_items
                            ),
                            ZERO,
                        )
                        / quantity
                    )
                    if quantity
                    else None
                ),
                "approval_required": any(item.approval_required for item in v_items),
                "created_at": version.created_at,
                "sent_at": version.sent_at,
                "accepted_at": version.accepted_at,
                "trade_terms": version.trade_terms,
                "payment_terms": version.payment_terms,
                "charges": [serialize_charge(charge) for charge in v_charges],
            }
        )

    diffs: list[dict] = []
    for previous, current in zip(rows, rows[1:], strict=False):
        prev_items = {item.sku_id: item for item in items_by_version.get(previous["version_id"], [])}
        curr_items = {item.sku_id: item for item in items_by_version.get(current["version_id"], [])}
        changes: list[dict] = []
        for sku_id in sorted(set(prev_items) | set(curr_items)):
            before = prev_items.get(sku_id)
            after = curr_items.get(sku_id)
            if before is None:
                changes.append(
                    {
                        "sku_id": sku_id,
                        "sku_name": after.sku_name_snapshot if after else None,
                        "field": "added",
                        "before": None,
                        "after": _f(after.quoted_price) if after else None,
                    }
                )
                continue
            if after is None:
                changes.append(
                    {
                        "sku_id": sku_id,
                        "sku_name": before.sku_name_snapshot,
                        "field": "removed",
                        "before": _f(before.quoted_price),
                        "after": None,
                    }
                )
                continue
            if before.quantity != after.quantity:
                changes.append(
                    {
                        "sku_id": sku_id,
                        "sku_name": after.sku_name_snapshot,
                        "field": "quantity",
                        "before": _f(before.quantity),
                        "after": _f(after.quantity),
                    }
                )
            if before.quoted_price != after.quoted_price:
                changes.append(
                    {
                        "sku_id": sku_id,
                        "sku_name": after.sku_name_snapshot,
                        "field": "quoted_price",
                        "before": _f(before.quoted_price),
                        "after": _f(after.quoted_price),
                    }
                )
        if previous["charge_amount"] != current["charge_amount"]:
            changes.append(
                {
                    "sku_id": None,
                    "sku_name": None,
                    "field": "charge_amount",
                    "before": previous["charge_amount"],
                    "after": current["charge_amount"],
                }
            )
        if previous["discount_amount"] != current["discount_amount"]:
            changes.append(
                {
                    "sku_id": None,
                    "sku_name": None,
                    "field": "discount_amount",
                    "before": previous["discount_amount"],
                    "after": current["discount_amount"],
                }
            )
        diffs.append(
            {
                "from_version_no": previous["version_no"],
                "to_version_no": current["version_no"],
                "amount_delta": round(current["amount_total"] - previous["amount_total"], 2),
                "profit_delta": round(current["profit_total"] - previous["profit_total"], 2),
                "margin_delta": round(current["margin"] - previous["margin"], 6),
                "changes": changes,
            }
        )

    return {
        "versions": rows,
        "diffs": diffs,
        "latest_version_id": versions[-1].id,
    }
