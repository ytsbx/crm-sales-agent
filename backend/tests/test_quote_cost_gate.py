"""正式报价必须有完整成本；仅在内存SQLite中验证。"""
import pytest
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.core.errors import AppError
from test_batch7_cost_lifecycle import case, build, submit, USER, REQUEST, NOW  # noqa: F401
from app.modules.quote import service as quotes, router as quote_router, lifecycle
from app.modules.pricing import service as pricing
from app.modules.pricing.model import ProductCost


@pytest.mark.anyio
@pytest.mark.parametrize("cost", ["absent", "partial"])
async def test_formal_submit_rejects_missing_or_partial_cost(case, cost):
    raw, session, quote, version = case
    await build(raw, session, version, cost=cost)
    with pytest.raises(AppError, match="成本"):
        await submit(session, quote, version)
    assert version.approval_status == "not_submitted"
    assert quote.status == "draft"


@pytest.mark.anyio
@pytest.mark.parametrize("amount", ["0", "10"])
async def test_explicit_complete_cost_including_zero_can_submit(case, amount):
    raw, session, quote, version = case
    await build(raw, session, version, cost="complete", amount=amount)
    await submit(session, quote, version)
    assert version.approval_status == "approved"


@pytest.mark.anyio
@pytest.mark.parametrize("entry", ["pdf", "pdf_alias", "send", "email", "order", "excel", "archive", "file"])
async def test_formal_exits_cannot_bypass_missing_cost(case, monkeypatch, entry):
    raw, session, quote, version = case
    await build(raw, session, version, cost="absent")
    version.approval_status = "approved"  # 历史已免审的版本也不能绕过新规则。
    quote.status = "approved"
    from app.modules.bizdoc import service as docs
    from app.modules.bizdoc.model import BizDoc
    doc = SimpleNamespace(doc_type="quote_sheet", quote_id=quote.id,
                          source_version=version.version_no)
    from app.modules.quote.schema import SendRequest
    payload = SendRequest(channel="邮件", receiver="test@example.invalid")
    with pytest.raises(AppError, match="成本"):
        if entry == "pdf":
            await quote_router.download_pdf(version.id, USER, session)
        elif entry == "pdf_alias":
            await quote_router.generate_pdf_alias(version.id, USER, session)
        elif entry == "send":
            from app.modules.notification.model import BusinessEvent
            BusinessEvent.__table__.create(raw.get_bind(), checkfirst=True)
            await lifecycle.mark_version_sent(session, quote=quote, version=version,
                                             operator_id=USER.id, payload=payload)
        elif entry == "email":
            await quote_router.send_email(version.id, payload, REQUEST, USER, session)
        elif entry == "order":
            from app.modules.order import service as orders
            from app.modules.order.model import SalesOrder
            SalesOrder.__table__.create(raw.get_bind(), checkfirst=True)
            await orders.create_order_from_quote(session, version=version, user_id=USER.id)
        elif entry == "excel":
            monkeypatch.setattr(docs, "build_quote_doc", AsyncMock(return_value={"owner_id": USER.id}))
            template = AsyncMock()
            monkeypatch.setattr(docs, "current_template", template)
            try:
                await docs.generate_quote_doc(session, quote_version_id=version.id, user=USER)
            finally:
                template.assert_not_awaited()  # 拦截发生在渲染/写文件之前。
        elif entry == "archive":
            await docs.ensure_quote_doc_cost_complete(session, doc)
        else:
            BizDoc.__table__.create(raw.get_bind(), checkfirst=True)
            raw.add(BizDoc(doc_no="OLD", title="历史报价", created_by=USER.id, created_at=NOW,
                           doc_type="quote_sheet", file_id=99,
                           quote_id=quote.id, source_version=version.version_no,
                           template_id=1, input_snapshot={}, content_sha256="x" * 64))
            raw.flush()
            await docs.ensure_quote_file_cost_complete(session, 99)
    assert version.sent_at is None
    assert quote.status == "approved"


