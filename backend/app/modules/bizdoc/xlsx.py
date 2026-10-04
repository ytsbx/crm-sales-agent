"""对客报价单 Excel 出图（文档 §3.5 / 场景 10）。

与 PDF 出图（打样单、下单文件）共用同一套快照：**金额一律取自快照**，
不在这里现算、也不回查当前价格规则——否则"Excel 与对应报价版本金额一致"
这条根本保证不了（客户手里的表会随价格维护悄悄变）。

模板先用行业通用格式（抬头/客户/单号与版本/明细/合计/有效期/说明），
业务给了正式样张后新增一版模板即可，历史文件仍指向它们当时用的那一版。
"""

from io import BytesIO
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

THIN = Side(style="thin", color="D9DDE3")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HEAD_FILL = PatternFill("solid", fgColor="F0F2F5")

#: 明细表列：标签 + 宽度 + 取值键（键在快照的 items 里）
ITEM_COLUMNS: list[tuple[str, int, str]] = [
    ("产品 / 需求", 32, "name"),
    ("规格", 22, "spec"),
    ("数量", 10, "quantity"),
    ("单位", 8, "unit"),
    ("单价", 14, "unit_price"),
    ("金额", 14, "amount"),
    ("备注", 24, "remark"),
]


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def render_quote_xlsx(data: dict[str, Any]) -> bytes:
    """把一份对客报价单渲染成 xlsx 字节流。data 由 bizdoc.service 组装。"""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "报价单"

    for idx, (_label, width, _key) in enumerate(ITEM_COLUMNS, start=1):
        sheet.column_dimensions[get_column_letter(idx)].width = width

    bold = Font(bold=True)
    title_font = Font(bold=True, size=15)

    sheet["A1"] = _text(data.get("company_name")) or "本公司"
    sheet["A2"] = _text(data.get("title"))
    sheet["A2"].font = title_font
    # 元信息落在明细表上方，客户一眼能看到单号与版本——对账全靠这两个
    meta = [
        ("单据编号", _text(data.get("doc_no")), "单据版本", f"V{_text(data.get('version'))}"),
        ("客户", _text(data.get("customer_name")), "生成日期", _text(data.get("created_date"))),
        # 状态必须进对客 Excel：作废的报价单如果和有效的长得一样，客户拿着它
        # 继续下单就是事故。打样/下单 PDF 早就带状态，这里此前漏了。
        ("单据状态", _text(data.get("status_label")), "", ""),
    ]
    source = data.get("source") or {}
    if source.get("no"):
        meta.append(
            (
                "来源单据",
                f"{_text(source.get('label'))} {_text(source.get('no'))}",
                "模板版本",
                f"V{_text(data.get('template_version'))}",
            )
        )
    row = 4
    for left_label, left_value, right_label, right_value in meta:
        sheet.cell(row=row, column=1, value=left_label).font = bold
        sheet.cell(row=row, column=2, value=left_value)
        sheet.cell(row=row, column=3, value=right_label).font = bold
        sheet.cell(row=row, column=4, value=right_value)
        row += 1

    header_row = row + 1
    for idx, (label, _width, _key) in enumerate(ITEM_COLUMNS, start=1):
        cell = sheet.cell(row=header_row, column=idx, value=label)
        cell.font = bold
        cell.fill = HEAD_FILL
        cell.border = BORDER
        cell.alignment = Alignment(horizontal="center")

    items = data.get("items") or []
    for offset, item in enumerate(items, start=1):
        for idx, (_label, _width, key) in enumerate(ITEM_COLUMNS, start=1):
            cell = sheet.cell(row=header_row + offset, column=idx, value=_text(item.get(key)))
            cell.border = BORDER
        total_row = header_row + offset
    if not items:
        sheet.cell(row=header_row + 1, column=1, value="（无明细）")
        total_row = header_row + 1

    # 金额区：小计 → 各项附加费 → 优惠 → 合计，逐行列全。
    # 此前表里只有"明细 + 合计"，而合计里含运费/折扣，客户按计算器加明细永远对不上。
    # 2026-10-04 口径：运费与折扣给客户看，所以把这几行补出来。金额一律取快照，
    # 不在这里对明细求和——两处各自算会出现"表内合计与报价版本对不上"的经典扯皮。
    def _money(value: Any) -> str:
        return "" if value is None else f"{float(value):,.2f}"

    summary_row = total_row + 2
    sheet.cell(row=summary_row, column=1, value="小计").font = bold
    sheet.cell(row=summary_row, column=6, value=_money(data.get("subtotal_amount")))
    summary_row += 1
    for charge in data.get("charges") or []:
        sheet.cell(row=summary_row, column=1, value=_text(charge.get("label")))
        sheet.cell(row=summary_row, column=6, value=_money(charge.get("amount")))
        summary_row += 1
    sheet.cell(row=summary_row, column=1, value="合计").font = bold
    total_cell = sheet.cell(
        row=summary_row, column=6, value=_money(data.get("total_amount"))
    )
    total_cell.font = bold

    row = summary_row + 1
    for section in data.get("sections") or []:
        if not _text(section.get("value")).strip():
            continue
        sheet.cell(row=row, column=1, value=_text(section.get("label"))).font = bold
        sheet.cell(row=row, column=2, value=_text(section.get("value")))
        row += 1

    body = _text(data.get("body")).strip()
    if body:
        sheet.cell(row=row + 1, column=1, value="说明").font = bold
        for line in body.splitlines():
            if line.strip():
                row += 1
                sheet.cell(row=row, column=1, value=line.strip())

    if data.get("content_sha256"):
        sheet.cell(
            row=row + 2,
            column=1,
            value=f"内容校验值（SHA-256）：{data['content_sha256']}",
        ).font = Font(size=8, color="888888")

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
