"""报价业务逻辑：编号、明细快照、金额汇总、审批判定。"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.modules.approval.model import ApprovalDefinition, ApprovalInstance, ApprovalRecord
from app.modules.customer.model import Contact, Customer
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
        # 定制项（场景09）：sku_id 为空时靠这两栏说明"对着哪条需求报的价"
        "inquiry_id": item.inquiry_id,
        "inquiry_no": item.inquiry_no_snapshot,
        "is_custom": item.sku_id is None,
        "specification": item.spec_snapshot,
        "quantity": _f(item.quantity),
        "cost_snapshot": _f(item.cost_snapshot),
        "package_cost_snapshot": _f(item.package_cost_snapshot),
        "logistics_cost_snapshot": _f(item.logistics_cost_snapshot),
        "standard_price_snapshot": _f(item.standard_price_snapshot),
        "recommended_price_snapshot": _f(item.recommended_price_snapshot),
        "minimum_price_snapshot": _f(item.minimum_price_snapshot),
        "price_source": item.price_source,
        "customer_level_snapshot": item.customer_level_snapshot,
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
    """按「编号规则」取报价单号（PRD §2.6）。

    原来的实现是 `count(*)+1`：删掉历史单会重号，并发还会撞。
    现在走 `settings/numbering.py` 的行锁计数器，默认格式与原来一致
    （`Q` + YYYYMMDD + 4 位流水），但保证唯一且并发安全。

    `generate_for` 会自动带上"号已被占用就跳过 + 从库里最大号播种"，
    计数器与已发布号脱节时能自愈（详见 numbering.py 的说明）。
    """
    from app.modules.settings import numbering

    return await numbering.generate_for(session, "quote", model=Quote, column=Quote.quote_no)


async def get_quote_or_404(session: AsyncSession, quote_id: int) -> Quote:
    quote = await session.get(Quote, quote_id)
    if quote is None or quote.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, "报价单不存在", 404)
    return quote


async def get_visible_quote(
    session: AsyncSession, user, quote_id: int
) -> Quote:
    """取报价并校验数据范围（列表按 owner_id 过滤，详情此前没校验）。"""
    from app.core.data_scope import ensure_in_scope

    quote = await get_quote_or_404(session, quote_id)
    await ensure_in_scope(session, user, owner_id=quote.owner_id, label="报价单")
    return quote


async def get_version_or_404(session: AsyncSession, version_id: int) -> QuoteVersion:
    version = await session.get(QuoteVersion, version_id)
    if version is None:
        raise AppError(ErrorCode.NOT_FOUND, "报价版本不存在", 404)
    return version


async def get_visible_version(
    session: AsyncSession, user, version_id: int
) -> QuoteVersion:
    """取报价版本并校验其所属报价在数据范围内（版本自己没有负责人）。"""
    from app.core.data_scope import ensure_in_scope

    version = await get_version_or_404(session, version_id)
    quote = await get_quote_or_404(session, version.quote_id)
    await ensure_in_scope(session, user, owner_id=quote.owner_id, label="报价单")
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
    enforce_opportunity: bool = True,
) -> dict:
    """从商机（或直接给客户）生成报价单 + V1 版本 + 明细。

    这段逻辑原本写在路由里；抽到 service 是为了让 Agent 工具
    （`create_quote_draft`）和 HTTP 接口共用同一套实现，
    否则两处各写一遍，改价规则时必然漂移。
    调用方负责审计与 commit。
    """
    # D8（已确认）：正式报价必须关联商机——成交端点挂在商机上（confirm-win），
    # 不挂商机的报价只能走 convert-to-order 旧路；漏斗/渠道归因/需求明细/价格来源
    # 快照也都依赖商机。只约束新建，历史数据不追溯。
    # 复制（clone）走 enforce_opportunity=False：它先建壳、再挂回商机，
    # 但挂不回（源报价就没有商机且未指定）时由调用方按同一口径拒绝。
    if opportunity is None and enforce_opportunity:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            "报价必须关联商机——请选择已有商机，或在查价页「选品下单」一键新建快捷商机",
            422,
        )
    if opportunity is not None:
        customer_id = opportunity.customer_id

    # 客户/联系人必须存在。放在 service 而不是路由：Agent 工具
    # （`create_quote_draft`）直接调这里，绕开路由校验；漏了就会撞 FK
    # 约束报 500，而不是可读的 40401。
    if await session.get(Customer, customer_id) is None:
        raise AppError(ErrorCode.NOT_FOUND, f"客户 id={customer_id} 不存在", 404)
    resolved_contact_id = contact_id or (
        opportunity.primary_contact_id if opportunity else None
    )
    if resolved_contact_id is not None:
        contact = await session.get(Contact, resolved_contact_id)
        if contact is None:
            raise AppError(
                ErrorCode.NOT_FOUND, f"联系人 id={resolved_contact_id} 不存在", 404
            )
        if contact.customer_id != customer_id:
            raise AppError(
                ErrorCode.PARAM_ERROR,
                f"联系人 id={resolved_contact_id} 不属于客户 id={customer_id}",
            )
    contact_id = resolved_contact_id

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
        customer_obj = await session.get(Customer, customer_id)
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

            # 产品报价中心第二批（方案 §5 / A05）：拟报价默认带「系统适用价」，
            # 客户目标价只是谈判参考、单独留在商机明细上，不再直接变成拟报价。
            # 无适用价时不做成本推算（D4/D5），回退目标价并显式留痕要求人工确认。
            sku = await session.get(Sku, opp_item.sku_id)
            sku_label = sku.sku_code if sku else str(opp_item.sku_id)
            if sku is not None and sku.moq and opp_item.quantity < sku.moq:
                item_warnings.append(
                    f"SKU {sku_label}：数量 {opp_item.quantity} 低于起订量 {sku.moq}，"
                    f"请与生产确认能否接单（方案 §4.1 MOQ 校验）"
                )
            # 方案 §7：需求里的包装/目的地透传进报价上下文并显式提示，
            # 但物流/包装费用不计入拟报价（避免与附加费用重复计入），由销售在附加费用里补
            context_bits = []
            if opp_item.package_requirement:
                context_bits.append(f"包装要求「{opp_item.package_requirement}」")
            if opp_item.destination:
                context_bits.append(f"目的地「{opp_item.destination}」")
            if context_bits:
                item_warnings.append(
                    f"SKU {sku_label}：需求含 {'、'.join(context_bits)}，"
                    f"物流/包装费用未计入拟报价，请在附加费用中补充"
                )
            lookup = await pricing_service.lookup_applicable_price(
                session,
                customer=customer_obj,
                sku_id=opp_item.sku_id,
                quantity=opp_item.quantity,
                # 公海客户（无负责人）不参与专属价匹配——与查价路由同一纪律，
                # 协议价只对负责人可见，此前这里漏传导致公海也能吃到专属价
                include_customer_specific=customer_obj.owner_id is not None,
            )
            if lookup["status"] == "ok" and lookup["unit_price"] is not None:
                quoted_price, _ = convert_cny_to(
                    Decimal(str(lookup["unit_price"])),
                    to_currency=version.currency,
                    rate=version.exchange_rate_snapshot,
                )
                item_warnings.append(
                    f"SKU {sku_label}：拟报价取系统适用价 ¥{lookup['unit_price']}（{lookup['source_label']}），"
                    f"客户目标价 ¥{target_price} 已单独记录"
                )
            else:
                quoted_price = target_price
                item_warnings.append(
                    f"SKU {sku_label}：无系统适用价（待定价），拟报价暂用客户目标价，请人工确认"
                )
            item = await build_item_snapshot(
                session,
                version=version,
                sku_id=opp_item.sku_id,
                quantity=opp_item.quantity,
                customer_id=customer_id,
                quoted_price=quoted_price,
                logistics_cost=None,
                opportunity_item_id=opp_item.id,
                spec_snapshot=opp_item.specification,
                remark=opp_item.remark,
                role_codes=user.roles,
                package_type=opp_item.package_requirement,
                country=opp_item.destination,
                # A09：把"这版当初按哪条规则带的价"落成快照
                price_source=lookup.get("source"),
                customer_level_snapshot=(customer_obj.level or "").strip() or None,
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
    session: AsyncSession, *, quote: Quote, user, source_version_id: int | None = None
) -> QuoteVersion:
    """在已有报价单上新建版本：复制上一版明细与费用，旧版本原样保留。

    `source_version_id` 为空时取最新版（"再来一版"的默认语义）；
    显式传入时以该版本为准（`POST /quote-versions/{id}/copy` 要用，
    否则复制历史版本会变成"最新版明细 + 目标版明细"的叠加）。

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

    source = latest
    if source_version_id is not None and source_version_id != latest.id:
        candidate = await session.get(QuoteVersion, source_version_id)
        if candidate is None or candidate.quote_id != quote.id:
            raise AppError(
                ErrorCode.NOT_FOUND, f"报价版本 {source_version_id} 不存在", 404
            )
        source = candidate

    version = QuoteVersion(
        quote_id=quote.id,
        version_no=latest.version_no + 1,
        currency=source.currency,
        payment_terms=source.payment_terms,
        delivery_terms=source.delivery_terms,
        remark=source.remark,
        approval_status="not_submitted",
        created_by=user.id,
        created_at=datetime.now(UTC),
    )
    session.add(version)
    try:
        await session.flush()
    except IntegrityError as exc:
        # 并发"再来一版"两个请求同时读到同一个 latest，各自 +1 撞唯一约束。
        # 数据库约束是兜底（此前没有约束会静默产生两条同号版本）：
        # 回滚并让客户端拿 409 重试，第二个请求刷新后取到新号。
        await session.rollback()
        raise AppError(
            ErrorCode.VERSION_CONFLICT,
            "报价版本并发创建冲突，请刷新后重试",
            409,
        ) from exc

    for item in await version_items(session, source.id):
        session.add(
            QuoteItem(
                quote_version_id=version.id,
                opportunity_item_id=item.opportunity_item_id,
                sku_id=item.sku_id,
                # 定制件溯源 + 价格来源 + 客户等级快照：漏了就断链——
                # 尤其 price_source 丢成 None 会让漂移检测把"系统带价"当成"人工定价"，
                # 价格维护后系统价变了也不报警（2026-09-30 复核补回）。
                inquiry_id=item.inquiry_id,
                inquiry_no_snapshot=item.inquiry_no_snapshot,
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
                price_source=item.price_source,
                customer_level_snapshot=item.customer_level_snapshot,
                tax_refund_snapshot=item.tax_refund_snapshot,
                profit_with_refund_snapshot=item.profit_with_refund_snapshot,
                approval_required=item.approval_required,
                approval_reason=item.approval_reason,
                remark=item.remark,
            )
        )
    for charge in await version_charges(session, source.id):
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
        session, version, currency=source.currency, explicit_rate=None
    )
    if rate_warning:
        raise AppError(ErrorCode.PARAM_ERROR, rate_warning, 422)
    return version


