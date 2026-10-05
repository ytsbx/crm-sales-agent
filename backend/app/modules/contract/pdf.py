"""合同/月结协议 PDF 生成（文档 §3.6：模板生成、下载、签后归档）。

与报价单同一套中文字体方案（reportlab STSong-Light，不依赖系统字体）。
正文取 `content_snapshot` 快照——客户资料之后改了，已生成的合同原文不变。
"""

from io import BytesIO
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.lib import colors

CN_FONT = "STSong-Light"


def _esc(value: Any) -> str:
    """转义后再进 Paragraph。

    reportlab 的 Paragraph 按 XML/HTML 解析内容：公司名、客户名、正文里只要出现
    `<` 或没转义的 `&`（"A&B 公司"、"<加急>"），渲染就抛异常 → 下载接口 500。
    快照里的文本是用户输入，必须转义。
    """
    return escape("" if value is None else str(value))


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
                _esc(data["company_name"]),
                ParagraphStyle("CNCompany", parent=normal, fontSize=11, leading=16, alignment=1),
            )
        )
        story.append(Spacer(1, 4 * mm))

    story.append(Paragraph(_esc(data.get("doc_type_label") or "合同"), title_style))
    story.append(Spacer(1, 5 * mm))

    status_label = _esc(data.get("status_label") or "")
    # 报价号带上版本：合同依据的是哪一版报价必须印在纸上。报价后来改过价时，
    # 只有"Q2026xxx V2"这种写法才能说明这份合同签的是哪一版。
    quote_text = _esc(data.get("quote_no")) or "-"
    if data.get("quote_version_no"):
        quote_text = f"{quote_text} V{_esc(data['quote_version_no'])}"
    meta_rows = [
        [
            Paragraph(f"编号：{_esc(data.get('doc_no', '-'))}", head),
            Paragraph(f"客户：{_esc(data.get('customer_name')) or '-'}", head),
        ],
        [
            Paragraph(f"生成日期：{_esc(data.get('created_date')) or '-'}", head),
            Paragraph(
                f"关联订单：{_esc(data.get('order_no')) or '-'}"
                f"　关联报价：{quote_text}",
                head,
            ),
        ],
    ]
    # 状态必须上纸：作废的合同如果看起来和有效的一模一样，客户/工厂拿着它
    # 继续走流程就是事故。打样/下单 PDF 早就带状态，合同这边此前漏了。
    if status_label:
        meta_rows.append(
            [
                Paragraph(f"单据状态：{status_label}", head),
                Paragraph("", head),
            ]
        )
    if data.get("expiry_date"):
        meta_rows.append(
            [Paragraph(f"到期日：{_esc(data['expiry_date'])}", head), Paragraph("", head)]
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
            story.append(Paragraph(_esc(text).replace(" ", "&nbsp;"), normal))
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
