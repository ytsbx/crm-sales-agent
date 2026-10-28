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