async def _build_custom_item_snapshot(
    session: AsyncSession,
    *,
    version: QuoteVersion,
    inquiry_id: int | None,
    item_name: str | None,
    quantity: Decimal,
    quoted_price: Decimal | None,
    unit_cost: Decimal | None,
    logistics_cost: Decimal | None,
    spec_snapshot: str | None,
    remark: str | None,
    opportunity_item_id: int | None,
) -> QuoteItem:
    """定制项明细（文档场景09）：尚无正式 SKU 时按需求编号报价。

    为什么三个都必填、缺一就拒：
    1. **需求**（inquiry_id）：没有 SKU 又没有需求，这条明细无源可溯，
       对客文件上连"这是什么"都说不清；
    2. **报价**（quoted_price）：没有 SKU 就没有价格规则可查，只能人工定；
    3. **成本**（unit_cost）：成本未知时若按 0 记，会算出 100% 毛利、
       低价审批也永远不会触发——与 A06「无成本不造假」相反。宁可让人填。

    最低保护价按系统已配置的最低毛利率推（成本 ×(1+default_min_margin)），
    不另创一套定制价政策：定制项与现货项因此走同一个低价审批判定。

    **币种口径（2026-09-30 修正，别改回去）**：成本与运费按**人民币**录入，
    落成 `cost_snapshot` / `logistics_cost_snapshot` / `minimum_price_snapshot`；
    只有报价 `quoted_price` 按报价版本币种。外币单的利润先按汇率把成本折过去再算。
    原文案说"成本按报价币种填"，但审批判定、前端标签（¥）与整单加权底价三处
    都按人民币读同一批快照——于是美元单上 7 美元成本被当成 7 人民币，与 50 人民币的
    报价一比永远不触发低价审批与绝对底价，还显示虚高毛利。**静默放行，最危险的那种错。**
    """
    from app.modules.inquiry.model import CustomInquiry

    if not inquiry_id:
        raise AppError(
            ErrorCode.PARAM_ERROR, "明细必须关联 SKU 或定制需求编号", 422
        )
    inquiry = await session.get(CustomInquiry, inquiry_id)
    if inquiry is None or inquiry.deleted_at is not None:
        raise AppError(ErrorCode.NOT_FOUND, f"定制需求 id={inquiry_id} 不存在", 404)
    if quoted_price is None:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"定制项「{inquiry.title}」请人工填写报价（无系统适用价可查）",
            422,
        )
    if unit_cost is None:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"定制项「{inquiry.title}」请填写核价成本（人民币；缺成本无法判断毛利与低价审批）",
            422,
        )

    price = Decimal(str(quoted_price))
    cost = Decimal(str(unit_cost))  # 人民币：与 SKU 路径、审批判定同一口径
    freight = Decimal(str(logistics_cost or 0))  # 人民币
    min_ratio = Decimal(
        str(await settings_service.get_number(session, "default_min_margin", "ratio", 0.15))
    )
    minimum_price = (cost * (1 + min_ratio)).quantize(Decimal("0.01"))  # 人民币
    # 利润必须与报价同币种：外币单要先把人民币成本折过去再减，
    # 否则会算出"50 美元 − 350 人民币"这种假数字（与定价服务 cost_in_quote_currency 同口径）
    fx = version.exchange_rate_snapshot
    foreign = (version.currency or "CNY").upper() != "CNY" and bool(fx) and fx > 0
    cost_in_quote = ((cost + freight) / fx) if foreign else (cost + freight)
    profit = price - cost_in_quote
    profit_rate = (profit / price) if price else ZERO
    # 比最低保护价同样要同币种：保护价是人民币，先把报价折过去
    price_cny = (price * fx) if foreign else price
    below_floor = price_cny < minimum_price

    # 需求被报价引用即视为已转下游（只在待评估/开发中时翻转，
    # 不覆盖人工做的归档决定）
    if inquiry.status in ("open", "developing"):
        inquiry.status = "converted"

    return QuoteItem(
        quote_version_id=version.id,
        opportunity_item_id=opportunity_item_id,
        sku_id=None,
        inquiry_id=inquiry.id,
        inquiry_no_snapshot=inquiry.inquiry_no,
        # 快照占住 SKU 的位置：对客文件上要能看出这是哪条定制需求
        sku_code_snapshot=inquiry.inquiry_no or f"XQ{inquiry.id:04d}",
        sku_name_snapshot=item_name or inquiry.title,
        spec_snapshot=spec_snapshot or inquiry.description,
        quantity=quantity,
        cost_snapshot=cost,
        package_cost_snapshot=ZERO,
        logistics_cost_snapshot=freight,
        standard_price_snapshot=None,
        recommended_price_snapshot=price,
        minimum_price_snapshot=minimum_price,
        price_source="custom_manual",
        customer_level_snapshot=None,
        quoted_price=price,
        profit_snapshot=profit,
        profit_rate_snapshot=profit_rate.quantize(Decimal("0.000001")),
        tax_refund_snapshot=ZERO,
        profit_with_refund_snapshot=profit,
        approval_required=below_floor,
        approval_reason=(
            f"定制项报价 ¥{price_cny:.2f}（折人民币）低于最低保护价 ¥{minimum_price:.2f}"
            f"（成本 ¥{cost}×(1+{min_ratio})），需审批"
            if below_floor
            else None
        ),
        remark=remark,
    )


