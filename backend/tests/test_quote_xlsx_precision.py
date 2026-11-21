"""检查项目现有Excel渲染器实际写出的XML文本；不连接业务数据库。"""
from decimal import Decimal
from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest

from app.modules.bizdoc.xlsx import render_quote_xlsx


def cells(data):
    with ZipFile(BytesIO(render_quote_xlsx(data))) as archive:
        root = ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
    ns = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    return [''.join(cell.itertext()) for cell in root.findall('.//m:c', ns)]


@pytest.mark.parametrize('factory', [Decimal, str])
@pytest.mark.parametrize('amount', ['99999999999999.99', '1.01'])
def test_quote_xlsx_item_subtotal_and_total_preserve_cents(factory, amount):
    value = factory(amount)
    data = {'currency': 'CNY', 'items': [{'name': '隔离产品', 'quantity': 1, 'unit_price': 1, 'amount': value}],
            'subtotal_amount': value, 'total_amount': value}
    expected = f'{Decimal(amount):,.2f}'
    assert cells(data).count(expected) == 3


@pytest.mark.parametrize('factory', [Decimal, str])
def test_quote_xlsx_fee_and_total_preserve_cents(factory):
    data = {'currency': 'CNY', 'items': [{'name': '隔离产品', 'quantity': 1, 'unit_price': 100, 'amount': factory('100')}],
            'subtotal_amount': factory('100'), 'charges': [{'label': '运费', 'amount': factory('99999999999899.99')}],
            'total_amount': factory('99999999999999.99')}
    values = cells(data)
    assert '99,999,999,999,899.99' in values
    assert '99,999,999,999,999.99' in values
