"""报价业务逻辑：编号、明细快照、金额汇总、审批判定。"""

from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.core.timebase import today_business
from app.core.trade_mode import ensure_currency_allowed
from app.modules.approval.model import ApprovalDefinition, ApprovalInstance, ApprovalRecord
from app.modules.customer.model import Contact, Customer
from app.modules.opportunity.model import Opportunity, OpportunityItem
from app.modules.pricing import service as pricing_service
from app.modules.pricing.model import ExchangeRate
from app.modules.product import master as master_service
from app.modules.product.model import Product, Sku
from app.modules.quote.model import (
    PRICING_BASIS_ACTUAL_PASS_THROUGH,
    PRICING_BASIS_LABEL,
    QUOTE_STATUS_LABEL,
    Quote,
    QuoteCharge,
    QuoteItem,
    QuoteVersion,
)
from app.modules.settings import service as settings_service
from app.modules.user.model import User

ZERO = Decimal("0")

#: 运费的**费用分类码**。向客户收取的运费就是这一条 `QuoteCharge`
#: （2026-10-09 口径：不另建一套运费金额，避免两套金额都被加进总额）。
CHARGE_TYPE_LOGISTICS = "logistics"

#: 明细数量的列精度（`quote_items.quantity` 是 `Numeric(16, 3)`）。
QUANTITY_SCALE = Decimal("0.001")


def normalized_quantity(quantity: Decimal | None) -> Decimal:
    """把数量按**库列精度**（三位小数）归一后再参与计算（N01，2026-10-09 修）。

    为什么需要：`recalc_version` 的版本合计与 `serialize_item` 的明细金额，
    从前算的**不是同一个数** —— 合计拿 Python 内存里未落库的原值，
    明细拿读回来（已被列精度舍入过）的值。于是"数量 1.23456、单价 150"
    会出现明细 185.25、合计 185.18：同一张单两个数。

    入参侧已经拦住超过三位小数的数量（`QuoteItemInput.decimal_places=3`），
    这里再归一一次是**第二道保险**：任何绕过 schema 的写入路径（脚本、导入、
    将来新增的入口）都不会再造成"两个数对不上"。

    用 `ROUND_HALF_UP` 与 PostgreSQL 的 `numeric` 舍入保持一致，
    避免 Python 默认的银行家舍入（ROUND_HALF_EVEN）造成新的错位。
    """
    if quantity is None:
        return ZERO
    return Decimal(quantity).quantize(QUANTITY_SCALE, rounding=ROUND_HALF_UP)


def is_logistics_charge(charge: QuoteCharge) -> bool:
    """这条费用是不是"向客户收取的运费"。

    ⚠️ 只认分类码，**不按说明文字里含"运费"去识别**（口径明确要求）：
    说明是自由文本，写成"宁波到苏州运费"或"物流费"都不该改变金额归属，
    反过来把一句别的费用写成含"运费"的字样也不该被算成运费。
    折扣行永远不算运费（`is_discount=True` 走折扣那一档，折扣在库里是负数）。
    """
    return (
        not charge.is_discount
        and (charge.charge_type or "").strip().lower() == CHARGE_TYPE_LOGISTICS
    )


def mark_logistics_confirmed(charge: QuoteCharge, *, at: datetime | None = None) -> None:
    """把这条费用标成"运费已由业务确认"；不是物流费用则清空确认时刻。

    口径（2026-10-09）：**草稿允许尚未填写运费，正式发送时必须已经确认具体金额**。
    金额列本身分不出"没填"和"明确是零运费"（都是 0），所以另记一个确认时刻。

    - 是物流费用 → 打上确认时刻。**每次经接口保存都重新打一次**：改的是
      "已确认的实际运费"这个事实本身，拿改动前的时刻去背书改动后的金额是错的。
    - 不是物流费用（含折扣、或从物流改成了别的分类）→ 清空。留着旧的确认时刻
      会让一条**已经不是运费**的费用继续被当成"已确认的运费"，进而把正式发送放过去。
    """
    if is_logistics_charge(charge):
        charge.logistics_confirmed_at = at or datetime.now(UTC)
    else:
        charge.logistics_confirmed_at = None


def quote_is_expired(valid_until, *, today=None) -> bool:
    """A quote is expired only after its inclusive valid-through date.

    §9.10 复审：默认的"今天"取**业务日期**（北京时间），原来取 UTC 日期 ——
    北京时间 10-07 01:00 时 UTC 还停在 10-06，于是"有效期到 10-06"的报价
    被判成"没过期"，正式发送照放（那 8 小时里操作与展示各说各话）。
    """
    if valid_until is None:
        return False
    return valid_until < (today or today_business())