async def build_item_snapshot(
    session: AsyncSession,
    *,
    version: QuoteVersion,
    sku_id: int | None,
    quantity: Decimal,
    customer_id: int,
    quoted_price: Decimal | None,
    logistics_cost: Decimal | None,
    opportunity_item_id: int | None,
    spec_snapshot: str | None,
    remark: str | None,
    role_codes: list[str],
    package_type: str | None = None,
    country: str | None = None,
    price_source: str | None = None,
    customer_level_snapshot: str | None = None,
    inquiry_id: int | None = None,
    item_name: str | None = None,
    unit_cost: Decimal | None = None,
) -> QuoteItem:
    """生成一条报价明细：成本、标准价、最低价、利润全部落成快照。

    `sku_id` 为空即定制项（场景09）：转 `_build_custom_item_snapshot`，
    那条路径不需要价格规则，靠人工核价的成本与报价。

    成本口径（与 02-ER §11 的分层保持一致，别改坏）：
      cost_snapshot            = 商品成本（采购+生产+包装+加工）
      package_cost_snapshot    = 包装成本（是商品成本的组成部分，用于展示拆解，不重复计入）
      logistics_cost_snapshot  = 单件运费（独立一层）
    → 单件总成本 = cost_snapshot + logistics_cost_snapshot（审批判定用的就是这个）

    汇率与退税：按报价版本上快照的币种/汇率核价，并把结果一并落成快照，
    否则外贸报价会静默按人民币口径算（此前汇率快照字段一直没被写入）。
    """
    if sku_id is None:
        return await _build_custom_item_snapshot(
            session,
            version=version,
            inquiry_id=inquiry_id,
            item_name=item_name,
            quantity=quantity,
            quoted_price=quoted_price,
            unit_cost=unit_cost,
            logistics_cost=logistics_cost,
            spec_snapshot=spec_snapshot,
            remark=remark,
            opportunity_item_id=opportunity_item_id,
        )
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
        # A07：商机需求里的包装要求与目的地要传进核价，物流匹配才有依据
        package_type=package_type,
        country=country,
    )
    if quoted_price is not None:
        price = Decimal(str(quoted_price))
    elif result["recommended_price"] is not None:
        price = Decimal(str(result["recommended_price"]))
    else:
        # A06：无成本也无已维护售价时，宁可报错也不给出"0 成本推算价"
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"SKU {sku.sku_code} 无适用价且无成本记录，无法自动定价；请先维护价格规则或成本",
            422,
        )
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
        # 存**人民币**口径（result["minimum_price"] 在外币单上已折成计价币种，
        # 拿它落快照会让审批把 3.6 美元的保护价当成 3.6 人民币去比）
        minimum_price_snapshot=(
            Decimal(str(result["minimum_price_cny"]))
            if result.get("minimum_price_cny") is not None
            else None
        ),
        price_source=price_source,
        customer_level_snapshot=customer_level_snapshot,
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
    version.subtotal_amount = subtotal.quantize(Decimal("0.01"))
    version.charge_amount = charge_amount.quantize(Decimal("0.01"))
    version.discount_amount = discount.quantize(Decimal("0.01"))
    # 统一定点舍入（方案 §5）：与订单侧 amount 口径一致，避免出现 3 位小数的总额
    version.total_amount = (subtotal + charge_amount + discount).quantize(Decimal("0.01"))


