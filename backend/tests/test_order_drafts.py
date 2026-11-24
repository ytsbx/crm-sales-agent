from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4
import pytest
from pydantic import ValidationError
from app.core.errors import AppError
from app.modules.order.drafts import require_editable
from app.modules.order.schema import OrderDraftCreate
from app.modules.bizdoc.service import _diff_lines


def test_draft_requires_explicit_positive_quantity_and_never_uses_target_price():
    body = {'inquiry_id': 1, 'request_key': uuid4(), 'items': [{'source_item_id': 1}]}
    with pytest.raises(ValidationError): OrderDraftCreate(**body)
    body['items'][0]['quantity'] = 10000
    parsed = OrderDraftCreate(**body)
    assert parsed.items[0].quantity == Decimal(10000) and parsed.items[0].unit_price is None


def test_duplicate_source_items_rejected():
    with pytest.raises(ValidationError):
        OrderDraftCreate(inquiry_id=1, request_key=uuid4(), items=[{'source_item_id':1,'quantity':1}]*2)


@pytest.mark.parametrize('status,revision', [('converted',1),('draft',2)])
def test_converted_or_stale_draft_is_not_editable(status,revision):
    with pytest.raises(AppError): require_editable(SimpleNamespace(status=status,revision=revision),1)


def test_repeated_sku_rows_match_by_original_line_id():
    rows=[{'quote_item_id':11,'sku_id':1,'name':'重复商品','quantity':10},
          {'quote_item_id':12,'sku_id':1,'name':'重复商品','quantity':20}]
    assert _diff_lines(rows,rows,'quote_item_id')==[]
    assert _diff_lines(rows,[{**row,'quantity':str(Decimal(row['quantity']).quantize(Decimal('0.001')))} for row in rows],'quote_item_id')==[]
    changed=[{**rows[0],'quantity':5},rows[1]]
    result=_diff_lines(changed,rows,'quote_item_id')
    assert len(result)==1 and result[0]['before']=='10' and result[0]['after']=='5'
    legacy=_diff_lines(rows,rows,('sku_id','inquiry_id'))
    assert all(d['field']=='来源匹配' for d in legacy)


@pytest.mark.anyio
@pytest.mark.parametrize("price_fields, expected", [
    ({}, Decimal("12.3456")),
    ({"unit_price": None}, None),
    ({"unit_price": "0"}, Decimal("0")),
])
async def test_update_draft_distinguishes_omitted_price_from_explicit_values(monkeypatch, price_fields, expected):
    from unittest.mock import AsyncMock
    from app.modules.order import drafts
    from app.modules.order.schema import OrderDraftUpdate

    draft = SimpleNamespace(id=1, status="draft", revision=1)
    item = SimpleNamespace(source_snapshot={"source_item_id": 1}, unit_price=Decimal("12.3456"))
    monkeypatch.setattr(drafts, "get_visible", AsyncMock(return_value=draft))
    monkeypatch.setattr(drafts, "items", AsyncMock(return_value=[item]))
    monkeypatch.setattr(drafts, "detail", AsyncMock(return_value={}))
    monkeypatch.setattr(drafts, "write_audit", AsyncMock())
    payload = OrderDraftUpdate(
        revision=1, items=[{"source_item_id": 1, "quantity": "2", **price_fields}],
    )
    await drafts.update(SimpleNamespace(flush=AsyncMock()), SimpleNamespace(id=1), draft.id, payload)
    assert (item.quantity, item.unit_price) == (Decimal("2"), expected)
    assert draft.revision == 2
