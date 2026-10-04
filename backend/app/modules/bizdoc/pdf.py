"""打样需求单 / 下单文件的 PDF 渲染（文档 §四「对外模板及生成文件」）。

与报价单、合同同一套中文字体方案（reportlab STSong-Light，不依赖系统字体）。
正文只读 `input_snapshot`（由 service 组装），**不实时回查业务表**：
否则客户资料一改，已经发给工厂的那份文件正文就跟着变了。
"""

from io import BytesIO
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

CN_FONT = "STSong-Light"


def _register_font() -> None:
    try:
        pdfmetrics.getFont(CN_FONT)
    except KeyError:
        pdfmetrics.registerFont(UnicodeCIDFont(CN_FONT))


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "DocTitle",
            parent=base["Title"],
            fontName=CN_FONT,
            fontSize=18,
            leading=24,
            spaceAfter=6,
        ),
        "meta": ParagraphStyle(
            "DocMeta", parent=base["Normal"], fontName=CN_FONT, fontSize=9, leading=13
        ),
        "section": ParagraphStyle(
            "DocSection",
            parent=base["Normal"],
            fontName=CN_FONT,
            fontSize=10.5,
            leading=16,
            spaceBefore=10,
            spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "DocBody", parent=base["Normal"], fontName=CN_FONT, fontSize=10, leading=16
        ),
        "footer": ParagraphStyle(
            "DocFooter",
            parent=base["Normal"],
            fontName=CN_FONT,
            fontSize=7.5,
            leading=11,
            textColor=colors.HexColor("#888888"),
        ),
    }


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _esc(value: Any) -> str:
    """进 Paragraph 前必须转义：reportlab 按 XML 解析内容，

    公司名/客户名/正文里的 `<` 或未转义的 `&` 会让渲染抛异常（下载接口 500）。
    注意：Table 的字符串单元格按纯文本绘制、不走 XML 解析，所以只转 Paragraph。
    """
    return escape(_text(value))


def _items_table(rows: list[list[str]], headers: list[str], widths: list[float]) -> Table:
    table = Table([headers, *rows], colWidths=widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), CN_FONT),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F0F2F5")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#333333")),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D9DDE3")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def render_biz_doc_pdf(data: dict[str, Any]) -> bytes:
    """把一份对外单据渲染成 PDF 字节流。

    data 由 bizdoc.service 组装（单据头 + 来源 + 客户 + 明细 + 差异 + 条款 + 校验值）。
    """
    _register_font()
    style = _styles()
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=_text(data.get("title")),
    )

    flow: list[Any] = []
    company = _text(data.get("company_name")) or "本公司"
    flow.append(Paragraph(_esc(company), style["meta"]))
    flow.append(Paragraph(_esc(data.get("title")), style["title"]))

    meta_rows = [
        ["单据编号", _text(data.get("doc_no")), "单据版本", f"V{_text(data.get('version'))}"],
        ["生成日期", _text(data.get("created_date")), "单据状态", _text(data.get("status_label"))],
        ["客户", _text(data.get("customer_name")) or "-", "联系人", _text(data.get("contact_name")) or "-"],
    ]
    source = data.get("source") or {}
    if source.get("no"):
        meta_rows.append(
            [
                "来源单据",
                f"{_text(source.get('label'))} {_text(source.get('no'))}"
                + (f"（第 {source['version']} 版）" if source.get("version") else ""),
                "模板版本",
                f"V{_text(data.get('template_version'))}",
            ]
        )
    flow.append(Spacer(1, 4))
    flow.append(
        _items_table(
            meta_rows,
            ["项目", "内容", "项目", "内容"],
            [24 * mm, 55 * mm, 24 * mm, 55 * mm],
        )
    )

    items = data.get("items") or []
    flow.append(Paragraph("明细", style["section"]))
    if items:
        flow.append(
            _items_table(
                [
                    [
                        _text(row.get("name")),
                        _text(row.get("spec")),
                        _text(row.get("quantity")),
                        _text(row.get("unit")),
                        _text(row.get("remark")),
                    ]
                    for row in items
                ],
                ["产品 / 需求", "规格", "数量", "单位", "备注"],
                [50 * mm, 38 * mm, 20 * mm, 15 * mm, 35 * mm],
            )
        )
    else:
        flow.append(Paragraph("（无明细）", style["body"]))

    diffs = data.get("diffs") or []
    if diffs:
        flow.append(Paragraph("与来源单据的差异", style["section"]))
        flow.append(
            _items_table(
                [
                    [
                        _text(row.get("item")),
                        _text(row.get("field")),
                        _text(row.get("before")) or "—",
                        _text(row.get("after")) or "—",
                    ]
                    for row in diffs
                ],
                ["明细行", "字段", "来源值", "本次值"],
                [50 * mm, 28 * mm, 40 * mm, 40 * mm],
            )
        )

    for section in data.get("sections") or []:
        if not _text(section.get("value")).strip():
            continue
        flow.append(Paragraph(_text(section.get("label")), style["section"]))
        flow.append(Paragraph(_esc(section.get("value")), style["body"]))

    body = _text(data.get("body")).strip()
    if body:
        flow.append(Paragraph("说明", style["section"]))
        for line in body.splitlines():
            if line.strip():
                flow.append(Paragraph(_esc(line.strip()), style["body"]))

    flow.append(Spacer(1, 10))
    flow.append(
        Paragraph(
            f"内容校验值（SHA-256，按本文的快照与模板版本计算）："
            f"{_text(data.get('content_sha256'))}",
            style["footer"],
        )
    )
    flow.append(
        Paragraph(
            "本文件由系统按生成时的快照出图；之后改动业务资料不会改变本文内容，"
            "需要更新请重新生成一份（旧文件保留）。",
            style["footer"],
        )
    )

    doc.build(flow)
    return buffer.getvalue()
