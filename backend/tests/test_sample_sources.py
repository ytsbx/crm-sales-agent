from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.sample.model import SampleItem
from app.modules.sample.schema import SampleFromSource, SampleSource
from app.modules.sample.service import serialize_item


@pytest.mark.parametrize('payload', [{}, {'quote_version_id': 1, 'inquiry_id': 2}, {'inquiry_id': 0}])
def test_source_requires_one_valid_reference(payload):
    with pytest.raises(ValidationError):
        SampleSource(**payload)


def test_default_one_does_not_require_procurement_quantity():
    payload = SampleFromSource(inquiry_id=1, request_key=uuid4(), items=[{'source_item_id': 1}])
    assert payload.items[0].quantity == Decimal(1)


@pytest.mark.parametrize('quantity', [0, -1, '1.0001', '10000000000000'])
def test_invalid_sample_quantity_rejected(quantity):
    with pytest.raises(ValidationError):
        SampleFromSource(inquiry_id=1, request_key=uuid4(), items=[{'source_item_id': 1, 'quantity': quantity}])


def test_repeated_source_item_rejected():
    with pytest.raises(ValidationError):
        SampleFromSource(inquiry_id=1, request_key=uuid4(), items=[{'source_item_id': 1}, {'source_item_id': 1}])


def test_serialization_keeps_source_and_sample_quantities_and_specs_separate():
    item = SampleItem(id=1, sample_request_id=2, item_name='原名称', original_quantity=Decimal(10000),
                      quantity=Decimal(2), specification='本次规格', remark='本次备注',
                      source_snapshot={'name': '原名称', 'sku_code': 'ORIGINAL', 'original_quantity': '10000',
                                       'specification': '原规格', 'remark': '原备注'})
    result = serialize_item(item)
    assert result['original_quantity'] == 10000 and result['quantity'] == 2
    assert result['source_snapshot']['original_quantity'] == '10000'
    assert result['differences'] == {'quantity_changed': True, 'specification_changed': True, 'remark_changed': True}
    assert result['specification'] == '本次规格'
