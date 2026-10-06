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

#: 出图程序版本（第八批 §8.10）。改渲染逻辑时**必须**动这个字符串：
#: 它落进 `biz_docs.renderer_version`，用来解释"同一份快照为什么两次出图不同"。
#: 旧原件不会因此改变——它存的是字节，不是"再渲染一遍的承诺"。
RENDERER_VERSION = "bizdoc-pdf/1"


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
        # 草稿提示要**看得见**：红色、略大，和正文条款明显区分
        "draft": ParagraphStyle(
            "DocDraft",
            parent=base["Normal"],
            fontName=CN_FONT,
            fontSize=11,
            leading=16,
            textColor=colors.HexColor("#C0392B"),
            spaceBefore=4,
            spaceAfter=4,
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


def _part_spec(row: dict[str, Any]) -> str:
    """明细行的「车间依据」：材质 / 工艺 / 图纸版本，拼成一格。

    为什么拼一格而不是铺三列：明细表本来就有名字/规格/数量/单位/备注，
    再铺三列会把每列挤到放不下中文。而这三项车间总是连起来用
    （用什么料、怎么做、按哪版图），合成一格读起来反而更顺。
    """
    parts = [
        str(value).strip()
        for value in (row.get("material"), row.get("craft"))
        if value and str(value).strip()
    ]
    text = " / ".join(parts)
    version = str(row.get("drawing_version") or "").strip()
    if version:
        text = f"{text}（图纸 {version}）" if text else f"图纸 {version}"
    return text


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

    # 草稿（第八批 §8.8）：模板变量没解析出来时只允许出草稿，纸面上必须写明
    # "这不是正式对外文件"，否则一份缺条款的 PDF 会被当成正式件发出去。
    if data.get("is_draft"):
        issues = data.get("token_issues") or []
        detail = "；".join(
            _text(issue.get("message") or issue.get("token")) for issue in issues
        ) or "模板变量未解析"
        flow.append(
            Paragraph(
                _esc("【草稿】本文件不是正式对外文件：模板变量未解析。" + detail),
                style["draft"],
            )
        )

    # 副本标记（第八批 §8.10）：这一份不是生成时存档的那份字节，而是现在按快照
    # 重出的。**必须在纸面上写明**——否则一份"重建副本"会被当成当初发给客户的原件。
    if data.get("copy_notice"):
        flow.append(Paragraph(_esc(f"【{data['copy_notice']}】"), style["draft"]))

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
        # 「车间依据」（材质/工艺/图纸版本）是打样单才有的，逐行不同。
        # 下单文件与打样单共用这个渲染函数，所以**只有真有值时才加这一列**——
        # 否则会给下单文件白加一列空格子。
        has_part_spec = any(_part_spec(row) for row in items)
        headers = ["产品 / 需求", "规格", "数量", "单位"]
        widths = [42 * mm, 30 * mm, 15 * mm, 12 * mm]
        if has_part_spec:
            headers.append("材质 / 工艺 / 图纸")
            widths.append(36 * mm)
        headers.append("备注")
        widths.append(29 * mm if has_part_spec else 54 * mm)

        rows = []
        for row in items:
            cells = [
                _text(row.get("name")),
                _text(row.get("spec")),
                _text(row.get("quantity")),
                _text(row.get("unit")),
            ]
            if has_part_spec:
                cells.append(_part_spec(row))
            cells.append(_text(row.get("remark")))
            rows.append(cells)
        flow.append(_items_table(rows, headers, widths))
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
    # 按版本冻结时确实没记录的栏位（历史文件）：在纸面上说明"这些不是当时的空白，
    # 是系统没留存"，免得客户以为业务当时就是这么定的
    gaps = (data.get("frozen") or {}).get("gaps") or []
    if gaps:
        flow.append(
            Paragraph(
                "未留存项（生成时未记录，待核实）："
                + "；".join(
                    f"{_text(gap.get('label'))}（{_text(gap.get('display'))}）"
                    for gap in gaps[:8]
                ),
                style["footer"],
            )
        )

    doc.build(flow)
    return buffer.getvalue()
