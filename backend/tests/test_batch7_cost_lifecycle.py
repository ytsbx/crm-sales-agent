"""第七批返修：真实核价、复制、重建和提交审批入口。"""
from datetime import UTC, date, datetime
from decimal import Decimal as D
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.orm import Session
from starlette.requests import Request

from conftest import make_engine, make_user, SyncSessionAsAsync
from app.core.errors import AppError
from app.core.audit import AuditLog
from app.modules.quote import service as svc, router
from app.modules.quote.model import Quote, QuoteVersion, QuoteItem, QuoteCharge
from app.modules.quote.schema import QuoteClone, QuoteItemUpdate
from app.modules.pricing.model import ProductCost, PriceRule, CustomerPriceRule, PricePermission
from app.modules.product.model import Product, Sku, SkuFieldAuthority, SkuMasterVersion
from app.modules.settings.model import SystemSetting, NumberingRule, NumberSequence
from app.modules.approval import rules_engine
from app.modules.approval.model import ApprovalDefinition, ApprovalInstance, ApprovalRecord, ApprovalRule
from app.modules.customer.model import Customer
from app.modules.opportunity.model import Opportunity
from app.modules.user.model import Role

NOW = datetime.now(UTC)
REQUEST = Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1)})
USER = make_user(1, permissions=("quote:manage", "quote:view", "price:manage"), data_scope="all")

@pytest.fixture
def case():
    engine = make_engine()
    for model in (AuditLog, Product, Sku, SkuFieldAuthority, SkuMasterVersion,
                  ProductCost, PriceRule, CustomerPriceRule, PricePermission, SystemSetting,
                  NumberingRule, NumberSequence, Role, QuoteItem, QuoteCharge, ApprovalDefinition, ApprovalInstance, ApprovalRecord, ApprovalRule):
        model.__table__.create(engine, checkfirst=True)
    with Session(engine, expire_on_commit=False) as raw:
        raw.add(Customer(id=1, name="成本回归客户", owner_id=1))
        raw.add(Product(id=1, name="回归产品"))
        raw.add(Sku(id=1, product_id=1, sku_code="COST", name="回归SKU"))
        raw.add(Opportunity(id=1, title="回归商机", stage_id=1, customer_id=1, owner_id=1))
        quote = Quote(id=1, quote_no="COST-Q", customer_id=1, owner_id=1,
                      opportunity_id=1, current_version_id=1, status="draft")
        version = QuoteVersion(id=1, quote_id=1, version_no=1, created_at=NOW,
                               currency="CNY", approval_status="not_submitted")
        raw.add_all([quote, version]); raw.flush()
        raw.add(QuoteCharge(quote_version_id=1, charge_type="logistics", description="零运费",
                            amount=D(0), logistics_confirmed_at=NOW))
        raw.commit()
        yield raw, SyncSessionAsAsync(raw), quote, version
    engine.dispose()

async def build(raw, session, version, *, cost="partial", amount="10"):
    if cost != "absent":
        raw.add(ProductCost(sku_id=1, effective_from=date(2020, 1, 1), purchase_cost=D(amount),
                            production_cost=D(0) if cost == "complete" else None,
                            package_cost=D(0) if cost == "complete" else None,
                            processing_cost=D(0) if cost == "complete" else None))
        raw.flush()
    item = await svc.build_item_snapshot(session, version=version, sku_id=1, quantity=D(1),
        customer_id=1, quoted_price=D(100), logistics_cost=D(0), opportunity_item_id=None,
        spec_snapshot=None, remark=None, role_codes=USER.roles)
    raw.add(item); raw.flush()
    await svc.recalc_version(session, version)
    return item

async def submit(session, quote, version):
    return await svc.submit_for_approval(session, quote=quote, version=version,
                                        applicant_id=1, user_roles=USER.roles)

@pytest.mark.anyio
@pytest.mark.parametrize("cost,amount,required", [
    ("partial", "0", True), ("partial", "10", True),
    ("complete", "0", False), ("complete", "10", False), ("absent", "0", False)])
async def test_cost_states_submit(case, cost, amount, required):
    raw, session, quote, version = case
    item = await build(raw, session, version, cost=cost, amount=amount)
    assert item.cost_present_snapshot is (cost != "absent")
    assert item.approval_required is required
    if cost != "complete":
        with pytest.raises(AppError, match="成本"):
            await submit(session, quote, version)
        assert version.approval_status == "not_submitted"
        return
    instance, needs = await submit(session, quote, version)
    assert needs is required
    assert version.approval_status == ("pending" if required else "approved")

