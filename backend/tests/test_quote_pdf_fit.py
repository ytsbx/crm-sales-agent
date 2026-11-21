"""实际渲染后的数字单元格必须放得下，普通报价维持原字号。"""
from decimal import Decimal as D

import pytest
from reportlab.pdfbase import pdfmetrics

from app.modules.quote import pdf


@pytest.mark.parametrize('kind', ['goods', 'fee', 'quantity', 'normal'])
def test_pdf_numeric_cells_fit_without_changing_content(monkeypatch, kind):
    quantity = D('9999999999999.989') if kind == 'quantity' else D('100') if kind == 'goods' else D('1000') if kind == 'normal' else D('1')
    price = D('999999999999.9999') if kind == 'goods' else D('33.3333') if kind == 'normal' else D('100') if kind == 'fee' else D('1')
    goods = (quantity * price).quantize(D('0.01'))
    fee = D('99999999999899.99') if kind == 'fee' else D('0')
    data = {'items': [{'quantity': quantity, 'quoted_price': price, 'amount': goods}],
            'charges': [{'amount': fee}], 'subtotal_amount': goods, 'total_amount': goods + fee,
            'discount_amount': D('0'), 'summary': {'logistics_amount': fee, 'other_charge_amount': D('0')}}
    tables = []
    original = pdf.Table

    def capture(rows, *args, **kwargs):
        table = original(rows, *args, **kwargs)
        tables.append((table, rows, kwargs.get('colWidths')))
        return table

    monkeypatch.setattr(pdf, 'Table', capture)
    assert pdf.render_quote_pdf(data).startswith(b'%PDF')
    checked = 0
    for table, rows, widths in tables:
        if rows[0][0] == '序号':
            columns, start, baseline = [3, 4, 5], 1, 9
            assert rows[1][5] == f'{goods:.2f}'
            assert rows[1][4] == pdf.format_unit_price(price)
        elif rows[0][0] == '类型':
            columns, start, baseline = [2], 1, 9
        elif rows[0][0] == '商品货款':
            columns, start, baseline = [1], 0, 10
        else:
            continue
        for row in range(start, len(rows)):
            for column in columns:
                text = rows[row][column]
                if not text:
                    continue
                style = table._cellStyles[row][column]
                measured = pdfmetrics.stringWidth(text, style.fontname, style.fontsize)
                available = widths[column] - style.leftPadding - style.rightPadding
                assert measured <= available + 1e-6, (kind, text, measured, available)
                assert style.fontsize <= baseline
                if kind == 'normal':
                    assert style.fontsize == baseline
                checked += 1
    assert checked >= 8