@pytest.mark.anyio
@pytest.mark.parametrize("present,complete", [(False, True), (None, None), (True, False)])
async def test_ambiguous_and_inconsistent_historical_snapshots_rejected(case, present, complete):
    raw, session, quote, version = case
    item = await build(raw, session, version, cost="complete")
    item.cost_present_snapshot, item.cost_complete_snapshot = present, complete
    with pytest.raises(AppError, match="成本"):
        await quotes.ensure_items_cost_complete(session, version_id=version.id)


@pytest.mark.anyio
async def test_refresh_cost_preserves_price_quantity_master_and_history(case):
    raw, session, quote, version = case
    item = await build(raw, session, version, cost="absent")
    frozen = (item.quoted_price, item.quantity, item.spec_snapshot,
              item.unit_snapshot, item.sku_name_snapshot, item.price_source)
    raw.add(ProductCost(sku_id=1, effective_from=date(2020, 1, 1), purchase_cost=Decimal(23),
                        production_cost=Decimal(0), package_cost=Decimal(0), processing_cost=Decimal(0)))
    raw.flush()
    assert item.cost_present_snapshot is False  # 保存成本不会自动覆盖已有报价快照。
    result = await quotes.refresh_cost_snapshots(session, version=version, user=USER)
    assert result["refreshed"] == 1
    assert item.cost_snapshot == Decimal(23) and item.cost_complete_snapshot is True
    assert frozen == (item.quoted_price, item.quantity, item.spec_snapshot,
                      item.unit_snapshot, item.sku_name_snapshot, item.price_source)
    await quotes.ensure_items_cost_complete(session, version_id=version.id)
    version.approval_status = "approved"
    with pytest.raises(AppError):
        await quotes.refresh_cost_snapshots(session, version=version, user=USER)
    assert item.cost_snapshot == Decimal(23)


@pytest.mark.anyio
@pytest.mark.parametrize("state", ["missing", "complete", "incomplete", "expired", "future", "stopped", "conflict"])
async def test_product_cost_status_uses_effective_records_without_amounts(case, state):
    raw, session, _, _ = case
    from app.core.timebase import today_business
    today = today_business()
    if state != "missing":
        record = ProductCost(sku_id=1, effective_from=today,
                             purchase_cost=Decimal(0), production_cost=Decimal(0),
                             package_cost=Decimal(0), processing_cost=Decimal(0))
        if state == "incomplete": record.processing_cost = None
        if state == "expired":
            record.effective_from = today - timedelta(days=2)
            record.effective_to = today - timedelta(days=1)
        if state == "future": record.effective_from = today + timedelta(days=1)
        if state == "stopped": record.stopped_at = NOW
        raw.add(record)
        if state == "conflict": raw.add(ProductCost(sku_id=1, effective_from=today))
        raw.flush()
    statuses = await pricing.sku_cost_statuses(session, [1])
    expected = "missing" if state in ("expired", "future", "stopped") else state
    assert statuses[1]["state"] == expected
    assert set(statuses[1]) == {"state", "label"}  # 无价格权限也绝不泄露金额。


@pytest.mark.anyio
async def test_detail_cost_problems_no_cost_amounts(case):
    raw, session, _, version = case
    item = await build(raw, session, version, cost="partial")
    problem = quotes.item_cost_problems([item])[0]
    assert set(problem) == {"item_id", "sku_id", "label", "reason"}
    assert problem["reason"] == "成本未填完整"


@pytest.mark.parametrize("total", [Decimal('99999999999999.99'), None])
def test_quote_version_exposes_exact_total_without_float_rounding(total):
    from app.modules.quote.model import QuoteVersion
    version = QuoteVersion(id=1, quote_id=1, version_no=1,
                           total_amount=total, currency='CNY')
    result = quotes.serialize_version(version)
    assert result['total_amount_exact'] == (str(total) if total is not None else None)