@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["version", "clone"])
@pytest.mark.parametrize("flag", [False, True, None])
async def test_copy_retains_snapshot_state(case, operation, flag):
    raw, session, quote, version = case
    item = await build(raw, session, version)
    item.cost_complete_snapshot = flag
    item.cost_present_snapshot = None if flag is None else True
    raw.commit()
    if operation == "version":
        target = await svc.create_version(session, quote=quote, user=USER)
        target_quote = quote
    else:
        await router.clone_quote(quote.id, QuoteClone(), REQUEST, USER, session)
        target_quote = raw.query(Quote).filter(Quote.id != quote.id).one()
        target = raw.get(QuoteVersion, target_quote.current_version_id)
    copied = raw.query(QuoteItem).filter_by(quote_version_id=target.id).one()
    assert copied.cost_complete_snapshot is flag
    assert copied.cost_present_snapshot is (None if flag is None else True)
    if flag is not True:
        with pytest.raises(AppError, match="成本"):
            await submit(session, target_quote, target)
        assert target.approval_status == "not_submitted"
    else:
        _, needs = await submit(session, target_quote, target)
        assert not needs and target.approval_status == "approved"

@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["edit", "refresh"])
async def test_rebuild_replaces_complete_cost_with_partial(case, operation):
    raw, session, quote, version = case
    item = await build(raw, session, version, cost="complete")
    record = raw.query(ProductCost).one()
    record.production_cost = record.package_cost = record.processing_cost = None
    record.purchase_cost = D(0)
    raw.add(PriceRule(sku_id=1, effective_from=date(2020, 1, 1), standard_price=D(101), guide_price=D(101)))
    raw.commit()
    if operation == "edit":
        await router.update_item(item.id, QuoteItemUpdate(quoted_price=D(101)), REQUEST, USER, session)
    else:
        item.price_source = "general"
        result = await svc.refresh_prices(session, version=version, user=USER)
        assert result["refreshed"] == 1
    assert item.cost_complete_snapshot is False and item.cost_present_snapshot is True
    with pytest.raises(AppError, match="成本"):
        await submit(session, quote, version)
    assert version.approval_status == "not_submitted"

@pytest.mark.anyio
@pytest.mark.parametrize("cost", ["partial", "complete"])
async def test_unrelated_auto_pass_cannot_override_incomplete_cost(case, monkeypatch, cost):
    raw, session, quote, version = case
    await build(raw, session, version, amount="0", cost=cost)
    if cost == "complete":
        # 正向对照：完整成本、低于保护价时仍允许已有免审规则工作。
        raw.query(QuoteItem).one().minimum_price_snapshot = D(120)
        raw.flush()
    from types import SimpleNamespace
    decision = SimpleNamespace(kind="auto_pass", action={}, rule_name="金额免审", trace=lambda: {})
    monkeypatch.setattr(rules_engine, "route", AsyncMock(return_value=decision))
    if cost == "partial":
        with pytest.raises(AppError, match="成本"):
            await submit(session, quote, version)
        rules_engine.route.assert_not_awaited()
        assert version.approval_status == "not_submitted"
        return
    instance, needs = await submit(session, quote, version)
    assert needs
    assert version.approval_status == ("pending" if cost == "partial" else "approved")
    assert bool(instance.summary.get("auto_passed")) is (cost == "complete")


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["version", "clone"])
async def test_copy_absent_cost_remains_absent(case, operation):
    raw, session, quote, version = case
    await build(raw, session, version, cost="absent")
    raw.commit()
    if operation == "version":
        target = await svc.create_version(session, quote=quote, user=USER)
        target_quote = quote
    else:
        await router.clone_quote(quote.id, QuoteClone(), REQUEST, USER, session)
        target_quote = raw.query(Quote).filter(Quote.id != quote.id).one()
        target = raw.get(QuoteVersion, target_quote.current_version_id)
    item = raw.query(QuoteItem).filter_by(quote_version_id=target.id).one()
    assert item.cost_present_snapshot is False
    with pytest.raises(AppError, match="成本"):
        await submit(session, target_quote, target)
    assert target.approval_status == "not_submitted"

@pytest.mark.anyio
async def test_incomplete_cost_cannot_enter_manual_approval(case):
    raw, session, quote, version = case
    await build(raw, session, version, amount="0")
    with pytest.raises(AppError, match="成本"):
        await submit(session, quote, version)
    assert version.approval_status == "not_submitted" and quote.status == "draft"
    assert raw.query(ApprovalInstance).count() == 0
    assert raw.query(ApprovalRecord).count() == 0


@pytest.mark.anyio
@pytest.mark.parametrize("complete_flag,required", [(False, True), (None, False)])
async def test_pre_presence_zero_snapshot_does_not_hide_known_incompleteness(case, complete_flag, required):
    raw, session, quote, version = case
    item = await build(raw, session, version, amount="0")
    item.cost_present_snapshot = None
    item.cost_complete_snapshot = complete_flag
    raw.commit()
    with pytest.raises(AppError, match="成本"):
        await submit(session, quote, version)
    assert version.approval_status == "not_submitted"
