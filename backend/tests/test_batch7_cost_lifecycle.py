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
from app.modules.quote.schema import QuoteClone, QuoteItemUpdate, QuoteUpdate
from app.modules.pricing.model import ProductCost, PriceRule, CustomerPriceRule, PricePermission
from app.modules.product.model import Product, Sku, SkuFieldAuthority, SkuMasterVersion
from app.modules.settings.model import SystemSetting, NumberingRule, NumberSequence
from app.modules.approval import rules_engine
from app.modules.approval.model import ApprovalDefinition, ApprovalInstance, ApprovalRecord, ApprovalRule
from app.modules.customer.model import Customer
from app.modules.opportunity.model import Opportunity, OpportunityItem
from app.modules.order import service as order_service
from app.modules.order.model import SalesOrder, SalesOrderItem, OrderStatusHistory
from app.modules.payment.model import ReceivablePlan
from app.modules.user.model import Permission, Role, User, user_roles, role_permissions

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
async def test_quote_allows_one_active_order_across_versions(case, monkeypatch):
    raw, session, quote, version = case
    for model in (OpportunityItem, SalesOrderItem, OrderStatusHistory):
        model.__table__.create(raw.get_bind(), checkfirst=True)
    monkeypatch.setattr(order_service, "generate_order_no", AsyncMock(side_effect=["SO-FIRST", "SO-NEW"]))
    monkeypatch.setattr("app.modules.followup.service.record_and_notify", AsyncMock())
    await build(raw, session, version, cost="complete")
    quote.status = "accepted"
    version.approval_status = "approved"
    version.sent_at = version.accepted_at = NOW
    raw.commit()

    actor = make_user(1, permissions=("order:view", "order:manage"), data_scope="all")
    first = await order_service.create_order_from_quote(session, version=version, user_id=1, user=actor)
    raw.commit()
    detail = (await router.get_version(version.id, USER, session))["data"]
    assert detail["has_active_order"] is True and detail["active_order"] is None
    detail = (await router.get_version(version.id, actor, session))["data"]
    assert detail["active_order"] == {"id": first.id, "order_no": "SO-FIRST"}
    with pytest.raises(AppError, match="已生成有效订单"):
        await svc.create_version(session, quote=quote, user=USER)

    # A previously created version may still exist when the guard is rolled out.
    previous = QuoteVersion(quote_id=quote.id, version_no=2, currency="CNY", approval_status="approved",
                            created_at=NOW, sent_at=NOW, accepted_at=NOW)
    raw.add(previous)
    raw.flush()
    quote.current_version_id = previous.id
    with pytest.raises(AppError, match="SO-FIRST"):
        await order_service.create_order_from_quote(session, version=previous, user_id=1, user=actor)
    with pytest.raises(AppError, match="SO-FIRST"):
        await order_service.create_order(session, user_id=1, user=actor, customer_id=1, items=[], quote_id=quote.id)
    assert raw.query(SalesOrder).count() == raw.query(ReceivablePlan).count() == 1

    # Simulate the committed result of the existing cancellation workflow.
    first.status = "cancelled"
    raw.query(ReceivablePlan).one().status = "cancelled"
    quote.current_version_id = version.id
    raw.flush()
    detail = (await router.get_version(version.id, actor, session))["data"]
    assert detail["has_active_order"] is False and detail["version_order_cancelled"] is True
    with pytest.raises(AppError, match="请新建报价版本"):
        await order_service.create_order_from_quote(session, version=version, user_id=1, user=actor)
    with pytest.raises(AppError, match="请在报价中新建版本"):
        await order_service.create_order(session, user_id=1, user=actor, customer_id=1, items=[], quote_id=quote.id)

    replacement = await svc.create_version(session, quote=quote, user=USER, source_version_id=version.id)
    assert quote.status == "draft"
    with pytest.raises(AppError, match="未通过审批"):
        await order_service.create_order_from_quote(session, version=replacement, user_id=1, user=actor)
    replacement.approval_status = "approved"
    replacement.sent_at = NOW
    with pytest.raises(AppError, match="客户已接受"):
        await order_service.create_order_from_quote(session, version=replacement, user_id=1, user=actor)
    replacement.accepted_at = NOW
    quote.status = "accepted"
    second = await order_service.create_order_from_quote(session, version=replacement, user_id=1, user=actor)
    assert second.order_no == "SO-NEW"
    assert (await order_service.active_order_for_quote(session, quote.id)).id == second.id
    assert raw.query(ReceivablePlan).filter_by(status="pending").count() == 1


@pytest.mark.anyio
@pytest.mark.parametrize("amount,expected", [("99999999999999.98", "manager"), ("99999999999999.99", "director")])
async def test_approval_tier_compares_exact_cents(case, amount, expected):
    raw, session, quote, version = case
    item = await build(raw, session, version, cost="complete")
    item.minimum_price_snapshot = D(120)
    version.total_amount = D(amount)
    raw.add(SystemSetting(key="approval_levels", value={"levels": [
        {"node": "manager", "max_amount": "99999999999999.98"},
        {"node": "director", "max_amount": " "},
    ]}))
    raw.flush()
    instance, needs = await submit(session, quote, version)
    assert needs and instance.current_node == expected

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
@pytest.mark.parametrize("operation", ["clone", "update"])
@pytest.mark.parametrize("owner_id,status", [(0, 404), (999, 404), (2, 422), (3, 200), (4, 403), (5, 403)])
async def test_quote_assignment_checks_recipient_customer_access(case, operation, owner_id, status):
    raw, session, quote, _ = case
    for table in (Permission.__table__, user_roles, role_permissions):
        table.create(raw.get_bind(), checkfirst=True)
    raw.add_all([
        User(id=2, name="停用用户", username="inactive", password_hash="x", status="inactive"),
        User(id=3, name="在职用户", username="active", password_hash="x", status="active"),
        User(id=4, name="范围外用户", username="outside", password_hash="x"),
        User(id=5, name="无客户查看权", username="no_view", password_hash="x"),
        Role(id=1, code="viewer_all", name="全公司客户查看", data_scope="all"),
        Role(id=2, code="viewer_self", name="本人客户查看", data_scope="self"),
        Role(id=3, code="other_role", name="其他角色", data_scope="all"),
        Permission(id=1, code="customer:view", name="查看客户"),
    ])
    raw.flush()
    raw.execute(user_roles.insert(), [{"user_id": i + 2, "role_id": i} for i in (1, 2, 3)])
    raw.execute(role_permissions.insert(), [{"role_id": i, "permission_id": 1} for i in (1, 2)])
    raw.commit()

    async def save():
        if operation == "clone":
            await router.clone_quote(quote.id, QuoteClone(owner_id=owner_id, copy_items=False), REQUEST, USER, session)
        else:
            await router.update_quote(quote.id, QuoteUpdate(owner_id=owner_id), REQUEST, USER, session)

    if status != 200:
        with pytest.raises(AppError) as error:
            await save()
        assert error.value.http_status == status
        assert raw.query(Quote).count() == 1
        assert quote.owner_id == 1
        return

    await save()
    saved = raw.query(Quote).filter(Quote.id != quote.id).one() if operation == "clone" else quote
    assert saved.owner_id == owner_id


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