def test_quote_item_and_charge_expose_exact_amounts_without_float_rounding():
    from app.modules.quote.model import QuoteItem, QuoteCharge
    item = QuoteItem(id=1, quote_version_id=1, quantity=Decimal('100'),
                     quoted_price=Decimal('999999999999.9999'))
    charge = QuoteCharge(id=1, quote_version_id=1, amount=Decimal('0.01'))
    item_result = quotes.serialize_item(item)
    charge_result = quotes.serialize_charge(charge)
    assert item_result['quoted_price_exact'] == '999999999999.9999'
    assert item_result['amount_exact'] == '99999999999999.99'
    assert charge_result['amount_exact'] == '0.01'


def test_quote_list_preserves_exact_total_and_missing_version():
    from app.modules.quote.model import Quote, QuoteVersion
    quote = Quote(id=1, quote_no='Q-EXACT', customer_id=1, status='draft')
    version = QuoteVersion(id=2, quote_id=1, total_amount=Decimal('99999999999999.99'), currency='CNY')
    assert quotes.serialize_quote(quote, version=version)['current_version_amount_exact'] == '99999999999999.99'
    assert quotes.serialize_quote(quote)['current_version_amount_exact'] is None


@pytest.mark.anyio
async def test_comparison_preserves_exact_product_amount_without_exposing_cost():
    from app.modules.quote.model import QuoteItem, QuoteVersion
    from unittest.mock import Mock
    version = QuoteVersion(id=2, quote_id=1, version_no=1, currency='CNY',
                           total_amount=Decimal('99999999999999.99'))
    item = QuoteItem(id=1, quote_version_id=2, sku_id=1, quantity=Decimal('100'),
                     quoted_price=Decimal('999999999999.9999'),
                     cost_snapshot=Decimal('1'), package_cost_snapshot=Decimal('0'),
                     logistics_cost_snapshot=Decimal('0'), profit_snapshot=Decimal('999999999998.9999'))
    results = []
    for rows in [[version], [item], []]:
        result = Mock()
        result.scalars.return_value.all.return_value = rows
        results.append(result)
    session = SimpleNamespace(execute=AsyncMock(side_effect=results))
    data = await quotes.version_comparison(session, 1)
    assert data['versions'][0]['amount_total_exact'] == '99999999999999.99'
    redacted = quotes.strip_comparison_cost(data)
    assert redacted['versions'][0]['amount_total_exact'] == '99999999999999.99'
    assert redacted['versions'][0]['cost_total'] is None


@pytest.mark.anyio
@pytest.mark.parametrize('before_price,after_price,expected', [
    ('999999999999.9998', '999999999999.9999', '0.01'),
    ('999999999999.9999', '999999999999.9998', '-0.01'),
    ('1.0000', '1.0001', '0.01'),
    ('1.0000', '1.0000', '0.00'),
])
async def test_comparison_delta_preserves_cent_changes(before_price, after_price, expected):
    from app.modules.quote.model import QuoteItem, QuoteVersion
    from unittest.mock import Mock
    versions = [QuoteVersion(id=i, quote_id=1, version_no=i, currency='CNY',
                             charge_amount=Decimal('0.00'), discount_amount=Decimal('0.00'))
                for i in (1, 2)]
    items = [QuoteItem(id=i, quote_version_id=i, sku_id=1, quantity=Decimal('100'),
                       quoted_price=Decimal(price), cost_snapshot=Decimal('1'),
                       logistics_cost_snapshot=Decimal('0'), profit_snapshot=Decimal('0'))
             for i, price in enumerate((before_price, after_price), 1)]
    results = []
    for rows in (versions, items, []):
        result = Mock()
        result.scalars.return_value.all.return_value = rows
        results.append(result)
    session = SimpleNamespace(execute=AsyncMock(side_effect=results))
    data = await quotes.version_comparison(session, 1)
    assert Decimal(str(data['diffs'][0]['amount_delta'])) == Decimal(expected)
    assert data['diffs'][0]['amount_delta_exact'] == expected