def _f(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


#: 报价明细里**属于公司内部成本口径**的字段：无 `price:manage` 的操作人不该拿到。
#:
#: 为什么连**利润**也要一起隐（issue #5）：报价 100、利润 80 就能反推出成本 20 ——
#: 只隐成本等于没隐。底价快照同理（它是由成本推出来的）。
#: 查价接口早已按 `price:manage` 隐藏成本，这里是让**所有读出口**与它同一口径。
_COST_ONLY_ITEM_FIELDS = (
    "cost_snapshot",
    "package_cost_snapshot",
    "logistics_cost_snapshot",
    "standard_price_snapshot",
    "recommended_price_snapshot",
    "minimum_price_snapshot",
    "profit_snapshot",
    "profit_rate_snapshot",
)

#: `price_source` 取这个值时，说明这条明细的成本是**业务员自己填的人工核价成本**
#: （定制项，`_build_custom_item_snapshot` 落的就是它）——那是操作人自己的输入，
#: 不是公司内部成本，隐了就没法编辑定制项。主人 2026-10-10 拍板：这一种不隐。
_SELF_ENTERED_COST_SOURCE = "custom_manual"


def serialize_item(item: QuoteItem, *, can_see_cost: bool = True) -> dict:
    """一条报价明细。

    `can_see_cost=False`（无 `price:manage`）时，**公司内部成本口径**的字段置空，
    包含由成本推出的利润/底价（否则能反推成本）。
    例外：`price_source='custom_manual'` 的人工核价成本是操作人自己的输入，照常返回。
    """
    # 无权限时默认全隐；`custom_manual` 只**放行成本本身**（业务员自己的输入），
    # 利润/底价不跟着放行 —— 它们由成本推出，放行等于把成本又泄回去。
    hide = not can_see_cost
    own_cost_only = hide and (item.price_source or "") == _SELF_ENTERED_COST_SOURCE
    data = {
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
        # §8.14：这条明细按哪一版 SKU 主数据算的（空＝当时还没有确认记录）
        "master_version_no": item.master_version_no,
        "quoted_price": _f(item.quoted_price),
        "amount": _f(item.quantity * item.quoted_price),
        "profit_snapshot": _f(item.profit_snapshot),
        "profit_rate_snapshot": _f(item.profit_rate_snapshot),
        "approval_required": item.approval_required,
        "approval_reason": item.approval_reason,
        "remark": item.remark,
    }
    if hide:
        for field in _COST_ONLY_ITEM_FIELDS:
            if own_cost_only and field == "cost_snapshot":
                continue  # 业务员自己填的人工核价成本：照常回填，否则定制项没法编辑
            data[field] = None
        if own_cost_only:
            # 包装/运费成本不是他的输入（系统按 SKU 算的），一并隐掉，
            # 免得从"成本 120 里含包装 5"这种拆分再反推内部口径
            data["package_cost_snapshot"] = None
            data["logistics_cost_snapshot"] = None
    return data


def serialize_charge(charge: QuoteCharge) -> dict:
    return {
        "id": charge.id,
        "charge_type": charge.charge_type,
        "description": charge.description,
        "amount": _f(charge.amount),
        "is_discount": charge.is_discount,
        "sort_no": charge.sort_no,
        "is_logistics": is_logistics_charge(charge),
        # 运费是否已被业务确认（金额为 0 不等于"已确认零运费"）：
        # 前端据此把"未填写"和"确认零运费"显示成两种状态。
        "logistics_confirmed_at": (
            charge.logistics_confirmed_at.isoformat()
            if charge.logistics_confirmed_at
            else None
        ),
    }


def amount_summary(version: QuoteVersion) -> dict:
    """金额汇总的**唯一一份公式**（页面、对客文件、订单共用，别在前端再算一遍）。

    口径（2026-10-09 运费分离）：

        产品货款   = Σ(产品单价 × 数量)          → `subtotal_amount`
        运费       = 物流费用条目合计             → `logistics_amount`
        其他费用   = 非物流、非折扣的费用合计      → `other_charge_amount`
        优惠       = 折扣（**库里是负数**，保持这个约定）
        应付合计   = 货款 + 运费 + 其他费用 + 优惠  → `total_amount`

    ⚠️ `total_amount` **已经含**运费与其他费用，任何人都不许再把它加上一遍。
    `charge_amount == logistics_amount + other_charge_amount` 是恒等式：
    拆的是同一笔钱，不是又加了一笔。
    """
    return {
        "goods_amount": _f(version.subtotal_amount),
        "logistics_amount": _f(version.logistics_amount),
        "other_charge_amount": _f(version.other_charge_amount),
        "charge_amount": _f(version.charge_amount),
        "discount_amount": _f(version.discount_amount),
        "total_amount": _f(version.total_amount),
        "currency": version.currency,
        # 单一口径（2026-10-09 统一）：一律"产品价不含运费、运费代收代付"。
        # 从前这里 `or PRICING_BASIS_LEGACY` 兜底，会把口径未知的行**当成含运费**，
        # 与统一后的实际算法相反 —— 改为按新口径兜底，兜底值只影响标签、不影响取数。
        "pricing_basis": version.pricing_basis or PRICING_BASIS_ACTUAL_PASS_THROUGH,
        "pricing_basis_label": PRICING_BASIS_LABEL.get(
            version.pricing_basis or PRICING_BASIS_ACTUAL_PASS_THROUGH, ""
        ),
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
        # 金额拆分（2026-10-09 运费分离）：运费从"附加费用"里单列出来。
        # ⚠️ 这三列都**已经含在** `total_amount` 里了，前端只能拿来分项展示，
        # 不要再加到总额上。恒等式：charge_amount == logistics + other_charge。
        "logistics_amount": _f(version.logistics_amount),
        "other_charge_amount": _f(version.other_charge_amount),
        # 本版的产品核价口径。**单一口径**（2026-10-09 统一）：
        # actual_pass_through = 产品价不含运费、运费原额代收代付。不再有 legacy 分支。
        "pricing_basis": version.pricing_basis or PRICING_BASIS_ACTUAL_PASS_THROUGH,
        "pricing_basis_label": PRICING_BASIS_LABEL.get(
            version.pricing_basis or PRICING_BASIS_ACTUAL_PASS_THROUGH, ""
        ),
        "currency": version.currency,
        # §8.7：**本版**对客有效期的快照（PDF / BizDoc 读的是它，不是主单）。
        # 单独输出是必要的：主单的 `valid_until` 与它快照是两个字段，
        # 页面上要能看出"版本入口改了有效期之后，快照跟着走了没有"。
        "valid_until_snapshot": version.valid_until_snapshot,
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
        # 币种跟着金额一起给（第九批 §9.9）：前端不能假设是人民币。
        # 没有当前版本时给 null，由前端提示"币种待核实"。
        "currency": version.currency if version else None,
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
    session: AsyncSession, user, quote_id: int, *, for_update: bool = False
) -> Quote:
    """取报价并校验数据范围（列表按 owner_id 过滤，详情此前没校验）。"""
    from app.core.data_scope import ensure_in_scope

    if for_update:
        # 统一按商机 → 报价 → 版本加锁，与确认成交入口保持一致。
        # 正式发送会推进商机，反向加锁会与确认成交形成死锁。
        initial = await get_quote_or_404(session, quote_id)
        await ensure_in_scope(session, user, owner_id=initial.owner_id, label="报价单")
        if initial.opportunity_id:
            await session.execute(select(Opportunity).where(
                Opportunity.id == initial.opportunity_id
            ).with_for_update())
        quote = (await session.execute(select(Quote).where(Quote.id == quote_id)
                 .with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
        if quote is None or quote.deleted_at is not None:
            raise AppError(ErrorCode.NOT_FOUND, "报价单不存在", 404)
    else:
        quote = await get_quote_or_404(session, quote_id)
    await ensure_in_scope(session, user, owner_id=quote.owner_id, label="报价单")
    return quote


async def get_version_or_404(session: AsyncSession, version_id: int) -> QuoteVersion:
    version = await session.get(QuoteVersion, version_id)
    if version is None:
        raise AppError(ErrorCode.NOT_FOUND, "报价版本不存在", 404)
    return version


async def get_visible_version(
    session: AsyncSession, user, version_id: int, *, for_update: bool = False
) -> QuoteVersion:
    """取报价版本并校验其所属报价在数据范围内（版本自己没有负责人）。"""
    version = await get_version_or_404(session, version_id)
    await get_visible_quote(session, user, version.quote_id, for_update=for_update)
    if for_update:
        # 先锁单据再锁版本；锁等待结束后重读，不能按等待前的状态执行。
        version = (await session.execute(select(QuoteVersion).where(QuoteVersion.id == version_id)
                   .with_for_update().execution_options(populate_existing=True))).scalar_one()
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
    """取该币种对本币（CNY）的**当前已生效**汇率，用于报价版本快照（02-ER §11）。

    内贸（CNY/空）不需要汇率，直接返回 (None, None)。
    找不到汇率时返回 (None, 说明)，由调用方决定是报错还是提示——
    不静默按 1:1 处理，否则外贸报价会悄悄算错一个数量级。

    ⚠️ **必须排除未来才生效的汇率**（第十一批 11.4）：汇率是时点数据，维护时
    允许"提前录一条 2030 年生效的"。原实现只按 `effective_at` 倒序取最新一条，
    于是那条 2030 年的会被**今天的报价**用上（实测：今天生效 7、2030 年 99，
    报价快照存的是 99），而且来源会显示成那条未来记录 —— 报价当场就错了。
    正确口径：在**报价时刻已经生效**的记录里取最新一条。
    """
    code = (currency or "CNY").upper()
    if code == "CNY":
        return None, None
    now = datetime.now(UTC)
    row = (
        await session.execute(
            select(ExchangeRate)
            .where(
                ExchangeRate.quote_currency == code,
                ExchangeRate.base_currency == "CNY",
                ExchangeRate.effective_at <= now,
            )
            .order_by(ExchangeRate.effective_at.desc(), ExchangeRate.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        # 区分"一条都没有"与"只有未来才生效的"：后者要让人知道是**时间**没到，
        # 而不是"没维护"——否则会去重复录一条，或者以为系统坏了。
        pending = (
            await session.execute(
                select(ExchangeRate.effective_at)
                .where(
                    ExchangeRate.quote_currency == code,
                    ExchangeRate.base_currency == "CNY",
                    ExchangeRate.effective_at > now,
                )
                .order_by(ExchangeRate.effective_at.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if pending is not None:
            return (
                None,
                f"币种 {code} 只有一条 {pending:%Y-%m-%d %H:%M} 才生效的汇率，"
                "现在还没有生效的汇率；请维护一条当前生效的，或等它生效后再报价",
            )
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
    unconfirmed_out: set[str] | None = None,
) -> dict:
    """从商机（或直接给客户）生成报价单 + V1 版本 + 明细。

    这段逻辑原本写在路由里；抽到 service 是为了让 Agent 工具
    （`create_quote_draft`）和 HTTP 接口共用同一套实现，
    否则两处各写一遍，改价规则时必然漂移。
    调用方负责审计与 commit。
    """
    # 业务口径闸（2026-10-08）：口径是「只做国内」时，报价币种只能是人民币。
    # 放在最前面：这一步不看任何数据、只读一个配置，最便宜，也避免"先建了壳
    # 才发现币种不允许"（报价建壳、明细、快照都在后面）。
    # 复制报价走 `source_currency`（从源版本继承）也会经过这里 —— 那是刻意的：
    # 口径变了之后，历史外币报价不该再复制出新的外币报价。
    currency = await ensure_currency_allowed(session, currency, label="报价币种")

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
        from app.modules.opportunity import service as opportunity_service
        opportunity = await opportunity_service.get_visible_opportunity(session, user, opportunity.id)
        customer_id = opportunity.customer_id

    # 客户/联系人必须存在。放在 service 而不是路由：Agent 工具
    # （`create_quote_draft`）直接调这里，绕开路由校验；漏了就会撞 FK
    # 约束报 500，而不是可读的 40401。
    from app.modules.customer import service as customer_service
    # 接住客户对象：创建版本时要把「当时的客户抬头」钉进版本快照（审查 2026-10-07）。
    customer = await customer_service.get_visible_customer(session, user, customer_id)
    resolved_contact_id = contact_id or (
        opportunity.primary_contact_id if opportunity else None
    )
    contact = None
    #: 报价**之前**就该攒的提醒（`item_warnings` 要到明细循环那里才建，所以分开攒，
    #: 最后一起返回）。
    early_warnings: list[str] = []
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
        # 已删除的联系人：**放行但显性提醒**（主人 2026-10-09 拍板的口径）。
        #
        # 为什么不硬拦：联系人不只由人手工选，还会从**定制需求**自动带过来
        # （`inquiry.contact_id`）。如果那个人离职后被删，硬拦会让"从这条需求建报价"
        # 这条**正常业务**直接卡死，出路只有把已删联系人重新加回来（留假数据）。
        # 而报价后面本来就要人工过一遍（填明细、改价、提交审批），多一条提醒不增加负担。
        #
        # 与"主数据未确认"同一口径：**默认放行 + 如实提示**，不静默。
        # 界面已经会逐条弹 `warnings`（`QuoteListPage` 的 onSuccess）。
        if contact.deleted_at is not None:
            early_warnings.append(
                f"联系人「{contact.name}」已被删除，请确认是否仍要发给这位；"
                f"如已换人，请在报价上改选该客户的有效联系人"
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
        session,
        "default_delivery_terms",
        "text",
        # 代码回退值**直接取自配置默认值**，不手写第二遍：
        # 从前这里是同一句话的副本，改口径时极易漏掉一处（种子脚本就漏过，
        # 见 `settings_service.DEFAULT_DELIVERY_TERMS` 的说明）。
        settings_service.DEFAULT_SETTINGS["default_delivery_terms"]["text"],
    )

    quote = Quote(
        quote_no=await generate_quote_no(session),
        opportunity_id=opportunity.id if opportunity else None,
        customer_id=customer_id,
        contact_id=contact_id or (opportunity.primary_contact_id if opportunity else None),
        owner_id=(opportunity.owner_id if opportunity else None) or user.id,
        status="draft",
        valid_until=valid_until
        or (today_business() + timedelta(days=valid_days)),
        created_by=user.id,
    )
    session.add(quote)
    await session.flush()

    version = QuoteVersion(
        quote_id=quote.id,
        version_no=1,
        # 新报价一律用新口径：产品单价不含运费，运费按实际金额代收代付。
        # 显式写出来而不是靠列默认值 —— 口径是业务决定，该在代码里看得见。
        pricing_basis=PRICING_BASIS_ACTUAL_PASS_THROUGH,
        payment_terms=payment_terms or default_payment,
        delivery_terms=delivery_terms or default_delivery,
        # 抬头与有效期在这里**定格**（审查 2026-10-07 修）：出对客文件时不再实时读
        # `customers.name` / `contacts.name` / `quotes.valid_until`，否则客户改名、
        # 主单有效期改动之后，同一版本重出会印成"今天的样子"，与当初发给客户的那份对不上。
        customer_name_snapshot=customer.name if customer else None,
        contact_name_snapshot=contact.name if contact else None,
        valid_until_snapshot=quote.valid_until,
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
            # 无适用价时客户目标价只能作为谈判参考，不能落成拟报价金额。
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
                item_warnings.append(
                    f"SKU {sku_label}：无系统适用价（待定价），客户目标价"
                    f"{target_price if target_price is not None else '未提供'} 仅作参考；"
                    "未加入报价明细，请在报价页手工定价后添加"
                )
                # 目标价不是客户承诺价；如果在这里写入 QuoteItem，后续审批/发送
                # 会把谈判参考当成正式拟报价。缺价行留在商机需求中，由销售明确
                # 定价后再添加到报价版本，避免生成金额看似完整的错误报价。
                continue
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
                unconfirmed_out=unconfirmed_out,
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
        # 先攒的（联系人已删除等）+ 明细循环里攒的，一起交出去
        "warnings": early_warnings + item_warnings,
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
    quote = await get_visible_quote(session, user, quote.id, for_update=True)
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

    # 新版本的抬头与有效期**按创建这一刻**定格（不是照抄上一版）：
    # 客户改了名，现在发出去的新版本就该印新名字，而上一版继续保留它当时的名字。
    customer = await session.get(Customer, quote.customer_id)
    contact = await session.get(Contact, quote.contact_id) if quote.contact_id else None
    version = QuoteVersion(
        quote_id=quote.id,
        version_no=latest.version_no + 1,
        # 建立新版一律切到新口径（2026-10-09）。这里是**刻意的转换点**：
        # 历史版本的单价里可能含运费，但那些版本留在 legacy 上不动；
        # 新版本是重新核过价的，所以按新口径来。复制旧明细时若旧单价含义
        # 无法确认，由调用方提示人工核实（`version_logistics_in_base_cost` 管口径，
        # 不替业务判断历史金额的含义）。
        pricing_basis=PRICING_BASIS_ACTUAL_PASS_THROUGH,
        currency=source.currency,
        payment_terms=source.payment_terms,
        delivery_terms=source.delivery_terms,
        customer_name_snapshot=customer.name if customer else None,
        contact_name_snapshot=contact.name if contact else None,
        valid_until_snapshot=quote.valid_until,
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
                # 单位快照跟着复制：漏了它，新版本一建出来就对客文件显示"待核实"
                unit_snapshot=item.unit_snapshot,
                # §8.14：主数据版本号一并带过去 —— 新版本复制自上一版，
                # 明细没重新取值，追溯口径就该还是原来那一版
                master_version_no=item.master_version_no,
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
    await copy_version_charges(session, source_id=source.id, target_version=version)
    await session.flush()
    # 复制过来的明细快照是**按源版本口径**算的，而新版本已经切到当前口径
    # （2026-10-09 统一后只有一套）。不重算就会留下"口径标着 A、数字是 B"的自相
    # 矛盾数据（审查实测：显示利润 15、按新口径应为 20）。只重算受口径影响的四列，
    # 售价与价格来源不动 —— 详见 `apply_current_basis_to_item` 的说明。
    for copied in await version_items(session, version.id):
        await apply_current_basis_to_item(session, copied, version, user=user)
    await session.flush()
    await recalc_version(session, version)
    quote.current_version_id = version.id
    quote.status = "draft"
    await close_superseded_approvals(session, quote=quote, new_version=version, user=user)

    # 版本沿用报价的币种；汇率按「本版本创建时点」重新取快照。
    rate_warning = await apply_exchange_rate_snapshot(
        session, version, currency=source.currency, explicit_rate=None
    )
    if rate_warning:
        raise AppError(ErrorCode.PARAM_ERROR, rate_warning, 422)
    return version


async def close_superseded_approvals(session: AsyncSession, *, quote: Quote,
                                     new_version: QuoteVersion, user) -> None:
    """新版本取代旧方案：结束旧版待审，保留审批历史，不改已完成结论。"""
    from app.modules.approval.model import ApprovalInstance, ApprovalRecord
    from app.core.audit import write_audit

    old_versions = list((await session.execute(select(QuoteVersion).where(
        QuoteVersion.quote_id == quote.id, QuoteVersion.id != new_version.id
    ).with_for_update())).scalars())
    old_map = {row.id: row for row in old_versions}
    instances = list((await session.execute(select(ApprovalInstance).where(
        ApprovalInstance.business_type == "quote_version",
        ApprovalInstance.business_id.in_(old_map), ApprovalInstance.status == "pending",
    ).with_for_update().execution_options(populate_existing=True))).scalars())
    for instance in instances:
        old = old_map[instance.business_id]
        reason = f"建立 V{new_version.version_no}，自动结束 V{old.version_no} 的待审批流程"
        instance.status = "withdrawn"
        instance.finished_at = datetime.now(UTC)
        instance.current_node = None
        instance.summary = {**(instance.summary or {}), "closed_reason": "superseded",
                            "superseded_by_version_id": new_version.id, "close_note": reason}
        if (instance.summary or {}).get("co_sign"):
            instance.summary = {**instance.summary, "co_sign": {**instance.summary["co_sign"], "status": "withdrawn"}}
        old.approval_status = "not_submitted"
        session.add(ApprovalRecord(approval_instance_id=instance.id, node_code="superseded",
                                  approver_id=user.id, action="withdraw", comment=reason))
        await write_audit(session, operator_id=user.id, action="supersede_approval",
                          business_type="quote", business_id=quote.id,
                          after={"approval_instance_id": instance.id, "old_version_id": old.id,
                                 "new_version_id": new_version.id, "reason": reason})


async def _build_custom_item_snapshot(
    session: AsyncSession,
    *,
    version: QuoteVersion,
    role_codes: list[str],
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
    # 这一版的口径：新报价的运费不进成本基数；历史版本沿用含运费的老口径。
    basis_includes_freight = version_logistics_in_base_cost(version)
    # ⚠️ 最低利润率必须与 SKU 路径、与审核引擎**同一个来源**（2026-10-09 与主人确认）。
    #
    # 从前这里读的是系统配置 `default_min_margin`，而"建立新版 / 复制报价"的重算
    # 读的是**操作人角色权限**（`resolve_min_margin`）—— 同一个管理员、同一张定制
    # 报价，换条路径就换一套算法：新建时底价 92（配置 15%），建新版变成 80（角色 0%）。
    # 后果是**价格权限表里配的 0% 对新建报价不起作用、对建新版反而起作用**，
    # 界面上看起来配好了、实际只在部分路径生效 —— 这不是设计选择，是漏改。
    #
    # "角色没配价格权限时退回系统配置"这层兜底在 `resolve_min_margin` 里已经有了，
    # 所以统一之后并不会失去"没人配过就用 15%"的保护。
    min_ratio, _can_approve = await pricing_service.resolve_min_margin(
        session, role_codes or []
    )
    # 保护价基础 = **商品成本**，不含运费（2026-10-09「产品价格与运费分离」）。
    # 定制项的成本是人工填的"商品成本"，运费同样由客户全额承担、公司代收代付，
    # 所以运费既不能抬高成本推高底价，也不能被当成利润。与 SKU 路径同一口径。
    basis_cost = (cost + freight) if basis_includes_freight else cost
    # ⚠️ 用**引擎那个公式**（成本 ÷ (1 − 最低利润率)），不是 成本 × (1 + 率)
    # （2026-10-09 与主人确认"按角色权限统一"时一并纠正）。
    #
    # 乘法表达不出"最低利润率"这个下限：成本 80、率 15% 时它只给 92，
    # 而 92 的利润率是 12/92 = **13.04%**，根本不到 15%；
    # 除法给 94.12，利润率正好 15%。所以乘法那条**不是"另一种口径"，是算错**。
    # 同一笔业务换条路径就换个数（新建 92 / 新版 94.12），根子就在这里。
    minimum_price = (
        (basis_cost / (Decimal(1) - min_ratio)).quantize(Decimal("0.01"))
        if min_ratio < 1
        else basis_cost.quantize(Decimal("0.01"))
    )  # 人民币
    # 利润必须与报价同币种：外币单要先把人民币成本折过去再减，
    # 否则会算出"50 美元 − 350 人民币"这种假数字（与定价服务 cost_in_quote_currency 同口径）
    fx = version.exchange_rate_snapshot
    foreign = (version.currency or "CNY").upper() != "CNY" and bool(fx) and fx > 0
    cost_in_quote = (basis_cost / fx) if foreign else basis_cost
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
        # 定制项没有 SKU，也就没有系统单位可冻结（§8.7）：留空，
        # 对客文件上显示"待核实"，不拿别的 SKU 的单位凑一个数字上去
        unit_snapshot=None,
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


def _pick_confirmed(confirmed: dict[str, str], field: str, fallback):
    """字段来源：**按"确认过没有"选，不按"值空不空"**。

    已确认过（键存在）就用确认值 —— 哪怕确认的就是空串。例：某产品本来没有规格，
    人工确认「规格 = 空」就是对公司认可结论的确认，明细必须原样印这个空值；
    退回本地值等于替客户换了个口径。

    原写法 `confirmed.get(field) or fallback` 把"确认为空"和"没确认过"混成一档
    （空串是 falsy），于是：确认为空 → 本地 SKU 后填一个新规格 → 新明细悄悄用上
    那个**未确认**的值，而 `master_version_no` 仍指着旧快照；正式发送的校验只查
    "快照里有没有这个键"，看不出值已经被换过 → 一路放行到 `sent`
    （§8.14 复审第四轮的反例）。

    只有"从没确认过"（键都不存在）才退回本地值。
    """
    if field in confirmed:
        return confirmed[field]
    return fallback


def version_logistics_in_base_cost(version: QuoteVersion) -> bool:
    """这一版的产品核价，运费要不要算进基础成本。

    **2026-10-09 统一口径后恒为 `False`** —— 所有报价一律"产品单价不含运费、
    运费按已确认的实际金额由公司代收代付"。从前这里按 `pricing_basis` 分两条路
    （`legacy` 含运费 / `actual_pass_through` 不含），审查实测出四条缺陷，根因都是
    "两套口径并存"：审批毛利率把"单价之和"与"整单运费"混着减、建新版时口径切了
    快照照抄、对客文案不知道该不该写"不含运费"。

    保留这个函数（而不是把调用点全删成常量）有两个原因：
    ① 调用点集中在 8 处，留一个语义明确的名字比散落的 `False` 好读；
    ② 将来若真要把"运费进产品价"作为**另一种可选计价方式**加回来，
       改这一处即可，调用点不用动。

    参数保留是为了调用点签名不变；`version` 现在不参与判断。
    """
    return False


def item_unit_product_cost(item: QuoteItem, *, includes_freight: bool) -> Decimal:
    """一条明细的**单件产品核价成本**（审批判定、整单利润、摘要共用这一份）。

    口径（2026-10-09「产品价格与运费分离」）：

    - `includes_freight=False`（新报价）：只算 `cost_snapshot`（商品成本）。
      运费由客户全额承担、公司代收代付，**不进产品利润**——拿它当成本会
      把"运费收得多"变成"利润被吃掉"，进而误触低价审批；反过来也不能靠
      高运费收入去抵消产品低价（口径明确禁止）。
    - `includes_freight=True`（历史版本）：`cost_snapshot + logistics_cost_snapshot`。
      老版本的 `logistics_cost_snapshot` 是"单件运费"、当时确实参与了产品定价，
      回看/重算老版本必须沿用，否则会得出与当时不同的低价比对结论。

    调用方一律用 `version_logistics_in_base_cost(version)` 传这个开关，
    **不要在这个函数里自己去读 version** —— 审批引擎拿到的是明细列表 +
    一个显式的 fx，它没有版本对象。
    """
    cost = item.cost_snapshot or ZERO
    if includes_freight:
        cost = cost + (item.logistics_cost_snapshot or ZERO)
    return cost


async def apply_current_basis_to_item(
    session: AsyncSession,
    item: QuoteItem,
    version: QuoteVersion,
    *,
    user,
) -> None:
    """把**受核价口径影响**的派生快照按当前口径重算一遍（2026-10-09 缺陷③）。

    为什么需要：建立新版 / 复制报价 / 改明细，这几条路都会沿用源明细的快照值。
    口径切换时若照抄，会留下"口径标着 A、数字是 B"的自相矛盾数据
    （审查实测：显示利润 15、按新口径应为 20）。

    **只动受口径影响的四列**，其余快照（售价、标准价、建议价、价格来源、
    客户等级、主数据版本号……）原样保留 —— 刻意**不**重跑 `calculate_price`：
    它按**当前**价格规则与客户等级取值，复制一张旧报价时会顺手把售价改掉，
    那是另一种数据损坏。

    ## 底价必须沿用核价引擎的公式（2026-10-09 审查 P1 修）

    我上一版把底价写成 `成本 × (1 + 最低利润率)`，**两处都错**：

    1. **公式反了**：引擎用的是 `成本 ÷ (1 − 最低利润率)`
       （`pricing/service.py` 的 `floor_from_margin`），不是乘 `(1 + 率)`；
    2. **丢了公司保护价**：引擎的底价是
       `max(利润率反推价, 保护价)`，而保护价来自价格规则/客户特殊价的
       `minimum_price`。我上一版完全没考虑它。

    后果不是"数字难看"，而是**降低审批门槛**：审查实测产品成本 80、公司保护价 99、
    拟报价 95 —— 原版本底价 99 会触发审批，我那个错误公式算出 92，
    于是"不需要审批、直接通过"。低价就这么被放过去了。

    所以这里**照抄引擎的取数与公式**（`resolve_min_margin` / `find_price_rule` /
    `find_customer_price` 全部复用，不另写一套）：
    底价 = `max(成本 ÷ (1 − 最低利润率), 保护价)`，人民币、两位小数。

    ## 已知局限（与上面那个 bug 不是一回事）

    保护价取的是**当前生效**的规则，不是快照当时那一版。规则后来被改过时，
    这里会得到"按今天的规则"的底价。选择这样做，而不是"用现价重跑整条核价"，
    是因为前者只动底价、不改售价；后者会连带改掉已经报给客户的单价。
    """
    import logging as _lg
    _lg.getLogger("probe").warning("RECALC item=%s sku=%s prev_floor=%s", item.id, item.sku_id, item.minimum_price_snapshot)
    price = item.quoted_price or ZERO
    basis_cost = item_unit_product_cost(
        item, includes_freight=version_logistics_in_base_cost(version)
    )
    # 最低利润率也走引擎那一个函数：它按**操作人的角色价格权限**取最宽松值，
    # 角色没配时退回 default_min_margin —— 自己读设置会与核价结果不一致。
    min_margin, _can_approve = await pricing_service.resolve_min_margin(
        session, getattr(user, "roles", None) or []
    )

    # ---- 底价：**逐步照抄核价引擎的取数与公式** ----
    #
    # 引擎（`pricing_service.calculate_price`）的算法是：
    #   resolved_level = 客户等级（客户没等级时为空）
    #   rule = find_price_rule(customer_level=resolved_level)
    #   若命中的等级规则**没有指导价** → 回退取一般规则（customer_level=None）
    #   protection_price = max(rule.minimum_price, customer_rule.minimum_price)
    #   floor_from_margin = 成本 ÷ (1 − 最低利润率)
    #   floor_price = max(floor_from_margin, protection_price)
    #
    # ⚠️ 我上一版只写了 `find_price_rule(customer_level=None)` —— 那取的是**一般规则**，
    # 而保护价通常挂在**等级规则**上，于是保护价永远取不到、底价掉到利润率反推价
    # （实测：成本 80、保护价 99、拟报价 95 —— 原版本底价 99，新版算出 80，
    #  审批门槛被降低）。所以这里连"等级规则没指导价才回退一般规则"这一步也要照做。
    floor_from_margin = (
        basis_cost / (Decimal(1) - min_margin) if min_margin < 1 else None
    )
    protection_candidates: list[Decimal] = []
    if item.sku_id is not None:
        quantity = item.quantity or ZERO
        quote_row = await session.get(Quote, version.quote_id)
        customer_id = quote_row.customer_id if quote_row else None
        customer = await session.get(Customer, customer_id) if customer_id else None
        resolved_level = customer.level if customer else None

        rule = await pricing_service.find_price_rule(
            session,
            sku_id=item.sku_id,
            quantity=quantity,
            customer_level=resolved_level,
        )
        if (
            resolved_level
            and rule is not None
            and rule.customer_level == resolved_level
            and rule.guide_price is None
        ):
            general_rule = await pricing_service.find_price_rule(
                session,
                sku_id=item.sku_id,
                quantity=quantity,
                customer_level=None,
            )
            if general_rule is not None and general_rule.guide_price is not None:
                rule = general_rule
        customer_rule = (
            await pricing_service.find_customer_price(
                session,
                customer_id=customer_id,
                sku_id=item.sku_id,
                quantity=quantity,
            )
            if customer_id
            else None
        )
        protection_candidates = [
            value
            for value in [
                rule.minimum_price if rule else None,
                customer_rule.minimum_price if customer_rule else None,
            ]
            if value is not None
        ]
    protection_price = max(protection_candidates) if protection_candidates else None
    floors = [v for v in (floor_from_margin, protection_price) if v is not None]
    fresh_floor = max(floors).quantize(Decimal("0.01")) if floors else None

    # ---- 「只紧不松」：底价不得低于这条明细**原来那一版**（2026-10-09 与主人确认）----
    #
    # 保护价写在价格规则里，运营随时会改；而报价里冻住的那一份是**当年**的值。
    # 两者不一致时取哪个，是个真实取舍（口径已与主人对齐：**只紧不松**）：
    #
    #   情形 A：保护价从 99 降到 92
    #     · 只按今天的规则算 → 底价跟着降到 92 → 原来要审批的 95 元报价
    #       **变成免审批直接过** —— 这就是主人要我修的那类"降低审批门槛"；
    #     · 取 max(今天, 当年) → 仍是 99 → 门槛守住。
    #   情形 B：保护价从 99 涨到 105
    #     · max 取 105 → 门槛变严，守公司当前底线（这是应该的）。
    #
    # 所以这里用 `max(按今天算的, 原来那一版)`：**底价只会升或持平，绝不会降**。
    # 代价：保护价降过之后，老报价仍按老底价卡着 —— 但它当初就是按那个底价批的，
    # 这样反而自洽（"我批过的东西不该悄悄变便宜"）。
    #
    # 为什么拿 `item.minimum_price_snapshot` 当"当年那份"就够：
    # 它本身已经是当年的 `max(成本反推, 保护价)`，所以再取一次 max 等价于
    # `max(今天成本反推, 今天保护价, 当年成本反推, 当年保护价)` —— 语义正确且少查一次规则。
    # 老数据该列为 NULL 时不参与比较（只按今天的算），不会凭空把底价抬起来。
    previous_floor = item.minimum_price_snapshot
    if previous_floor is not None and (
        fresh_floor is None or previous_floor > fresh_floor
    ):
        item.minimum_price_snapshot = previous_floor
    else:
        item.minimum_price_snapshot = fresh_floor

    # ---- 利润：与报价同币种（人民币成本折过去再减）----
    fx = version.exchange_rate_snapshot
    foreign = (version.currency or "CNY").upper() != "CNY" and bool(fx) and fx > 0
    cost_in_quote = (basis_cost / fx) if foreign else basis_cost
    profit = price - cost_in_quote
    item.profit_snapshot = profit
    item.profit_rate_snapshot = (
        (profit / price) if price else ZERO
    ).quantize(Decimal("0.000001"))
    item.profit_with_refund_snapshot = profit + (item.tax_refund_snapshot or ZERO)


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
    unconfirmed_out: set[str] | None = None,
) -> QuoteItem:
    """生成一条报价明细：成本、标准价、最低价、利润全部落成快照。

    `sku_id` 为空即定制项（场景09）：转 `_build_custom_item_snapshot`，
    那条路径不需要价格规则，靠人工核价的成本与报价。

    成本口径（与 02-ER §11 的分层保持一致，别改坏）：
      cost_snapshot            = 商品成本（采购+生产+包装+加工）
      package_cost_snapshot    = 包装成本（是商品成本的组成部分，用于展示拆解，不重复计入）
      logistics_cost_snapshot  = 单件运费（独立一层，**旧口径**的产物）

    **产品核价成本怎么取，看这一版的口径**（2026-10-09「产品价格与运费分离」）：
      - 新口径 `actual_pass_through`：= `cost_snapshot`（商品成本），运费不参与
        产品定价与产品利润 —— 运费由客户全额承担、公司原额代收代付，不赚不赔；
      - 旧口径 `legacy`：= `cost_snapshot + logistics_cost_snapshot`（当时运费
        确实进了产品价，回看老版本必须沿用）。统一走 `item_unit_product_cost`。

    所以 `logistics_cost_snapshot` 在新口径下**仍然照常落库**（保留这层信息、
    也供历史口径回看），只是不再参与产品定价与利润计算。历史快照不批量清零。

    汇率与退税：按报价版本上快照的币种/汇率核价，并把结果一并落成快照，
    否则外贸报价会静默按人民币口径算（此前汇率快照字段一直没被写入）。

    §8.14（本轮接线）：报价明细的名称/规格/单位改从**已确认的主数据版本**取。
    口径是"默认放行 + 如实提示"——字段权威表目前整表为空（业务未拍板），
    所以有确认值就用确认值，没有的照常用本地 SKU 值，并把未确认的字段名
    收进 `unconfirmed_out` 交给调用方如实提示，既不静默也不阻断报价。
    """
    if sku_id is None:
        return await _build_custom_item_snapshot(
            session,
            version=version,
            role_codes=role_codes,
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
    # 停用/删除的 SKU 不能进报价明细（issue #7：从前只判 `deleted_at`，
    # 停用后仍能加进新报价）。
    from app.modules.product.service import require_available_sku

    sku = await require_available_sku(session, sku_id)
    product = await session.get(Product, sku.product_id)

    # §8.14 接线点。`resolve_confirmed_master` 与闸门版同源，只差"缺确认不抛错"。
    # ⚠️ **显式传对客三字段**：默认是全部 13 个，会让"颜色/长宽高/起订量…"
    # 也被算成"尚未确认"，从而提示用户一堆与报价无关的字段。
    # 快照真正用的也只有这三个（见下面的 `_pick_confirmed`）。
    master = await master_service.resolve_confirmed_master(
        session, sku_id, list(master_service.QUOTE_DISPLAY_FIELDS)
    )
    confirmed = master["values"]
    if unconfirmed_out is not None and master["unconfirmed"]:
        unconfirmed_out.add(
            f"{sku.sku_code} 的「{'、'.join(master['unconfirmed_labels'])}」主数据尚未确认"
        )

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
        # 运费是否进产品核价基数 —— 由**这一版的口径**决定，不由全局开关决定。
        # 新报价 False（运费代收代付，不抬高产品价与利润）；历史版本 True（老口径）。
        logistics_in_base_cost=version_logistics_in_base_cost(version),
    )
    if quoted_price is not None:
        if Decimal(str(quoted_price)) <= 0:
            raise AppError(ErrorCode.PARAM_ERROR, "拟报价单价必须大于 0", 422)
        price = Decimal(str(quoted_price))
    elif (
        (result.get("customer_price_rule") or {}).get("agreed_price") is not None
        or (result.get("price_rule") or {}).get("guide_price") is not None
    ) and result["recommended_price"] is not None:
        # 未传拟报价时只允许采用已维护的客户专属价/指导价；成本反推值仅供内部
        # 试算，不得因为它出现在 recommended_price 中就自动写成对客报价。
        price = Decimal(str(result["recommended_price"]))
    else:
        # A06/D5：缺少已批准销售价时，不能把成本推算建议变成正式报价。
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"SKU {sku.sku_code} 无适用售价；成本试算不能作为正式报价，请维护指导价或手工填写拟报价",
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
        # 名称/规格/单位用**已确认**的主数据值（§8.14）：确认过的口径才代表
        # "公司认可的这条 SKU 叫什么、规格是什么、单位是什么"。
        # 判据是"这个字段**确认过没有**"（见 `_pick_confirmed`），不是"值空不空" ——
        # 已确认为空必须保留为空，只有从没确认过才退回本地值；否则一个未确认的
        # 新本地值会被悄悄印给客户，而校验只看"快照里有没有这个键"，拦不住。
        sku_name_snapshot=_pick_confirmed(
            confirmed, "name", sku.name or (product.name if product else None)
        ),
        spec_snapshot=(
            spec_snapshot
            if spec_snapshot
            else _pick_confirmed(confirmed, "specification", sku.specification)
        ),
        # 单位同样先看确认值（§8.7 的单位快照 + §8.14 的确认优先）：
        # 之后 SKU 单位改了，旧版本的对客表也不会跟着变
        unit_snapshot=_pick_confirmed(confirmed, "unit", sku.unit),
        # §8.14：把"这条明细用的是哪一版主数据"一并落快照（0 → NULL＝当时还没确认过）
        master_version_no=master["version_no"] or None,
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
    """重算版本金额：货款、运费、其他费用、折扣、总额（以及运费确认状态）。

    总额公式**没有变**（与订单侧同一口径）：

        应付合计 = 货款 + 附加费用 + 折扣
                 = 货款 + (运费 + 其他费用) + 折扣

    这里额外落两个**拆分**列（`logistics_amount` / `other_charge_amount`），
    只是为了让页面和文件能把运费从"附加费用"里单列出来。

    ⚠️ 三条恒等式，改这个函数时不要破坏：
      1. `charge_amount == logistics_amount + other_charge_amount`
         （拆的是同一笔钱，不是又加了一笔）；
      2. `total_amount` 里**已经含**运费 —— 任何地方都不要再把 `logistics_amount`
         加到 `total_amount` 上，那是重复计费；
      3. 运费与折扣互斥：`is_discount=True` 的费用无论分类码是什么都进折扣，
         不参与运费汇总（折扣在库里是负数，保持这个约定）。
    """
    items = (
        await session.execute(
            select(QuoteItem).where(QuoteItem.quote_version_id == version.id)
        )
    ).scalars().all()
    # 数量按库列精度归一再求和：与 `serialize_item` 的明细金额取**同一个数**，
    # 否则同一张单会出现"明细 185.25、合计 185.18"（N01）。
    subtotal = sum(
        (normalized_quantity(item.quantity) * item.quoted_price for item in items), ZERO
    )
    charges = (
        await session.execute(
            select(QuoteCharge).where(QuoteCharge.quote_version_id == version.id)
        )
    ).scalars().all()
    discount = sum((charge.amount for charge in charges if charge.is_discount), ZERO)
    # 运费只按**分类码**认，不按说明文字里含"运费"去猜（口径明确要求：
    # 一句写错的说明不该改变金额归属）。
    logistics = sum(
        (charge.amount for charge in charges if is_logistics_charge(charge)), ZERO
    )
    other_charge = sum(
        (
            charge.amount
            for charge in charges
            if not charge.is_discount and not is_logistics_charge(charge)
        ),
        ZERO,
    )
    charge_amount = logistics + other_charge
    version.subtotal_amount = subtotal.quantize(Decimal("0.01"))
    version.charge_amount = charge_amount.quantize(Decimal("0.01"))
    version.logistics_amount = logistics.quantize(Decimal("0.01"))
    version.other_charge_amount = other_charge.quantize(Decimal("0.01"))
    version.discount_amount = discount.quantize(Decimal("0.01"))
    # 统一定点舍入（方案 §5）：与订单侧 amount 口径一致，避免出现 3 位小数的总额
    version.total_amount = (subtotal + charge_amount + discount).quantize(Decimal("0.01"))


def quote_freight_unconfirmed_reason(charges: list[QuoteCharge]) -> str | None:
    """正式发送前，运费是否"还没有被确认"（返回人话原因；None = 已确认，可以发）。

    口径（2026-10-09）：**草稿允许尚未填写运费，正式发送时必须已经确认具体金额**。
    所以判据不能是"运费金额是不是 0" —— 明确确认的零运费是合法的，
    而空输入不能被当成已确认的零元。

    判据分两种情况，都只看"有没有被确认过"：

    - **这一版还没有任何物流费用条目**：说明业务员压根没填 → 拦住，让他去填；
      确实零运费就新增一条 0 元并确认，走下面那一种。
    - **有物流费用条目**：每一条都必须确认过（`logistics_confirmed_at` 非空）。
      只要有一条没确认，就拦住 —— 有条目不等于金额已经确认。
    """
    logistics = [c for c in charges if is_logistics_charge(c)]
    if not logistics:
        return "尚未填写运费：请在「附加费用」里以「物流」类型填写已确认的实际运费金额"
    unconfirmed = [c for c in logistics if c.logistics_confirmed_at is None]
    if unconfirmed:
        names = "、".join(
            (c.description or f"物流费用#{c.id}") for c in unconfirmed
        )
        return (
            f"以下运费尚未确认具体金额：{names}。"
            "请确认后再正式发送；确属零运费也要显式确认（不能把空输入当作已确认的 0 元）"
        )
    return None


async def moq_warning(session: AsyncSession, sku_id: int | None, quantity: Decimal) -> str | None:
    """MOQ 提示（方案 §4.1）：数量低于起订量给提示不拦截——拦截与否由业务拍板。"""
    if sku_id is None:  # 定制项没有 SKU，也就没有起订量可谈
        return None
    sku = await session.get(Sku, sku_id)
    if sku is not None and sku.moq and quantity < sku.moq:
        return f"SKU {sku.sku_code}：数量 {quantity} 低于起订量 {sku.moq}，请与生产确认能否接单"
    return None


async def master_confirmation_problems(
    session: AsyncSession, *, version_id: int
) -> list[str]:
    """这版明细里"主数据还没达标"的清单（人话；空 = 全部合格）。

    §8.14 复审（第三轮）修的第二个待办：原判据只看 `master_version_no is None`，
    而**只确认了名称**也会拿到版本号 —— 于是"缺单位"的报价照样发出去（实测进到
    `sent`）。改成回查**明细自己记的那一版快照**，检查印给客户的三个字段
    （名称/规格/单位）在不在里面；判据落在 `master.quoted_snapshot_problems` 一份上。

    §8.14 复审（第四轮）补的第三个待办：只查"字段在不在那一版"还不够 ——
    **明细实际印出去的值还必须与那一版一致**。反例是"确认为空规格 → 本地后填新
    规格 → 生成明细被 `or` 回退成新规格"，字段键在快照里、值却被换过了。所以这里
    把明细的三个快照值一并读出来交给同一份判据做比对（见 `quoted_snapshot_problems`
    的 `actual` 参数）。

    **一份判据两处用**：正式发送（`ensure_items_master_confirmed` 据此抛错）与
    业务文件生成（`bizdoc` 据此落成草稿），避免两边各写一套、迟早漂移。
    """
    rows = (
        await session.execute(
            select(
                QuoteItem.sku_id,
                QuoteItem.master_version_no,
                QuoteItem.sku_code_snapshot,
                QuoteItem.sku_name_snapshot,
                QuoteItem.spec_snapshot,
                QuoteItem.unit_snapshot,
            )
            .where(QuoteItem.quote_version_id == version_id, QuoteItem.sku_id.is_not(None))
        )
    ).all()
    problems: list[str] = []
    for sku_id, version_no, code, name, spec, unit in rows:
        missing = await master_service.quoted_snapshot_problems(
            session,
            sku_id=int(sku_id),
            version_no=version_no,
            actual={"name": name, "specification": spec, "unit": unit},
        )
        if not missing:
            continue
        # 纯字段名 = 那一版压根没确认过这些字段（要先去确认）；带括号 = 确认过、
        # 但明细印的值与那一版对不上（要重新生成这版明细）。两种整改动作不同，
        # 所以分开措辞，别混成一句。
        blank = [m for m in missing if "（" not in m]
        drifted = [m for m in missing if "（" in m]
        parts = ([f"缺 {'、'.join(blank)}"] if blank else []) + drifted
        problems.append(f"{code or f'SKU#{sku_id}'}：" + "；".join(parts))
    return problems


async def ensure_items_master_confirmed(session: AsyncSession, *, version_id: int) -> None:
    """正式发送前的**硬校验**：印给客户的三个字段必须来自已确认的主数据（§8.14）。

    口径（2026-10-07 确认）：**草稿随便建、正式发送时必须过**。
    生成明细那一步只提示不阻断 —— 字段权威表整表为空时硬拦会把所有报价堵死；
    但对外那一步不行：印在客户文件上的名称/规格/单位必须是确认过的值。

    判据落在 `master_confirmation_problems` 一份上（与业务文件生成共用）。
    定制项（没有 SKU）不适用：它们是独立业务身份，不要求提前建 SKU。
    """
    problems = await master_confirmation_problems(session, version_id=version_id)
    if not problems:
        return
    raise AppError(
        ErrorCode.STATUS_NOT_ALLOWED,
        "以下明细的主数据尚未确认，不能正式发送："
        + "；".join(problems)
        + "。请先在这些 SKU 上确认主数据，再重新生成这版明细。",
        422,
    )


async def ensure_freight_confirmed(session: AsyncSession, *, version_id: int) -> None:
    """正式发送前的**硬校验**：运费必须已经确认具体金额（2026-10-09 口径）。

    口径：**草稿允许尚未填写运费，正式发送时必须已经确认具体金额**。

    为什么不能只看金额：`0` 同时代表"没填"和"明确是零运费"。空输入被默认当成
    已确认的零元，客户文件上就会印出"运费 0.00"并声称"代收代付已确认" ——
    实际运费一毛钱都还没确认，这是静默的错。所以判据是
    `QuoteCharge.logistics_confirmed_at`（确认时刻），不是金额。

    判据落在 `quote_freight_unconfirmed_reason` 一份上，与前端提示同源。
    """
    charges = await version_charges(session, version_id)
    reason = quote_freight_unconfirmed_reason(charges)
    if reason is None:
        return
    raise AppError(ErrorCode.STATUS_NOT_ALLOWED, f"{reason}。", 422)


async def version_charges(session: AsyncSession, version_id: int) -> list[QuoteCharge]:
    """这一版的所有附加费用（含折扣行），按排序号与 id 稳定排序。"""
    return list(
        (
            await session.execute(
                select(QuoteCharge)
                .where(QuoteCharge.quote_version_id == version_id)
                .order_by(QuoteCharge.sort_no.asc(), QuoteCharge.id.asc())
            )
        ).scalars().all()
    )


async def copy_version_charges(
    session: AsyncSession, *, source_id: int, target_version: QuoteVersion
) -> int:
    """把源版本的费用行整份抄到目标版本，返回复制条数。

    **两个复制入口共用这一份**（`create_version` 与 `POST /quotes/{id}/clone`）。
    以前只有前者抄了费用，后者只抄明细 —— 于是"复制报价"出来的新单
    货款对、**运费与折扣全丢**，而且因为缺运费，正式发送还会被
    `ensure_freight_confirmed` 拦下，用户看到的是"这单发不出去"
    （`/tmp/verify_freight_rules.py` 的场景6 就是这么发现的）。

    运费的**确认时刻一并带过来**：源版本的运费是业务按承运商确认过的实际金额
    （口径：客户全额承担、公司原额代收代付），这个事实对同一笔生意的新版本
    仍然成立。金额对金额、口径对口径地照抄，不重新猜。
    要改金额就改，`mark_logistics_confirmed` 会重新打确认时刻。

    ⚠️ 折扣原样带过来（库里是负数），与 `recalc_version` 的代数相加约定一致；
    这里**不重新归一符号**，否则一个已经是负数的折扣会被翻成正数、把总额加上去。
    """
    copied = 0
    for charge in await version_charges(session, source_id):
        session.add(
            QuoteCharge(
                quote_version_id=target_version.id,
                charge_type=charge.charge_type,
                description=charge.description,
                amount=charge.amount,
                currency=charge.currency,
                is_discount=charge.is_discount,
                sort_no=charge.sort_no,
                logistics_confirmed_at=charge.logistics_confirmed_at,
            )
        )
        copied += 1
    return copied


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
            # ⚠️ **传 None**：让 `build_item_snapshot` 走 `_pick_confirmed` 取
            # **当前已确认的规格**。从前传的是 `item.spec_snapshot`，于是刷新把
            # `master_version_no` 钉到新版本、**规格快照却还是旧值** ——
            # 明细声称引用 v4、显示的还是 v3 的规格，发送校验照样拦（实测：
            # 「规格（明细用的是「999×888」，与所引用确认版本的「555×444」不一致）」）。
            # 传 None 而不是空串：函数里的判据是 `spec_snapshot if spec_snapshot else ...`，
            # 空串会落到同一分支，但 None 才是"没有外部指定"的准确表达。
            # 刷新这个动作的语义就是"按**当前已确认主数据**重建这一行"，
            # 而且执行前会先给人看变化预览（`master-refresh-preview`），
            # 所以不存在"悄悄改掉用户手填值"的问题。
            spec_snapshot=None,
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
            "unit_snapshot",
            # 对客三字段：刷新按已确认主数据重建（issue 建议第 5 条）
            "sku_name_snapshot", "spec_snapshot",
            # §8.14：刷新会重新经过主数据解析，版本号也要跟着刷新后的口径走
            "master_version_no",
        ):
            setattr(item, field, getattr(rebuilt, field))
        refreshed += 1
    await session.flush()
    await recalc_version(session, version)
    return {"refreshed": refreshed, "skipped": skipped}


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
    # 产品核价是否含运费，由**这一版的口径**决定（见 `item_unit_product_cost`）。
    # 新报价：运费代收代付，不进产品成本与利润；历史版本：沿用含运费的老口径。
    cost_includes_freight = version_logistics_in_base_cost(version)

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
            item_cost = item_unit_product_cost(item, includes_freight=cost_includes_freight)
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
            # 总额非正已在上面硬拒；这里保留守卫只作防御（除零），
            # 不再是"绕过校验"的入口。
            if revenue_cny is not None and revenue_cny > 0:
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
        base_cost = item_unit_product_cost(item, includes_freight=cost_includes_freight)
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

    # ---- 整单金额必须为正（2026-10-10 修 issue #3）----
    #
    # ⚠️ 从前这里是 `if revenue and revenue > 0:` —— **总额为 0 或负数时整段跳过**，
    # 于是整单优惠把应付打到 -66.29 元也照样 `approval_required=False` 直接批准
    # （实测：明细 28.71、成本 20，优惠 95/100/150 → 总额 -66.29/-71.29/-121.29，
    # 三次全部 `code=0 未超出权限，报价已通过`）。
    # 业务上不存在"客户倒收钱"的报价，所以它跟"低于绝对底价"是同一类：
    # **任何审批都不该放过**，因此在这里硬拒（422），不生成可批的审批单。
    # 与下面硬底价那段同一个范式，出口文案也照它的写法给。
    _total_for_check = (
        (version.total_amount * fx) if foreign else version.total_amount
    ) or ZERO
    if _total_for_check <= 0:
        raise AppError(
            ErrorCode.PRICE_BELOW_HARD_FLOOR,
            f"报价整单金额为 ¥{_total_for_check:.2f}，为零或负数，任何审批都无法通过，"
            "已拒绝提交。请检查明细金额与整单优惠是否把总额打到了 0 以下；"
            "合法出口：免费样品 / 清库存等特殊业务请走各自通道（另立零元单据），"
            "不要在正常报价上用优惠把总额抵消掉。",
            422,
        )

    # A10（方案 §7.1）：整单有效金额判定。
    # 逐项全过 ≠ 整体能过：整单优惠（is_discount 附加费）摊下来后，
    # 整单利润率/加权均价可能已跌破授权——不能逐项检查后就直接放行。
    #
    # 运费分离口径（2026-10-09）：**收入与成本两边必须同时排除代收代付的运费**。
    #   - 收入侧：应收总额里含运费，但那笔钱是替客户转交给承运商的，不是公司的收入；
    #   - 成本侧：运费也不进产品成本。
    # 只排除一边会算出一个假的利润率 —— 只排收入 → 利润率被压得极低（误触审批）；
    # 只排成本 → 利润率被抬得极高（该拦的拦不住）。两边都不排 → 靠运费收入
    # 抵消产品低价，正是口径明确禁止的。
    #
    # ⚠️ 老口径（legacy）版本**一个字不改**：那时运费本来就含在产品价里，
    # 收入与成本的重心与今天不同，改了就成了改写历史结论。
    revenue = version.total_amount
    if revenue and revenue > 0:
        # 存在无成本明细时，整单利润率等于"拿 0 成本算出来的"，不可信——
        # 利润率维度跳过（D5），加权保护价是绝对口径仍生效
        any_missing_cost = any(
            item_unit_product_cost(item, includes_freight=cost_includes_freight) <= 0
            for item in items
        )
        revenue_cny = (revenue * fx) if foreign else revenue
        cost_total = sum(
            (
                item_unit_product_cost(item, includes_freight=cost_includes_freight)
                * item.quantity
                for item in items
            ),
            ZERO,
        )
        # 非运费口径：收入与成本一起把运费剔除（用**本版拆分列**，不重新猜分类）
        if not cost_includes_freight:
            logistics_cny = (
                (version.logistics_amount * fx) if foreign else version.logistics_amount
            ) or ZERO
            revenue_cny = revenue_cny - logistics_cny
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
            "node_code": record.node_code,
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
        # 单件产品核价成本：口径**逐版**取（新旧版本可以并存，见 `item_unit_product_cost`）。
        # 新口径只算商品成本（运费代收代付，不进产品利润）；老版本仍含单件运费，
        # 这样汇总毛利才与明细的 `profit_snapshot` 对得上 —— 两边的口径必须同源。
        v_includes_freight = version_logistics_in_base_cost(version)
        cost_total = sum(
            (
                item_unit_product_cost(item, includes_freight=v_includes_freight)
                * item.quantity
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

    条件：有正式发送/客户接受事实 + 已过有效期 + 没有非取消订单引用它。
    "同一报价只提醒一次"按标题查*任何状态*的任务（完成/忽略过就不再建），
    否则销售每天都会收到同一条到期提醒。由每日自动任务调用（不新增调度项）。
    """
    from app.modules.order.model import SalesOrder
    from app.modules.task.model import Task
    from app.modules.task.scanning import lock_task_scan
    from app.modules.customer.service import refresh_next_followup_at

    await lock_task_scan(session)
    # 过期扫描的"今天"也按业务日期（§9.10 复审）：UTC 日期在北京时间凌晨会差一天，
    # 提醒、展示、发送拦截三处必须同一个"今天"。
    today = today_business()
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
                Quote.status.in_(("sent", "accepted", "expired")),
                or_(Quote.status.in_(("sent", "accepted")),
                    select(QuoteVersion.id).where(QuoteVersion.quote_id == Quote.id,
                                                  QuoteVersion.sent_at.is_not(None)).exists()),
                Customer.deleted_at.is_(None),
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
            await session.execute(select(Task.id).where(Task.title == title).limit(1))
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
    for customer_id in sorted({quote.customer_id for quote, _ in rows}):
        await refresh_next_followup_at(session, customer_id)
    return created


async def link_master_only(
    session: AsyncSession, *, version: QuoteVersion
) -> dict:
    """只把明细**接到最新已确认主数据**上，**不碰价格**（方案二：一键修复）。

    为什么必须有这条独立的路（2026-10-10 实测）：

    黄条说的是"这条明细没有可引用的已确认主数据版本"，用户去产品中心确认完
    回来点「刷新主数据」，**黄条还在** —— 因为刷新走的是 `refresh_prices`，
    而它在下面两种情况直接跳过整条明细：

        `item.price_source is None`        （手工定价的明细）
        `lookup["status"] != "ok"`         （没有价格规则、规则被停用、查不到价）

    于是**"接主数据"被"查不到价"挡住了**：明明确认了，却接不上。
    这两件事本来就不该绑在一起 —— 主数据版本号回答的是"这一行对着哪一版
    名称/规格/单位"，与"这一行卖多少钱"无关。

    所以这里只做一件事：按**当前已确认主数据**重建对客三字段快照 + 钉上版本号，
    价格、成本、利润快照**一个字不动**（不重算、不覆盖手工价）。

    没有可引用的已确认版本时**明确报缺哪些字段**，而不是静默跳过 ——
    静默跳过正是"点了没反应、黄条还在"的来源。
    """
    from app.modules.product import master as master_service

    items = await version_items(session, version.id)
    linked = 0
    already = 0
    missing: list[dict] = []
    for item in items:
        # 定制项没有 SKU 主数据（名称是人工填的），不属于这条路
        if item.sku_id is None:
            continue
        # ⚠️ **必须显式传对客三字段**：`resolve_confirmed_master` 的默认是
        # `MASTER_FIELDS`（全部 13 个）。不传就会把"颜色/材质/长宽高/起订量…"
        # 也算成"缺确认"，报出"还缺 10 个字段"—— 而报价根本不用它们。
        # 实测踩到：接主数据返回 linked=0、missing 列了 10 个无关字段。
        master = await master_service.resolve_confirmed_master(
            session, item.sku_id, list(master_service.QUOTE_DISPLAY_FIELDS)
        )
        values = master.get("values") or {}
        unconfirmed = master.get("unconfirmed") or []
        if unconfirmed:
            missing.append(
                {
                    "item_id": item.id,
                    "sku_code": item.sku_code_snapshot or f"SKU#{item.sku_id}",
                    "missing_labels": master.get("unconfirmed_labels") or [],
                }
            )
            continue
        version_no = master.get("version_no")
        if version_no is None:
            missing.append(
                {
                    "item_id": item.id,
                    "sku_code": item.sku_code_snapshot or f"SKU#{item.sku_id}",
                    "missing_labels": ["（没有可引用的已确认主数据版本）"],
                }
            )
            continue
        if item.master_version_no == version_no and not _display_fields_drift(item, values):
            already += 1
            continue
        # 只动这三列 + 版本号；价格/成本/利润一律不碰
        for field in ("name", "specification", "unit"):
            value = values.get(field)
            if field == "name":
                item.sku_name_snapshot = None if value is None else str(value)
            elif field == "specification":
                item.spec_snapshot = None if value is None else str(value)
            else:
                item.unit_snapshot = None if value is None else str(value)
        item.master_version_no = version_no
        linked += 1
    await session.flush()
    return {
        "linked": linked,
        "already_linked": already,
        "missing": missing,
        "missing_count": len(missing),
        "message": (
            f"已把 {linked} 条明细接到最新已确认主数据"
            + (f"（{already} 条本来就已接上）" if already else "")
            + (f"；{len(missing)} 条还缺确认，请先去产品中心确认" if missing else "")
            + "。价格与成本快照未改动。"
        ),
    }


def _display_fields_drift(item: QuoteItem, values: dict) -> bool:
    """明细的对客三字段快照是否与目标已确认值不一致。"""
    pairs = (
        (item.sku_name_snapshot, values.get("name")),
        (item.spec_snapshot, values.get("specification")),
        (item.unit_snapshot, values.get("unit")),
    )
    return any(
        ("" if cur is None else str(cur)) != ("" if want is None else str(want))
        for cur, want in pairs
    )
