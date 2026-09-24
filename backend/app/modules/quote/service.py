"""报价业务逻辑：编号、明细快照、金额汇总、审批判定。"""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.modules.approval.model import ApprovalDefinition, ApprovalInstance, ApprovalRecord
from app.modules.customer.model import Customer
from app.modules.opportunity.model import OpportunityItem
from app.modules.pricing import service as pricing_service
from app.modules.product.model import Product, Sku
from app.modules.quote.model import (
    QUOTE_STATUS_LABEL,
    Quote,
    QuoteCharge,
    QuoteItem,
    QuoteVersion,
)
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
    """生成一条报价明细：成本、标准价、最低价、利润全部落成快照。"""
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
    )
    price = Decimal(str(quoted_price)) if quoted_price is not None else Decimal(str(result["recommended_price"]))
    base_cost = Decimal(str(result["cost"]["base_cost"]))
    profit = price - base_cost
    profit_rate = (profit / price) if price else ZERO

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
    offending: list[dict] = []
    for item in items:
        base_cost = item.cost_snapshot + item.logistics_cost_snapshot
        price = item.quoted_price
        profit = price - base_cost
        profit_rate = (profit / price) if price else ZERO
        floor = max(item.minimum_price_snapshot or ZERO, base_cost)
        if price < floor - Decimal("0.0001") or profit_rate < min_margin - Decimal("0.000001"):
            item.approval_required = True
            item.approval_reason = (
                f"报价 ¥{price:.2f} 低于最低允许价 ¥{floor:.2f}，利润率 {profit_rate * 100:.2f}%"
                f"（授权 {min_margin * 100:.0f}%）"
            )
            offending.append(
                {
                    "sku_code": item.sku_code_snapshot,
                    "quoted_price": float(price),
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