@pytest.mark.anyio
@pytest.mark.parametrize('field,before,after', [
    ('charge_amount', '99999999999999.98', '99999999999999.99'),
    ('discount_amount', '-99999999999999.98', '-99999999999999.99'),
    ('charge_amount', '10.00', '10.01'),
    ('discount_amount', '-10.00', '-10.01'),
    ('charge_amount', '10.00', '10.00'),
    ('discount_amount', '-10.00', '-10.00'),
])
async def test_comparison_fee_changes_preserve_exact_values(field, before, after):
    from app.modules.quote.model import QuoteVersion
    from unittest.mock import Mock
    versions = [QuoteVersion(id=i, quote_id=1, version_no=i, currency='CNY',
                             charge_amount=Decimal('0.00'), discount_amount=Decimal('0.00'))
                for i in (1, 2)]
    for version, value in zip(versions, (before, after), strict=True):
        setattr(version, field, Decimal(value))
    results = []
    for rows in (versions, [], []):
        result = Mock()
        result.scalars.return_value.all.return_value = rows
        results.append(result)
    data = await quotes.version_comparison(SimpleNamespace(execute=AsyncMock(side_effect=results)), 1)
    changes = data['diffs'][0]['changes']
    if before == after:
        assert changes == []
    else:
        assert len(changes) == 1
        assert changes[0]['field'] == field
        assert changes[0]['before_exact'] == before
        assert changes[0]['after_exact'] == after
        assert changes[0]['before'] == float(before)
        assert changes[0]['after'] == float(after)


@pytest.mark.anyio
@pytest.mark.parametrize('before_profit,after_profit,expected', [
    ('999999999998.9998', '999999999998.9999', '0.01'),
    ('999999999998.9999', '999999999998.9998', '-0.01'),
    ('-999999999998.9998', '-999999999998.9999', '-0.01'),
    ('1.0000', '1.0001', '0.01'),
    ('1.0000', '1.0000', '0.00'),
])
async def test_comparison_profit_delta_preserves_cents(before_profit, after_profit, expected):
    from app.modules.quote.model import QuoteItem, QuoteVersion
    from unittest.mock import Mock
    versions = [QuoteVersion(id=i, quote_id=1, version_no=i, currency='CNY',
                             charge_amount=Decimal('0.00'), discount_amount=Decimal('0.00'))
                for i in (1, 2)]
    items = [QuoteItem(id=i, quote_version_id=i, sku_id=1, quantity=Decimal('100'),
                       quoted_price=Decimal('999999999999.9999'),
                       cost_snapshot=Decimal('1'), logistics_cost_snapshot=Decimal('0'),
                       profit_snapshot=Decimal(profit))
             for i, profit in enumerate((before_profit, after_profit), 1)]
    results = []
    for rows in (versions, items, []):
        result = Mock()
        result.scalars.return_value.all.return_value = rows
        results.append(result)
    data = await quotes.version_comparison(SimpleNamespace(execute=AsyncMock(side_effect=results)), 1)
    assert Decimal(str(data['diffs'][0]['profit_delta'])) == Decimal(expected)
    assert data['diffs'][0]['profit_delta_exact'] == expected