async def moq_warning(session: AsyncSession, sku_id: int | None, quantity: Decimal) -> str | None:
    """MOQ 提示（方案 §4.1）：数量低于起订量给提示不拦截——拦截与否由业务拍板。"""
    if sku_id is None:  # 定制项没有 SKU，也就没有起订量可谈
        return None
    sku = await session.get(Sku, sku_id)
    if sku is not None and sku.moq and quantity < sku.moq:
        return f"SKU {sku.sku_code}：数量 {quantity} 低于起订量 {sku.moq}，请与生产确认能否接单"
    return None


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


async def price_drift(
    session: AsyncSession, *, version: QuoteVersion
) -> dict:
    """A09 后半：草稿版本的"价格已有更新"检测。

    逐明细按当前条件重查适用价，与快照拟报价比对：
    - `price_source` 非空的明细（系统带价）价格或来源变了 → drift；
    - `price_source` 为空的明细（销售手工价）只提示参考，不算强制漂移，
      刷新时也**不会**覆盖它们（方案 §5：手工调整不得被无提示覆盖）。
    """
    quote = await session.get(Quote, version.quote_id)
    items = await version_items(session, version.id)
    customer = await session.get(Customer, quote.customer_id) if quote else None
    fx = version.exchange_rate_snapshot
    rows: list[dict] = []
    any_drift = False
    for item in items:
        # 定制项（场景09）没有 SKU，查不到适用价也不该被刷新覆盖：
        # 它们走的是人工核价，硬套价格规则只会报错或算出没意义的值
        if item.sku_id is None:
            rows.append(
                {
                    "item_id": item.id,
                    "sku_code": item.sku_code_snapshot,
                    "quoted_price": _f(item.quoted_price),
                    "current_applicable": None,
                    "source": "定制人工核价",
                    "hand_priced": True,
                    "drift": False,
                }
            )
            continue
        lookup = (
            await pricing_service.lookup_applicable_price(
                session, customer=customer, sku_id=item.sku_id, quantity=item.quantity
            )
            if customer is not None
            else {"status": "pending", "unit_price": None, "source": None}
        )
        current = None
        if lookup["status"] == "ok" and lookup["unit_price"] is not None:
            current, _ = convert_cny_to(
                Decimal(str(lookup["unit_price"])),
                to_currency=version.currency,
                rate=fx,
            )
        hand_priced = item.price_source is None
        drift = bool(
            not hand_priced
            and current is not None
            and abs(current - item.quoted_price) > Decimal("0.01")
        )
        if drift:
            any_drift = True
        rows.append(
            {
                "item_id": item.id,
                "sku_code": item.sku_code_snapshot,
                "quoted_price": _f(item.quoted_price),
                "current_applicable": _f(current) if current is not None else None,
                "source": lookup.get("source_label"),
                "hand_priced": hand_priced,
                "drift": drift,
            }
        )
    return {"any_drift": any_drift, "items": rows}


