"""合同/月结协议 PDF 生成（文档 §3.6：模板生成、下载、签后归档）。

与报价单同一套中文字体方案（reportlab STSong-Light，不依赖系统字体）。
正文取 `content_snapshot` 快照——客户资料之后改了，已生成的合同原文不变。
"""

from io import BytesIO
from typing import Any

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.lib import colors

CN_FONT = "STSong-Light"


def _register_font() -> None:
    try:
        pdfmetrics.getFont(CN_FONT)
    except KeyError:
        pdfmetrics.registerFont(UnicodeCIDFont(CN_FONT))


def render_contract_pdf(data: dict[str, Any]) -> bytes:
    """把合同文档渲染成 PDF 字节流。

    data 由 contract.router 组装：单据头 + 正文快照 + 签署信息。
    """
    _register_font()
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"{data.get('doc_type_label', '合同')} {data.get('doc_no', '')}",
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "CNTitle", parent=styles["Title"], fontName=CN_FONT, fontSize=18, leading=26
    )
    normal = ParagraphStyle(
        "CNNormal", parent=styles["Normal"], fontName=CN_FONT, fontSize=10, leading=17
    )
    head = ParagraphStyle("CNHead", parent=normal, fontSize=10, leading=16)
    small = ParagraphStyle("CNSmall", parent=normal, fontSize=8.5, leading=13)

    story: list[Any] = []
    if data.get("company_name"):
        story.append(
            Paragraph(
                str(data["company_name"]),
                ParagraphStyle("CNCompany", parent=normal, fontSize=11, leading=16, alignment=1),
            )
        )
        story.append(Spacer(1, 4 * mm))

    story.append(Paragraph(str(data.get("doc_type_label") or "合同"), title_style))
    story.append(Spacer(1, 5 * mm))

    meta_rows = [
        [
            Paragraph(f"编号：{data.get('doc_no', '-')}", head),
            Paragraph(f"客户：{data.get('customer_name') or '-'}", head),
        ],
        [
            Paragraph(f"生成日期：{data.get('created_date') or '-'}", head),
            Paragraph(
                f"关联订单：{data.get('order_no') or '-'}"
                f"　关联报价：{data.get('quote_no') or '-'}",
                head,
            ),
        ],
    ]
    if data.get("expiry_date"):
        meta_rows.append(
            [Paragraph(f"到期日：{data['expiry_date']}", head), Paragraph("", head)]
        )
    table = Table(meta_rows, colWidths=[85 * mm, 85 * mm])
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#DDDDDD")),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 6 * mm))

    # 正文：快照按行渲染（保留换行；长行交给 reportlab 自动折行）
    for line in str(data.get("content_snapshot") or "").splitlines():
        text = line.strip()
        if text:
            story.append(Paragraph(text.replace(" ", "&nbsp;"), normal))
        else:
            story.append(Spacer(1, 3 * mm))

    story.append(Spacer(1, 14 * mm))
    sign_rows = [
        [Paragraph("甲方（客户）：", normal), Paragraph("乙方（供方）：", normal)],
        [Paragraph("签署日期：　　年　　月　　日", small), Paragraph("签署日期：　　年　　月　　日", small)],
        [Paragraph("（盖章）", small), Paragraph("（盖章）", small)],
    ]
    sign_table = Table(sign_rows, colWidths=[85 * mm, 85 * mm], rowHeights=[16 * mm, 10 * mm, 12 * mm])
    sign_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(sign_table)

    doc.build(story)
    return buffer.getvalue()