@pytest.mark.anyio
@pytest.mark.parametrize('kind', ['quoted_price', 'quantity', 'added', 'removed'])
async def test_comparison_item_changes_preserve_exact_values(kind):
    from app.modules.quote.model import QuoteItem, QuoteVersion
    from unittest.mock import Mock
    versions = [QuoteVersion(id=i, quote_id=1, version_no=i, currency='CNY',
                             charge_amount=Decimal('0.00'), discount_amount=Decimal('0.00'))
                for i in (1, 2)]
    values = ('9999999999999.988', '9999999999999.989') if kind == 'quantity' else (
        '999999999999.9991', '999999999999.9992')
    items = [QuoteItem(id=i, quote_version_id=i, sku_id=1,
                       quantity=Decimal(value) if kind == 'quantity' else Decimal('1'),
                       quoted_price=Decimal('0.0001') if kind == 'quantity' else Decimal(value),
                       cost_snapshot=Decimal('1'), logistics_cost_snapshot=Decimal('0'),
                       profit_snapshot=Decimal('0'))
             for i, value in enumerate(values, 1)]
    if kind == 'added':
        items = items[1:]
    elif kind == 'removed':
        items = items[:1]
    results = []
    for rows in (versions, items, []):
        result = Mock()
        result.scalars.return_value.all.return_value = rows
        results.append(result)
    data = await quotes.version_comparison(SimpleNamespace(execute=AsyncMock(side_effect=results)), 1)
    change = data['diffs'][0]['changes'][0]
    assert change['field'] == kind
    assert change['before_exact'] == (None if kind == 'added' else values[0])
    assert change['after_exact'] == (None if kind == 'removed' else values[1])
    assert change['before'] == (None if kind == 'added' else float(values[0]))
    assert change['after'] == (None if kind == 'removed' else float(values[1]))


@pytest.mark.parametrize('value,expected', [
    ('0', '0.000000'), ('1', '1.000000'), ('-9999', '-9999.000000'),
    ('-9999.999999', '-9999.999999'), ('9999.999999', '9999.999999'),
    ('-9999.9999994', '-9999.999999'),
])
def test_profit_rate_snapshot_storage_boundary(value, expected):
    assert quotes._stored_profit_rate(Decimal(value)) == Decimal(expected)


@pytest.mark.parametrize('value', ['-10000', '10000', '-199999', '-9999.9999996'])
def test_profit_rate_snapshot_overflow_has_actionable_error(value):
    with pytest.raises(AppError, match='亏损率超出可保存范围'):
        quotes._stored_profit_rate(Decimal(value))


@pytest.mark.anyio
async def test_sku_snapshot_rejects_unstorable_profit_rate(case):
    raw, session, quote, version = case
    await build(raw, session, version, cost='complete', amount='20')
    with pytest.raises(AppError, match='亏损率超出可保存范围'):
        await quotes.build_item_snapshot(
            session, version=version, sku_id=1, quantity=Decimal('1'), customer_id=1,
            quoted_price=Decimal('0.0001'), logistics_cost=Decimal('0'),
            opportunity_item_id=None, spec_snapshot=None, remark=None, role_codes=USER.roles)


@pytest.mark.anyio
@pytest.mark.parametrize('price,rejected', [('0.0001', True), ('0.0020', False)])
async def test_basis_refresh_checks_profit_rate_storage(monkeypatch, price, rejected):
    from app.modules.quote.model import QuoteItem, QuoteVersion
    item = QuoteItem(id=1, sku_id=None, quote_version_id=1,
                     quantity=Decimal('1'), quoted_price=Decimal(price),
                     cost_snapshot=Decimal('20'), package_cost_snapshot=Decimal('0'),
                     logistics_cost_snapshot=Decimal('0'), tax_refund_snapshot=Decimal('0'),
                     cost_present_snapshot=True, cost_complete_snapshot=True)
    version = QuoteVersion(id=1, quote_id=1, currency='CNY')
    monkeypatch.setattr(pricing, 'resolve_min_margin', AsyncMock(return_value=(Decimal('0'), True)))
    if rejected:
        with pytest.raises(AppError, match='亏损率超出可保存范围'):
            await quotes.apply_current_basis_to_item(SimpleNamespace(), item, version, user=USER)
    else:
        await quotes.apply_current_basis_to_item(SimpleNamespace(), item, version, user=USER)
        assert item.profit_rate_snapshot == Decimal('-9999.000000')


