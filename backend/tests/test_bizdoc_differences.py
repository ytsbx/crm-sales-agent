"""下单文件按来源已知字段比较，复制未修改不能误报差异。"""
import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from app.modules.bizdoc.service import _diff_lines, build_order_sheet_doc
from app.modules.customer.model import Customer
from app.modules.order.model import SalesOrder, SalesOrderItem
from app.modules.quote.model import Quote, QuoteItem, QuoteVersion


def test_unknown_source_specification_is_not_reported_as_changed():
    current = [{'inquiry_id': 1, 'name': '测试', 'quantity': 2, 'spec': '现有规格', 'remark': '现有备注'}]
    source = [{'inquiry_id': 1, 'name': '测试', 'quantity': Decimal('2.000')}]
    assert _diff_lines(current, source, 'inquiry_id') == []


def test_clearing_known_specification_and_remark_is_a_real_difference():
    current = [{'inquiry_id': 1, 'name': '测试', 'quantity': 2, 'spec': '', 'remark': None}]
    source = [{'inquiry_id': 1, 'name': '测试', 'quantity': 2, 'spec': '原规格', 'remark': '原备注'}]
    result = _diff_lines(current, source, 'inquiry_id')
    assert [(row['field'], row['before']) for row in result] == [('规格', '原规格'), ('备注', '原备注')]


def test_order_document_reads_source_specification_remark_and_code_name():
    order = SalesOrder(id=1, customer_id=2, quote_id=3, quote_version_id=4)
    version = QuoteVersion(id=4, quote_id=3, version_no=1)
    quote = Quote(id=3, customer_id=2, quote_no='虚构报价')
    row = SalesOrderItem(sku_id=5, sku_snapshot='原编码', specification='原规格', quantity=Decimal(10), remark='原备注')
    origin = QuoteItem(sku_id=5, sku_code_snapshot='原编码', spec_snapshot='原规格', quantity=Decimal('10.000'), remark='原备注')
    records = {(SalesOrder, 1): order, (Quote, 3): quote, (QuoteVersion, 4): version,
               (Customer, 2): Customer(name='虚构客户')}
    session = SimpleNamespace(get=AsyncMock(side_effect=lambda model, ident: records[(model, ident)]),
        execute=AsyncMock(side_effect=[Mock(scalars=Mock(return_value=Mock(all=Mock(return_value=[row])))),
                                      Mock(scalars=Mock(return_value=Mock(all=Mock(return_value=[origin]))))]))
    built = asyncio.run(build_order_sheet_doc(session, 1))
    assert built['diffs'] == []
    row.specification = '本次规格'
    session.execute.reset_mock(side_effect=True)
    session.execute.side_effect = [Mock(scalars=Mock(return_value=Mock(all=Mock(return_value=[row])))),
                                   Mock(scalars=Mock(return_value=Mock(all=Mock(return_value=[origin]))))]
    built = asyncio.run(build_order_sheet_doc(session, 1))
    assert [(diff['field'], diff['before'], diff['after']) for diff in built['diffs']] == [('规格', '原规格', '本次规格')]