async def refresh_prices(
    session: AsyncSession, *, version: QuoteVersion, user
) -> dict:
    """把系统带价的明细刷新到当前适用价；手工价明细原样保留。"""
    quote = await session.get(Quote, version.quote_id)
    items = await version_items(session, version.id)
    customer = await session.get(Customer, quote.customer_id)
    fx = version.exchange_rate_snapshot
    refreshed = 0
    skipped = 0
    for item in items:
        # 定制项（场景09）没有 SKU：人工核价，不参与系统带价刷新
        if item.sku_id is None:
            skipped += 1
            continue
        if item.price_source is None:
            skipped += 1
            continue
        lookup = await pricing_service.lookup_applicable_price(
            session, customer=customer, sku_id=item.sku_id, quantity=item.quantity
        )
        if lookup["status"] != "ok" or lookup["unit_price"] is None:
            skipped += 1
            continue
        new_price, _ = convert_cny_to(
            Decimal(str(lookup["unit_price"])),
            to_currency=version.currency,
            rate=fx,
        )
        rebuilt = await build_item_snapshot(
            session,
            version=version,
            sku_id=item.sku_id,
            quantity=item.quantity,
            customer_id=quote.customer_id,
            quoted_price=new_price,
            logistics_cost=None,
            opportunity_item_id=item.opportunity_item_id,
            spec_snapshot=item.spec_snapshot,
            remark=item.remark,
            role_codes=user.roles,
            price_source=lookup["source"],
            customer_level_snapshot=(customer.level or "").strip() or None if customer else None,
        )
        for field in (
            "quantity", "quoted_price", "cost_snapshot", "logistics_cost_snapshot",
            "standard_price_snapshot", "recommended_price_snapshot", "minimum_price_snapshot",
            "profit_snapshot", "profit_rate_snapshot", "price_source",
            "customer_level_snapshot", "approval_required", "approval_reason",
        ):
            setattr(item, field, getattr(rebuilt, field))
        refreshed += 1
    await session.flush()
    await recalc_version(session, version)
    return {"refreshed": refreshed, "skipped": skipped}


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
    can_see_floor: bool = False,
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

    # ---- 绝对底价（D7 判定层）：命中即 422 硬拒，不生成任何可批的审批单 ----
    # 必须排在审批规则引擎（免审/极速通道）之前：auto_pass 不能把低于硬底的价放过去，
    # 否则"任何人都不能通过"就是空话。保护价走"生成审批单让人批"，这里走"直接拒绝"，
    # 两者严格分开。合法出口写在报错文案里：调价留审计 / 样品 / 清库存特殊通道。
    hard_mode = await settings_service.get_text(session, "hard_floor", "mode", "off")
    if hard_mode in ("cost", "cost_markup"):
        markup = Decimal(
            str(await settings_service.get_number(session, "hard_floor", "markup_ratio", 0.0))
        )
        factor = (Decimal(1) + markup) if hard_mode == "cost_markup" else Decimal(1)
        eps = Decimal("0.0001")
        hard_hits: list[str] = []
        weighted_floor_total = ZERO
        total_qty = ZERO
        for item in items:
            item_cost = item.cost_snapshot + item.logistics_cost_snapshot
            if item_cost <= 0:
                # 无成本记录：硬底无从计算，由保护价/利润判定兜住（A06 同口径）
                continue
            price_cny = (item.quoted_price * fx) if foreign else item.quoted_price
            floor_cny = item_cost * factor
            if price_cny < floor_cny - eps:
                # 脱敏纪律：底价由成本推出（mode=cost 时就是成本本身），
                # 只对价格管理员带数字，销售只看到"低于绝对底价"这一结论
                hard_hits.append(
                    f"明细 {item.sku_code_snapshot}：折人民币 ¥{price_cny:.2f}"
                    + (
                        f" 低于绝对底价 ¥{floor_cny:.2f}" if can_see_floor else " 低于公司绝对底价"
                    )
                )
            weighted_floor_total += floor_cny * item.quantity
            total_qty += item.quantity
        # 整单优惠摊到单价后的加权均价同样不得低于加权硬底
        if not hard_hits and total_qty:
            revenue_cny = (version.total_amount * fx) if foreign else version.total_amount
            if revenue_cny and revenue_cny > 0:
                avg_price = revenue_cny / total_qty
                avg_floor = weighted_floor_total / total_qty
                if avg_price < avg_floor - eps:
                    hard_hits.append(
                        f"整单（优惠摊后加权均价 ¥{avg_price:.2f}）"
                        + (
                            f"低于加权绝对底价 ¥{avg_floor:.2f}" if can_see_floor
                            else "低于公司加权绝对底价"
                        )
                    )
        if hard_hits:
            raise AppError(
                ErrorCode.PRICE_BELOW_HARD_FLOOR,
                "报价低于公司绝对底价，任何审批都无法通过，已拒绝提交（"
                + "；".join(hard_hits)
                + "）。合法出口：请价格管理员调整价格档位并留审计，"
                "或改走样品 / 清库存等特殊通道",
                422,
            )

    offending: list[dict] = []
    for item in items:
        base_cost = item.cost_snapshot + item.logistics_cost_snapshot
        price = item.quoted_price
        price_cny = (price * fx) if foreign else price
        if base_cost <= 0:
            # A06/D5：成本快照为 0 说明"没有成本记录"而不是"成本为零"。
            # 利润类判定（授权底价/负利润/利润率）全部无从谈起，停用；
            # 保护价是绝对口径，仍然生效。
            floor = item.minimum_price_snapshot
            if floor is not None and price_cny < floor - Decimal("0.0001"):
                item.approval_required = True
                item.approval_reason = (
                    f"报价 ¥{price_cny:.2f}（折人民币）低于最低保护价 ¥{floor:.2f}"
                    f"（该明细无成本记录，利润未评估）"
                )
                offending.append(
                    {
                        "sku_code": item.sku_code_snapshot,
                        "quoted_price": float(price),
                        "quoted_price_cny": float(price_cny),
                        "minimum_price": float(floor),
                        "profit_rate": None,
                    }
                )
            else:
                item.approval_required = False
                item.approval_reason = None
            continue
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

    # A10（方案 §7.1）：整单有效金额判定。
    # 逐项全过 ≠ 整体能过：整单优惠（is_discount 附加费）摊下来后，
    # 整单利润率/加权均价可能已跌破授权——不能逐项检查后就直接放行。
    revenue = version.total_amount
    if revenue and revenue > 0:
        # 存在无成本明细时，整单利润率等于"拿 0 成本算出来的"，不可信——
        # 利润率维度跳过（D5），加权保护价是绝对口径仍生效
        any_missing_cost = any(
            (item.cost_snapshot + item.logistics_cost_snapshot) <= 0 for item in items
        )
        revenue_cny = (revenue * fx) if foreign else revenue
        cost_total = sum(
            ((item.cost_snapshot + item.logistics_cost_snapshot) * item.quantity for item in items),
            ZERO,
        )
        total_qty = sum((item.quantity for item in items), ZERO)
        whole_margin = ((revenue_cny - cost_total) / revenue_cny) if revenue_cny else ZERO
        weighted_floor_hit = False
        if total_qty:
            weighted_min = sum(
                ((item.minimum_price_snapshot or ZERO) * item.quantity for item in items), ZERO
            ) / total_qty
            effective_avg = revenue_cny / total_qty
            weighted_floor_hit = effective_avg < weighted_min - Decimal("0.0001")
        if (
            not any_missing_cost
            and whole_margin < min_margin - Decimal("0.000001")
        ) or weighted_floor_hit:
            reason = (
                f"整单有效金额 ¥{revenue_cny:.2f}（含整单优惠）利润率 {whole_margin * 100:.2f}%"
                f"（授权 {min_margin * 100:.0f}%）"
            )
            if weighted_floor_hit:
                reason += "，或加权均价已低于加权保护价"
            offending.append(
                {
                    "sku_code": "整单（优惠后）",
                    "quoted_price_cny": float(revenue_cny),
                    "profit_rate": float(whole_margin),
                    "reason": reason,
                }
            )

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

    # ---- 审批规则引擎（设计稿 `_6` 的国内业务版）----
    # 走到这里说明有明细超权限。规则在金额分档**之前**求值，第一条命中的生效：
    #   auto_pass       → 提交即通过（留一条闭环审批单与规则痕迹）
    #   express         → 跳过高层级，一律第一级（主管）审
    #   exception_route → 正常分档 + 末尾追加会签节点
    # 没有规则命中 → 原金额分档逻辑不变。
    from app.modules.approval import rules_engine

    decision = await rules_engine.route(
        session, quote=quote, version=version, items=items, fx=fx
    )
    rule_trace: dict | None = None
    co_sign: dict | None = None
    force_first_level = False
    if decision is not None:
        rule_trace = decision.trace()
        rule_trace["effect"] = rules_engine.effect_summary(decision.kind, decision.action)
        if decision.kind == "auto_pass":
            version.approval_status = "approved"
            version.approved_at = datetime.now(UTC)
            quote.status = "approved"
            instance = ApprovalInstance(
                definition_id=definition.id,
                business_type="quote_version",
                business_id=version.id,
                applicant_id=applicant_id,
                status="approved",
                current_node=None,
                finished_at=datetime.now(UTC),
                summary={
                    "quote_no": quote.quote_no,
                    "version_no": version.version_no,
                    "reason": reason,
                    "offending": offending,
                    "total_amount": float(version.total_amount or 0),
                    "auto_passed": True,
                    "rule_trace": rule_trace,
                },
            )
            session.add(instance)
            await session.flush()
            session.add(
                ApprovalRecord(
                    approval_instance_id=instance.id,
                    node_code="rule_engine",
                    approver_id=applicant_id,
                    action="auto_pass",
                    comment=f"命中免审规则「{decision.rule_name}」：{rule_trace['effect']}",
                )
            )
            return instance, True
        if decision.kind == "express":
            force_first_level = True
        elif decision.kind == "exception_route":
            co_sign = {
                "role_codes": list(decision.action.get("add_node_role_codes") or ["finance"]),
                "label": decision.action.get("add_node_label") or "财务会签",
                "veto": bool(decision.action.get("veto", True)),
                "status": "pending",
            }

    # 审批分级：按报价总额决定走到哪一级，并记录这一级谁有权批（快照，避免中途改配置影响在途审批）
    levels = await settings_service.get_list(session, "approval_levels")
    amount = float(version.total_amount or 0)
    node = None
    if force_first_level and levels:
        node = levels[0]  # 极速通道：一律落到第一级（主管）
    else:
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

    summary = {
        "quote_no": quote.quote_no,
        "version_no": version.version_no,
        "reason": reason,
        "offending": offending,
        "authorized_min_margin": float(min_margin),
        "total_amount": amount,
        "node_label": node_label,
        "node_role_codes": node_roles,
    }
    if rule_trace:
        summary["rule_trace"] = rule_trace
    if co_sign:
        summary["co_sign"] = co_sign

    instance = ApprovalInstance(
        definition_id=definition.id,
        business_type="quote_version",
        business_id=version.id,
        applicant_id=applicant_id,
        status="pending",
        current_node=node_code,
        summary=summary,
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


async def notify_expired_quotes(session: AsyncSession) -> int:
    """报价有效期届满且未成单 → 给负责人建待办（文档 §3.4）。

    条件：已对客（sent/approved）+ 已过有效期 + 没有非取消订单引用它。
    "同一报价只提醒一次"按标题查*任何状态*的任务（完成/忽略过就不再建），
    否则销售每天都会收到同一条到期提醒。由每日自动任务调用（不新增调度项）。
    """
    from app.modules.order.model import SalesOrder
    from app.modules.task.model import Task

    today = datetime.now(UTC).date()
    ordered = select(SalesOrder.quote_id).where(
        SalesOrder.quote_id.is_not(None), SalesOrder.status != "cancelled"
    )
    rows = (
        await session.execute(
            select(Quote, Customer.owner_id)
            .join(Customer, Customer.id == Quote.customer_id)
            .where(
                Quote.deleted_at.is_(None),
                Quote.valid_until.is_not(None),
                Quote.valid_until < today,
                Quote.status.in_(("sent", "approved", "expired")),
                Quote.id.not_in(ordered),
            )
        )
    ).all()

    created = 0
    for quote, customer_owner_id in rows:
        # 派给**这张报价的负责人**，不是客户当前的负责人：
        # 协作单（客户归 A、这张报价由 B 做）按客户归属派会把提醒发给不相干的人，
        # 真正做这张报价的人反而收不到。报价没写负责人时才回退到客户负责人。
        owner_id = quote.owner_id or customer_owner_id
        if owner_id is None:
            # 报价与客户都没负责人 → 派不出去。别静默丢：留一条日志，
            # 否则"记得提醒我"永远不来、又查不出为什么（脏数据没有出口）。
            import logging

            logging.getLogger("crm.quote").warning(
                "报价 %s 已过期但无人可派（报价/客户都没有负责人），跳过建待办", quote.id
            )
            continue
        title = f"报价 {quote.quote_no} 已过有效期（{quote.valid_until}），请跟进续期或催单"
        existing = (
            await session.execute(select(Task.id).where(Task.title == title))
        ).scalar_one_or_none()
        if existing is not None:
            continue
        session.add(
            Task(
                title=title,
                task_type="followup",
                customer_id=quote.customer_id,
                # 带上 quote_id：待办能指回是哪张报价（task 表早有这一列，此前漏传）
                quote_id=quote.id,
                owner_id=owner_id,
                priority="high",
                status="pending",
                due_at=datetime.now(UTC),
                source="system",
                source_rule_id=None,
            )
        )
        created += 1
    await session.flush()
    return created