@pytest.mark.anyio
@pytest.mark.parametrize('discount', [False, True])
async def test_quote_fee_aggregate_overflow_is_rejected_before_storage(case, discount):
    from app.modules.quote.model import QuoteCharge
    raw, session, quote, version = case
    for is_discount in (discount, not discount, discount):
        raw.add(QuoteCharge(quote_version_id=version.id, charge_type='other',
                            amount=Decimal('-60000000000000') if is_discount else Decimal('60000000000000'),
                            is_discount=is_discount))
    raw.flush()
    with pytest.raises(AppError, match='超出可保存范围'):
        await quotes.recalc_version(session, version)


@pytest.mark.anyio
async def test_quote_fee_offset_with_storable_aggregates_remains_valid(case):
    from app.modules.quote.model import QuoteCharge
    raw, session, quote, version = case
    for is_discount in (False, True):
        raw.add(QuoteCharge(quote_version_id=version.id, charge_type='other',
                            amount=Decimal('-60000000000000') if is_discount else Decimal('60000000000000'),
                            is_discount=is_discount))
    raw.flush()
    await quotes.recalc_version(session, version)
    assert version.total_amount == Decimal('0.00')
    assert version.charge_amount == Decimal('60000000000000.00')
    assert version.discount_amount == Decimal('-60000000000000.00')


@pytest.mark.parametrize('value,rejected', [
    ('999999999999.99', False), ('999999999999.9999', False),
    ('1000000000000.00', True), ('999999999999.99996', True),
])
def test_quote_floor_storage_range(value, rejected):
    if rejected:
        with pytest.raises(AppError, match='底价超出可保存范围'):
            quotes._assert_floor_in_range(Decimal(value))
    else:
        quotes._assert_floor_in_range(Decimal(value))


@pytest.mark.anyio
async def test_basis_refresh_rejects_floor_rounding_overflow(monkeypatch):
    from app.modules.quote.model import QuoteItem, QuoteVersion
    item = QuoteItem(id=1, sku_id=None, quote_version_id=1,
                     quantity=Decimal('1'), quoted_price=Decimal('999999999999.9999'),
                     cost_snapshot=Decimal('999999999999.9999'), package_cost_snapshot=Decimal('0'),
                     logistics_cost_snapshot=Decimal('0'), tax_refund_snapshot=Decimal('0'),
                     cost_present_snapshot=True, cost_complete_snapshot=True)
    version = QuoteVersion(id=1, quote_id=1, currency='CNY')
    monkeypatch.setattr(pricing, 'resolve_min_margin', AsyncMock(return_value=(Decimal('0'), True)))
    with pytest.raises(AppError, match='底价超出可保存范围'):
        await quotes.apply_current_basis_to_item(SimpleNamespace(), item, version, user=USER)


@pytest.mark.anyio
async def test_sku_snapshot_rejects_computed_floor_overflow(case, monkeypatch):
    raw, session, quote, version = case
    original = pricing.calculate_price
    async def calculate_with_high_floor(*args, **kwargs):
        result = await original(*args, **kwargs)
        result['minimum_price_cny'] = 1000000000000.00
        kwargs['snapshot_out']['minimum_price_cny'] = Decimal('1000000000000.00')
        return result
    monkeypatch.setattr(pricing, 'calculate_price', calculate_with_high_floor)
    with pytest.raises(AppError, match='底价超出可保存范围'):
        await quotes.build_item_snapshot(
            session, version=version, sku_id=1, quantity=Decimal('1'), customer_id=1,
            quoted_price=Decimal('100'), logistics_cost=Decimal('0'),
            opportunity_item_id=None, spec_snapshot=None, remark=None, role_codes=USER.roles)


