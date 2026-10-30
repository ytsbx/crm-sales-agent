"""PDF路由及真实渲染表格不得把报价Decimal快照变成有损浮点。"""
from decimal import Decimal as D
from unittest.mock import AsyncMock

import pytest

from app.modules.quote import pdf, router, service
from app.modules.quote.model import QuoteCharge
from test_batch7_cost_lifecycle import case as cost_case, build, USER, NOW

case = cost_case


@pytest.mark.anyio
@pytest.mark.parametrize('kind', ['goods', 'logistics', 'other'])
async def test_pdf_tables_preserve_snapshot_cents(case, monkeypatch, kind):
    raw, session, quote, version = case
    item = await build(raw, session, version, cost='complete')
    item.quantity = D('100') if kind == 'goods' else D('1')
    item.quoted_price = D('999999999999.9999') if kind == 'goods' else D('1')
    goods = D('99999999999999.99') if kind == 'goods' else D('1')
    fee = D('0') if kind == 'goods' else D('99999999999998.99')
    version.subtotal_amount = goods
    version.logistics_amount = fee if kind == 'logistics' else D('0')
    version.other_charge_amount = fee if kind == 'other' else D('0')
    version.charge_amount = fee
    version.discount_amount = D('0')
    version.total_amount = D('99999999999999.99')
    version.approval_status = 'approved'
    if fee:
        large_charge = QuoteCharge(quote_version_id=version.id, charge_type='logistics' if kind == 'logistics' else 'other', amount=fee, logistics_confirmed_at=NOW if kind == 'logistics' else None)
        raw.add(large_charge)
    raw.flush()
    # Master-data validity is orthogonal to numeric projection; the fixture intentionally lacks confirmations.
    monkeypatch.setattr(service, 'ensure_items_master_confirmed', AsyncMock())
    cells = []
    original_table = pdf.Table

    def capture_table(data, *args, **kwargs):
        cells.extend(str(cell) for row in data for cell in row if isinstance(cell, str))
        return original_table(data, *args, **kwargs)

    monkeypatch.setattr(pdf, 'Table', capture_table)
    response = await router.download_pdf(version.id, USER, session)
    assert response.body.startswith(b'%PDF')
    assert '¥99999999999999.99' in cells, cells
    if kind == 'goods':
        assert '99999999999999.99' in cells, cells
    else:
        assert '99999999999998.99' in cells, cells
        assert '¥99999999999998.99' in cells, cells
