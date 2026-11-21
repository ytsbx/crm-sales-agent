"""PostgreSQL regressions for pricing, transaction failures and formal documents."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from importlib import import_module
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from _test_support import require_api_base, require_isolated_db

require_api_base()
require_isolated_db()

import_module("app.main")  # registers every model
from app.core.database import SessionLocal, engine  # noqa: E402
from app.core.deps import CurrentUser  # noqa: E402
from app.core.errors import AppError, ErrorCode  # noqa: E402
from app.core.timebase import today_business  # noqa: E402
from app.modules.agent.model import AgentAction, AgentExecution, AgentSession  # noqa: E402
from app.modules.agent.runtime import execute_action  # noqa: E402
from app.modules.approval import rules_engine  # noqa: E402
from app.modules.approval.model import ApprovalRule, ApprovalRuleVersion  # noqa: E402
from app.modules.bizdoc.service import build_quote_doc  # noqa: E402
from app.modules.customer.model import Customer  # noqa: E402
from app.modules.erp import reconcile  # noqa: E402
from app.modules.integration.model import IntegrationDiff  # noqa: E402
from app.modules.opportunity.model import Opportunity, OpportunityItem, OpportunityStage  # noqa: E402
from app.modules.order import service as orders  # noqa: E402
from app.modules.order.model import SalesOrder, SalesOrderItem  # noqa: E402
from app.modules.payment.model import ReceivablePlan  # noqa: E402
from app.modules.pricing import service as pricing  # noqa: E402
from app.modules.pricing.model import PricePermission, PriceRule, ProductCost  # noqa: E402
from app.modules.product import master  # noqa: E402
from app.modules.product.model import Product, Sku  # noqa: E402
from app.modules.quote import service as quotes  # noqa: E402
from app.modules.quote.model import Quote, QuoteCharge, QuoteVersion  # noqa: E402
from app.modules.quote.router import download_pdf, generate_pdf_alias  # noqa: E402
from app.modules.task.model import Task  # noqa: E402
from app.modules.user.model import Role, User  # noqa: E402


@asynccontextmanager
async def sandbox():
    # Gateway commit is real at the session level, but the fixture's outer transaction
    # remains rollbackable. Failure persistence can be checked without leaving fixtures.
    async with engine.connect() as connection:
        outer = await connection.begin()
        try:
            async with AsyncSession(bind=connection, expire_on_commit=False,
                                    join_transaction_mode="create_savepoint") as session:
                yield session
        finally:
            if outer.is_active:
                await outer.rollback()


async def fixture(session, cost=D("10")):
    tag = "CHKRISK" + uuid4().hex[:12]
    user = (await session.execute(select(User).where(User.username == "admin"))).scalar_one()
    role = Role(code=tag, name=tag, data_scope="all")
    product = Product(name=tag)
    customer = Customer(name=tag, owner_id=user.id)
    session.add_all([role, product, customer])
    await session.flush()
    sku = Sku(product_id=product.id, sku_code=tag, name=tag, unit="件")
    session.add(sku)
    await session.flush()
    session.add_all([
        PricePermission(role_id=role.id, minimum_margin=D(".15"), discount_limit=D(".3"), can_approve=False),
        ProductCost(sku_id=sku.id, purchase_cost=cost, production_cost=D(0),
                    package_cost=D(0), processing_cost=D(0), effective_from=today_business()),
        PriceRule(sku_id=sku.id, standard_price=D(100), guide_price=D(100), minimum_price=D(0)),
    ])
    quote = Quote(quote_no=tag, customer_id=customer.id, owner_id=user.id,
                  valid_until=today_business() + timedelta(days=30))
    session.add(quote)
    await session.flush()
    version = QuoteVersion(quote_id=quote.id, currency="CNY", created_at=datetime.now(UTC),
                           customer_name_snapshot=customer.name, valid_until_snapshot=quote.valid_until)
    session.add(version)
    await session.flush()
    quote.current_version_id = version.id
    return user, role, sku, customer, quote, version


async def add_item(session, role, sku, customer, version, price):
    row = await quotes.build_item_snapshot(
        session, version=version, sku_id=sku.id, quantity=D(1), customer_id=customer.id,
        quoted_price=price, logistics_cost=D(0), opportunity_item_id=None,
        spec_snapshot=None, remark=None, role_codes=[role.code],
    )
    session.add(row)
    await session.flush()
    await quotes.recalc_version(session, version)
    return row


async def expect_error(code, action):
    try:
        await action()
    except AppError as exc:
        assert exc.code == code, (exc.code, exc.message)
        return
    raise AssertionError(f"Expected business error {code}")


async def _confirm_freight(session, version):
    """补一条"已确认的 0 元运费"。

    运费闸已前置到**提交审批**（2026-10-12）：没确认运费就提交会被 422 拦下。
    本套件测的是审批分档 / 硬底价那些判据，与运费无关 —— 先过掉这道闸，
    否则会拦在错误的门上（看着像"分档没生效"）。

    用 **0 元**而不是别的金额：0 元加进去不改变 `total_amount`，
    所以"分档金额 = 某个数"这类断言一个都不用动。
    """
    from app.modules.quote.model import QuoteCharge

    session.add(QuoteCharge(
        quote_version_id=version.id, charge_type="logistics", amount=0,
        is_discount=False, logistics_confirmed_at=datetime.now(UTC),
        description="套件夹具：明确 0 元运费",
    ))
    await session.flush()


async def submit(session, user, role, quote, version):
    # Explicitly test no published exemption; no global configuration is changed.
    await _confirm_freight(session, version)
    with patch.object(rules_engine, "route", AsyncMock(return_value=None)):
        return await quotes.submit_for_approval(
            session, quote=quote, version=version, applicant_id=user.id, user_roles=[role.code]
        )


async def discount_cases():
    for price, cost, currency, expected in [
        (D(50), D(10), "CNY", True),
        (D(70), D(10), "CNY", False),
        (D("69.99"), D(10), "CNY", True),
        (D(50), D(0), "CNY", True),
        (D(7), D(10), "USD", True),
        (D("11.4286"), D(10), "USD", False),
    ]:
        async with sandbox() as session:
            user, role, sku, customer, quote, version = await fixture(session, cost)
            version.currency = currency
            if currency != "CNY":
                version.exchange_rate_snapshot = D(7)
            item = await add_item(session, role, sku, customer, version, price)
            instance, required = await submit(session, user, role, quote, version)
            assert required is expected
            assert item.approval_required is expected
            assert version.approval_status == ("pending" if expected else "approved")
            if expected:
                assert instance and any("折扣" in row.get("reason", "") for row in instance.summary["offending"])

    async with sandbox() as session:
        user, role, sku, customer, quote, version = await fixture(session)
        item = await add_item(session, role, sku, customer, version, D(80))
        assert not item.approval_required
        permission = (await session.execute(select(PricePermission).where(PricePermission.role_id == role.id))).scalar_one()
        permission.discount_limit = D(".1")
        instance, required = await submit(session, user, role, quote, version)
        assert required and instance and item.approval_required

    async with sandbox() as session:
        user, role, sku, customer, quote, version = await fixture(session)
        await add_item(session, role, sku, customer, version, D(100))
        session.add_all([
            QuoteCharge(quote_version_id=version.id, charge_type="other", amount=D(-50), is_discount=True),
            QuoteCharge(quote_version_id=version.id, charge_type="other", amount=D(500), is_discount=False),
        ])
        await session.flush()
        await quotes.recalc_version(session, version)
        instance, required = await submit(session, user, role, quote, version)
        assert required and instance and "整单折扣" in instance.summary["offending"][0]["reason"]
    async with sandbox() as session:
        user, role, sku, customer, quote, version = await fixture(session)
        item = await add_item(session, role, sku, customer, version, D(100))
        item.standard_price_snapshot = None
        session.add(QuoteCharge(quote_version_id=version.id, charge_type="other", amount=D(-50), is_discount=True))
        await session.flush()
        await quotes.recalc_version(session, version)
        instance, required = await submit(session, user, role, quote, version)
        assert required and instance and "无标准价" in instance.summary["offending"][0]["reason"]
    async with sandbox() as session:
        user, role, sku, customer, quote, version = await fixture(session)
        await add_item(session, role, sku, customer, version, D(50))
        rule = ApprovalRule(name=role.code, kind="auto_pass", priority=1, enabled=True, published_version_no=1)
        session.add(rule)
        await session.flush()
        session.add(ApprovalRuleVersion(rule_id=rule.id, version_no=1,
                    payload={"name": role.code, "kind": "auto_pass", "conditions": [], "action": {}}, published_by=user.id))
        await session.flush()
        await _confirm_freight(session, version)
        instance, required = await quotes.submit_for_approval(
            session, quote=quote, version=version, applicant_id=user.id, user_roles=[role.code])
        assert required and instance and instance.summary["auto_passed"]
        assert instance.summary["rule_trace"]["rule_id"] == rule.id
        assert instance.summary["offending"] and version.approval_status == "approved"
    print("OK discount limits: missing cost, current role, FX, boundary, whole-order discount and audited published exemptions")


async def hard_floor_cases():
    for discount, blocked in [(D(-150), True), (D(-200), True), (D(-250), True), (D(-50), False)]:
        async with sandbox() as session:
            user, role, sku, customer, quote, version = await fixture(session, D(100))
            await add_item(session, role, sku, customer, version, D(200))
            session.add_all([
                QuoteCharge(quote_version_id=version.id, charge_type="logistics", amount=D(200),
                            is_discount=False, logistics_confirmed_at=datetime.now(UTC)),
                QuoteCharge(quote_version_id=version.id, charge_type="other", amount=discount, is_discount=True),
            ])
            await session.flush()
            await quotes.recalc_version(session, version)
            original = quotes.settings_service.get_text
            async def hard_on(s, key, field, default=""):
                return "cost" if (key, field) == ("hard_floor", "mode") else await original(s, key, field, default)
            with patch.object(quotes.settings_service, "get_text", hard_on):
                if blocked:
                    await expect_error(ErrorCode.PRICE_BELOW_HARD_FLOOR, lambda: submit(session, user, role, quote, version))
                    assert version.approval_status == "not_submitted"
                else:
                    await submit(session, user, role, quote, version)
    print("OK hard floor: positive, zero and negative goods revenue excludes pass-through freight")


async def foreign_and_rounding_cases():
    async with sandbox() as session:
        user, role, sku, customer, quote, version = await fixture(session)
        await session.execute(delete(ProductCost).where(ProductCost.sku_id == sku.id))
        await session.execute(delete(PriceRule).where(PriceRule.sku_id == sku.id))
        result = await pricing.calculate_price(session, sku_id=sku.id, quantity=D(1), quoted_price=D(10),
            role_codes=[role.code], currency="USD", exchange_rate=D(7), logistics_cost=D(0))
        assert result["recommended_price"] is None and result["recommended_range"] == [None, None]
        version.currency, version.exchange_rate_snapshot = "USD", D(7)
        row = await add_item(session, role, sku, customer, version, D(10))
        assert row.quoted_price == D(10) and row.recommended_price_snapshot is None

    for legacy_mismatch in (False, True):
        async with sandbox() as session:
            user, role, sku, customer, quote, version = await fixture(session)
            for _ in range(3):
                await add_item(session, role, sku, customer, version, D("33.3333"))
            assert version.subtotal_amount == D("99.99")
            built = await build_quote_doc(session, version.id)
            assert sum((D(str(row["amount"])) for row in built["items"]), D(0)) == version.subtotal_amount
            quote.status, version.approval_status = "accepted", "approved"
            version.sent_at = version.accepted_at = datetime.now(UTC)
            if legacy_mismatch:
                version.subtotal_amount = version.total_amount = D(100)
                await expect_error(ErrorCode.PARAM_ERROR, lambda: orders.create_order_from_quote(session, version=version, user_id=user.id))
                assert not (await session.execute(select(SalesOrder.id).where(SalesOrder.quote_version_id == version.id))).first()
            else:
                order = await orders.create_order_from_quote(session, version=version, user_id=user.id)
                lines = list((await session.execute(select(SalesOrderItem).where(SalesOrderItem.order_id == order.id))).scalars())
                plan = (await session.execute(select(ReceivablePlan).where(ReceivablePlan.order_id == order.id))).scalar_one()
                assert sum((row.amount for row in lines), D(0)) == order.goods_amount == order.total_amount == plan.amount == D("99.99")
    print("OK FX optional prices and quote/order/document/receivable line amounts; old mismatches rejected")


async def pdf_cases():
    async with sandbox() as session:
        user, role, sku, customer, quote, version = await fixture(session)
        await add_item(session, role, sku, customer, version, D(100))
        caller = CurrentUser(user, {"quote:view"}, ["admin"], "all")
        quote.status, version.approval_status = "approved", "approved"
        for endpoint in (download_pdf, generate_pdf_alias):
            await expect_error(ErrorCode.STATUS_NOT_ALLOWED, lambda: endpoint(version.id, user=caller, session=session))
        await master.confirm_local_master(session, sku_id=sku.id, operator_id=user.id)
        # Refresh only draft contents, then submit; the gate must examine this exact snapshot.
        version.approval_status = "not_submitted"
        await quotes.link_master_only(session, version=version)
        version.approval_status = "approved"
        for endpoint in (download_pdf, generate_pdf_alias):
            await expect_error(ErrorCode.STATUS_NOT_ALLOWED, lambda: endpoint(version.id, user=caller, session=session))
        session.add(QuoteCharge(quote_version_id=version.id, charge_type="logistics", amount=D(0),
                               is_discount=False, logistics_confirmed_at=datetime.now(UTC)))
        await session.flush()
        for endpoint in (download_pdf, generate_pdf_alias):
            response = await endpoint(version.id, user=caller, session=session)
            assert response.status_code == 200 and response.body.startswith(b"%PDF")
    print("OK both PDF routes: unconfirmed master/freight blocked, confirmed zero freight allowed")


async def agent_cases():
    async with sandbox() as session:
        user, role, sku, customer, quote, version = await fixture(session)
        stage = (await session.execute(select(OpportunityStage).where(OpportunityStage.status == "active").limit(1))).scalar_one()
        opportunity = Opportunity(title=role.code, customer_id=customer.id, owner_id=user.id, stage_id=stage.id)
        agent_session = AgentSession(user_id=user.id, title=role.code)
        session.add_all([opportunity, agent_session])
        await session.flush()
        session.add(OpportunityItem(opportunity_id=opportunity.id, sku_id=sku.id, quantity=1))
        sku.status = "inactive"
        action = AgentAction(session_id=agent_session.id, action_type="create_quote_draft", tool_name="create_quote_draft",
                             risk_level="L2", title="create quote", proposed_payload={"opportunity_id": opportunity.id})
        session.add(action)
        await session.commit()
        caller = CurrentUser(user, {"quote:manage", "task:manage"}, ["admin"], "all")
        oid = opportunity.id
        await expect_error(ErrorCode.STATUS_NOT_ALLOWED, lambda: execute_action(session, action=action, user=caller))
        assert action.status == "failed"
        failed_execution = (await session.execute(select(AgentExecution).where(AgentExecution.action_id == action.id))).scalar_one()
        assert failed_execution.status == "failed" and failed_execution.input_payload["opportunity_id"] == oid
        count = await session.scalar(select(func.count()).select_from(Quote).where(Quote.opportunity_id == oid))
        assert count == 0, f"failed tool persisted {count} partial quotes"

        action = AgentAction(session_id=agent_session.id, action_type="create_task", tool_name="create_task", risk_level="L2",
                             title=role.code, proposed_payload={"title": role.code})
        session.add(action)
        await session.commit()
        result = await execute_action(session, action=action, user=caller)
        assert result["task_id"] and action.status == "executed"
        await expect_error(ErrorCode.STATUS_NOT_ALLOWED, lambda: execute_action(session, action=action, user=caller))
        assert await session.scalar(select(func.count()).select_from(Task).where(Task.title == role.code)) == 1
    print("OK Agent failed writes rollback, failure trace persists, successful actions execute once")


async def reconciliation_cases():
    async with SessionLocal() as session:
        row = IntegrationDiff(domain="order", system_type="jushuitan", object_type="order", diff_type="amount_mismatch",
                              diff_key="CHKRISK" + uuid4().hex, current_value={"amount":"100"}, incoming_value={"amount":"200"})
        session.add(row)
        await session.commit()
        ident = row.id
    try:
        async with SessionLocal() as first, SessionLocal() as second:
            a, b = await first.get(IntegrationDiff, ident), await second.get(IntegrationDiff, ident)
            await reconcile.confirm_diff(first, a, resolution="keep_local", note="first", operator_id=None)
            await first.commit()
            await expect_error(ErrorCode.DUPLICATE, lambda: reconcile.confirm_diff(second, b, resolution="take_external", note="stale", operator_id=None))
            replay = await reconcile.confirm_diff(second, b, resolution="keep_local", note="replay", operator_id=None)
            assert replay["diff"]["resolution"] == "keep_local" and replay["diff"]["resolve_note"] == "first"
            await second.commit()
    finally:
        async with SessionLocal() as session:
            await session.execute(delete(IntegrationDiff).where(IntegrationDiff.id == ident))
            await session.commit()
    print("OK stale reconciliation confirmation rejected; same decision replays original evidence")


async def main():
    try:
        await discount_cases()
        await hard_floor_cases()
        await foreign_and_rounding_cases()
        await pdf_cases()
        await agent_cases()
        await reconciliation_cases()
    finally:
        await engine.dispose()
    print("全部通过")


if __name__ == "__main__":
    asyncio.run(main())