@pytest.mark.anyio
async def test_auto_quote_price_and_price_snapshots_keep_decimal_rule(case, monkeypatch):
    from app.modules.pricing.model import PriceRule
    raw, session, quote, version = case
    expected = Decimal('999999999999.9992')
    # SQLite Numeric 自身先经浮点存取，不能作为大额 Decimal 源值的证据。
    # 用原始 Decimal 规则替换查价结果，实际计算和快照构建照常执行。
    rule = PriceRule(sku_id=1, effective_from=date(2020, 1, 1),
                     standard_price=expected, guide_price=expected, minimum_price=expected)
    monkeypatch.setattr(pricing, 'find_price_rule', AsyncMock(return_value=rule))
    item = await quotes.build_item_snapshot(
        session, version=version, sku_id=1, quantity=Decimal('100'), customer_id=1,
        quoted_price=None, logistics_cost=Decimal('0'), opportunity_item_id=None,
        spec_snapshot=None, remark=None, role_codes=USER.roles)
    assert item.quoted_price == expected
    assert item.standard_price_snapshot == expected
    assert item.recommended_price_snapshot == expected
    assert item.minimum_price_snapshot == expected
    snapshots = {}
    public = await pricing.calculate_price(
        session, sku_id=1, quantity=Decimal('100'), customer_id=1,
        role_codes=USER.roles, snapshot_out=snapshots)
    assert snapshots['recommended_price'] == expected
    assert snapshots['minimum_price_cny'] == expected
    assert isinstance(public['recommended_price'], float)
    assert 'snapshot_out' not in public and 'snapshot_values' not in public


@pytest.mark.anyio
@pytest.mark.parametrize('changes,expected_source', [
    ({'quoted_price': Decimal('101')}, None),
    ({'quoted_price': Decimal('100'), 'quantity': Decimal('2')}, 'general'),
    ({'remark': '只改备注'}, 'general'),
])
async def test_edit_price_marks_manual_only_on_actual_price_change(case, changes, expected_source):
    from app.modules.quote.schema import QuoteItemUpdate
    raw, session, quote, version = case
    item = await build(raw, session, version, cost='complete')
    item.price_source = 'general'
    raw.commit()
    await quote_router.update_item(item.id, QuoteItemUpdate(**changes), REQUEST, USER, session)
    assert item.price_source == expected_source


@pytest.mark.anyio
@pytest.mark.parametrize('specific', [False, True])
async def test_lookup_keeps_decimal_unit_price_for_quote_callers(case, monkeypatch, specific):
    from app.modules.pricing.model import PriceRule, CustomerPriceRule
    from app.modules.customer.model import Customer
    raw, session, quote, version = case
    expected = Decimal('999999999999.9992')
    rule = PriceRule(sku_id=1, standard_price=expected, guide_price=expected)
    agreed = CustomerPriceRule(customer_id=1, sku_id=1, agreed_price=expected) if specific else None
    monkeypatch.setattr(pricing, 'find_price_rule', AsyncMock(return_value=rule))
    monkeypatch.setattr(pricing, 'find_customer_price', AsyncMock(return_value=agreed))
    exact = {}
    data = await pricing.lookup_applicable_price(
        session, customer=raw.get(Customer, 1), sku_id=1, quantity=Decimal('100'), snapshot_out=exact)
    assert exact['unit_price'] == expected
    assert data['unit_price'] == float(expected)
    assert 'snapshot_out' not in data


@pytest.mark.anyio
@pytest.mark.parametrize('status', ['pending', 'approved'])
@pytest.mark.parametrize('entry', ['price', 'cost'])
async def test_refresh_locked_version_preserves_item(case, status, entry):
    raw, session, quote, version = case
    item = await build(raw, session, version, cost='complete')
    item.price_source = 'general'
    version.approval_status = status
    raw.commit()
    before = (item.quoted_price, item.cost_snapshot, item.price_source, version.total_amount)
    with pytest.raises(AppError):
        if entry == 'price':
            await quote_router.price_refresh(version.id, REQUEST, USER, session)
        else:
            await quote_router.cost_refresh(version.id, REQUEST, USER, session)
    assert (item.quoted_price, item.cost_snapshot, item.price_source, version.total_amount) == before
