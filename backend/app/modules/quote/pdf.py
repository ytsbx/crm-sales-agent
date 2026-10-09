"""报价单 PDF 生成。

用 reportlab 自带的 STSong-Light 中文字体，不依赖系统字体文件，
所以换一台机器也不会出现「方块字」。
"""

from io import BytesIO
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

CN_FONT = "STSong-Light"


def _register_font() -> None:
    try:
        pdfmetrics.getFont(CN_FONT)
    except KeyError:
        pdfmetrics.registerFont(UnicodeCIDFont(CN_FONT))


def render_quote_pdf(data: dict[str, Any]) -> bytes:
    """把报价单渲染成 PDF 字节流。

    data 结构由 quote.router 组装，包含报价单头、明细、附加费用与合计。
    """
    _register_font()
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"报价单 {data.get('quote_no', '')}",
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "CNTitle", parent=styles["Title"], fontName=CN_FONT, fontSize=18, leading=24
    )
    normal = ParagraphStyle("CNNormal", parent=styles["Normal"], fontName=CN_FONT, fontSize=9, leading=14)
    head = ParagraphStyle("CNHead", parent=normal, fontSize=10, leading=16)

    story: list[Any] = []
    if data.get("company_name"):
        story.append(
            Paragraph(
                str(data["company_name"]),
                ParagraphStyle(
                    "CNCompany", parent=normal, fontSize=11, leading=16, alignment=1
                ),
            )
        )
        story.append(Spacer(1, 4 * mm))
    story.append(Paragraph("报 价 单", title_style))
    story.append(Spacer(1, 6 * mm))

    meta_rows = [
        [Paragraph(f"报价单号：{data.get('quote_no', '-')}", head),
         Paragraph(f"版本：V{data.get('version_no', 1)}", head)],
        [Paragraph(f"客户：{data.get('customer_name') or '-'}", head),
         Paragraph(f"有效期至：{data.get('valid_until') or '-'}", head)],
        [Paragraph(f"商机：{data.get('opportunity_title') or '-'}", head),
         Paragraph(f"负责人：{data.get('owner_name') or '-'}", head)],
        [Paragraph(f"报价日期：{data.get('quote_date') or '-'}", head),
         Paragraph(f"状态：{data.get('status_label') or '-'}", head)],
    ]
    meta_table = Table(meta_rows, colWidths=[87 * mm, 87 * mm])
    meta_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(meta_table)
    story.append(Spacer(1, 6 * mm))

    header = ["序号", "SKU", "规格", "数量", "单价(元)", "金额(元)"]
    rows = [header]
    for index, item in enumerate(data.get("items", []), start=1):
        rows.append(
            [
                str(index),
                item.get("sku_code") or "-",
                item.get("specification") or "-",
                f"{item.get('quantity', 0):g}",
                f"{item.get('quoted_price', 0):.2f}",
                f"{item.get('amount', 0):.2f}",
            ]
        )
    items_table = Table(rows, colWidths=[12 * mm, 30 * mm, 60 * mm, 22 * mm, 25 * mm, 25 * mm])
    items_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), CN_FONT),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2F4F7")),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D9DCE0")),
                ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story.append(items_table)
    story.append(Spacer(1, 4 * mm))

    charges = data.get("charges", [])
    if charges:
        charge_rows = [["类型", "说明", "金额(元)"]]
        for charge in charges:
            charge_rows.append(
                [
                    charge.get("type_label") or charge.get("charge_type") or "-",
                    charge.get("description") or "-",
                    f"{charge.get('amount', 0):.2f}",
                ]
            )
        charge_table = Table(charge_rows, colWidths=[30 * mm, 114 * mm, 30 * mm])
        charge_table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), CN_FONT),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2F4F7")),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D9DCE0")),
                    ("ALIGN", (2, 1), (2, -1), "RIGHT"),
                ]
            )
        )
        story.append(charge_table)
        story.append(Spacer(1, 4 * mm))

    # 合计区：货款 → 运费 → 其他费用 → 折扣 → 合计（2026-10-09 运费分离）。
    # 运费**单独一列具体金额**，不再混在"附加费用"里让客户自己猜；
    # 同时印一句"产品单价不含运费"，避免客户按旧口径以为运费已包在单价里。
    # 这几行**只是把同一笔钱拆开显示**：`total_amount` 里已经含了费用与折扣，
    # 逐行相加正好等于合计，**不再另加一遍**。
    summary = data.get("summary") or {}
    logistics = summary.get("logistics_amount")
    other_charge = summary.get("other_charge_amount")
    if logistics is None:
        logistics = data.get("logistics_amount")
    if other_charge is None:
        other_charge = data.get("other_charge_amount")
    total_rows = [["商品货款", f"¥{data.get('subtotal_amount', 0):.2f}"]]
    if logistics is not None:
        total_rows.append(["运费（代收代付）", f"¥{float(logistics):.2f}"])
        total_rows.append(["其他费用", f"¥{float(other_charge or 0):.2f}"])
    else:
        # 历史文件没有拆分列：保持原来的"附加费用"一行，不改写老口径
        total_rows.append(["附加费用", f"¥{data.get('charge_amount', 0):.2f}"])
    total_rows.append(["折扣", f"¥{data.get('discount_amount', 0):.2f}"])
    total_rows.append(["合计金额", f"¥{data.get('total_amount', 0):.2f}"])
    unit_price_note = str(data.get("unit_price_note") or "").strip()
    if unit_price_note:
        total_rows.append([unit_price_note, ""])
    total_table = Table(total_rows, colWidths=[134 * mm, 40 * mm])
    total_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), CN_FONT),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("LINEABOVE", (0, 0), (-1, 0), 0.6, colors.HexColor("#D9DCE0")),
                ("LINEBELOW", (0, -1), (-1, -1), 0.8, colors.HexColor("#1F2329")),
                ("TEXTCOLOR", (0, -1), (-1, -1), colors.HexColor("#1F2329")),
                ("FONTNAME", (0, -1), (-1, -1), CN_FONT),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(total_table)
    story.append(Spacer(1, 6 * mm))

    terms = [
        f"付款条件：{data.get('payment_terms') or '双方另行约定'}",
        f"交货条件：{data.get('delivery_terms') or '双方另行约定'}",
        f"报价有效期：至 {data.get('valid_until') or '双方另行约定'}",
    ]
    if data.get("remark"):
        terms.append(f"备注：{data['remark']}")
    for line in terms:
        story.append(Paragraph(line, normal))

    story.append(Spacer(1, 8 * mm))
    story.append(
        Paragraph(
            "本报价单由系统生成，价格依据报价时的成本与价格规则快照，"
            "后续版本以最新报价为准。",
            ParagraphStyle("CNFooter", parent=normal, textColor=colors.HexColor("#8F959E")),
        )
    )

    doc.build(story)
    return buffer.getvalue()
