"""对客报价单 Excel 出图（文档 §3.5 / 场景 10）。

与 PDF 出图（打样单、下单文件）共用同一套快照：**金额一律取自快照**，
不在这里现算、也不回查当前价格规则——否则"Excel 与对应报价版本金额一致"
这条根本保证不了（客户手里的表会随价格维护悄悄变）。

第八批 §8.7 追加：币种、计价单位、有效期、付款/交付/税运条款、客户与联系人抬头
都必须**明确印在表上**（数量与单位同列组），且这些值只认按报价版本冻结的那一份
（`data["frozen"]`）。历史文件没有冻结块时印"未留存/待核实"，
**不回查当前客户或 SKU 资料**——把今天的资料写成当时发出去的事实，
客户拿两张表一对就能发现口径不一致。

模板先用行业通用格式（抬头/客户/单号与版本/明细/合计/有效期/说明），
业务给了正式样张后新增一版模板即可，历史文件仍指向它们当时用的那一版。
"""

from io import BytesIO
from decimal import Decimal, InvalidOperation
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from app.core.money import format_unit_price

THIN = Side(style="thin", color="D9DDE3")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HEAD_FILL = PatternFill("solid", fgColor="F0F2F5")

#: 出图程序版本（第八批 §8.10）。改渲染逻辑时**必须**动这个字符串：
#: 它落进 `biz_docs.renderer_version`。存档的原件是**字节**，升级渲染器不会
#: 改动已生成的那一份，这一列让"同一个编号为什么前后两次出图不同"可解释。
RENDERER_VERSION = "bizdoc-xlsx/1"

#: 历史值未留存时的展示文案。刻意用函数而不是散落的字面量：
#: 这几句话是要给客户看的，措辞必须统一。
NOT_RETAINED = "未留存（生成时未记录，待核实）"
UNIT_PENDING = "待核实"

#: 明细表列组：组名 + 列（标签 + 宽度 + 取值键）。
#: **数量与单位同列组**（§8.7 的明确要求）：客户报"300 套"时必须能一眼看到
#: 数字与单位是一起的，而不是"300"孤零零一格、"套"在别处。
ITEM_COLUMN_GROUPS: list[tuple[str, list[tuple[str, int, str]]]] = [
    ("产品", [("产品 / 需求", 32, "name")]),
    ("规格", [("规格", 22, "spec")]),
    ("数量", [("数量", 10, "quantity"), ("单位", 8, "unit")]),
    ("价格", [("单价", 14, "unit_price"), ("金额", 14, "amount")]),
    ("备注", [("备注", 24, "remark")]),
]


def _flat_columns() -> list[tuple[str, int, str]]:
    return [column for _group, columns in ITEM_COLUMN_GROUPS for column in columns]


def _text(value: Any) -> str:
    """任何值 → 单元格文本。None 变空串，其余 `str()`。

    只做展示转换，**不负责数据类型**：写进单元格要一律走 `_put_text`，
    否则以 `=` 开头的用户文本会被 openpyxl 当成公式。
    """
    if value is None:
        return ""
    return str(value)


def _put_text(sheet, *, row: int | None = None, column: int | None = None, ref: str | None = None, value: Any):
    """把**用户可见文本**写进单元格，并强制按字符串类型保存。

    为什么必须强制（第二轮审查 P2，2026-10-12）：openpyxl 对以 `=` 开头的字符串
    会写 `t="f"`（公式），Excel 打开时真的会去求值；客户名、公司名、产品文本、
    规格、备注、模板正文这些都是**外部输入**，一旦有人填 `=1+1`，
    表格里就出现一个公式；更复杂的表达式还可能变成外部引用（`=HYPERLINK`）。

    这里不做"加个前导单引号"那种改内容的做法 —— 那会让单元格里显示出一个引号。
    直接把 `data_type` 定为 `s`（实测回读 `t="s"`、值原样），
    金额与数量仍按数值/文本各自的口径走（见 `_cell_text`）。
    """
    text = _text(value)
    if ref is not None:
        cell = sheet[ref]
        cell.value = text
    else:
        cell = sheet.cell(row=row, column=column, value=text)
    cell.data_type = "s"
    return cell


def _frozen(data: dict[str, Any]) -> dict[str, Any]:
    """按报价版本冻结的抬头/条款/明细单位；历史文件没有这一块。"""
    return data.get("frozen") or {}


def _frozen_item_units(data: dict[str, Any]) -> list[str]:
    frozen = _frozen(data)
    rows = frozen.get("items") or []
    return [_text(row.get("unit")) for row in rows]


def _cell_text(key: str, value: Any) -> str:
    """明细单元格的展示口径。

    - **金额**保留两位（对客表格要能直接加）；
    - **单价**保留实际成交精度、末尾不补零（第二轮审查 P2，2026-10-12）：
      单价截成两位会让"单价 × 数量"对不上金额那一栏（33.3333 × 1000 印成
      33.33 × 1000 = 33330.00，而金额是 33333.30）。格式器只有
      `core.money.format_unit_price` 一份；
    - **数量**不做四舍五入：`Numeric(16,3)` 的 12.5 必须印成 12.5，
      印成 12.50 或 13 都会让客户拿计算器复核时对不上。
    """
    if value is None:
        return ""
    if key == "amount":
        try:
            return f"{Decimal(str(value)):,.2f}"
        except (InvalidOperation, TypeError, ValueError):
            return _text(value)
    if key == "unit_price":
        return format_unit_price(value)
    return _text(value)


def _gap_display(data: dict[str, Any], field: str) -> str | None:
    for gap in _frozen(data).get("gaps") or []:
        if gap.get("field") == field:
            return _text(gap.get("display")) or NOT_RETAINED
    return None


def _terms(data: dict[str, Any]) -> dict[str, Any]:
    """表外条款区：币种/有效期/付款/交付/贸易/客户/联系人，一律取冻结值。"""
    frozen = _frozen(data)
    terms = frozen.get("terms") or {}
    header = frozen.get("header") or {}
    available = bool(frozen)  # 有冻结块才算"这些值当时记录过"

    def _value(field: str, raw: Any) -> str:
        text = _text(raw).strip()
        if text:
            return text
        if not available:
            # 历史文件（早于字段冻结）不得拿当前资料补，明说没留存
            return NOT_RETAINED
        # 冻结块在、但这一栏确实空着：区分"可选字段允许留白"与"未留存"
        return _gap_display(data, field) or "—"

    rows = [
        ("币种", _value("currency", terms.get("currency"))),
        ("有效期至", _value("valid_until", terms.get("valid_until"))),
        ("付款条件", _value("payment_terms", terms.get("payment_terms"))),
        ("交付条件", _value("delivery_terms", terms.get("delivery_terms"))),
    ]
    # 贸易条款内贸为空是正常的，不印一行"未留存"吓人；有值才展示（外贸 FOB/CIF）
    if _text(terms.get("trade_terms")).strip():
        rows.append(("贸易条款", _text(terms.get("trade_terms"))))
    rows.append(("客户", _value("customer_name", header.get("customer_name"))))
    rows.append(("联系人", _value("contact_name", header.get("contact_name"))))
    return {"rows": rows, "frozen": available}


def render_quote_xlsx(data: dict[str, Any]) -> bytes:
    """把一份对客报价单渲染成 xlsx 字节流。data 由 bizdoc.service 组装。"""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "报价单"

    columns = _flat_columns()
    for idx, (_label, width, _key) in enumerate(columns, start=1):
        sheet.column_dimensions[get_column_letter(idx)].width = width

    bold = Font(bold=True)
    title_font = Font(bold=True, size=15)

    # 用户可见文本一律走 `_put_text`（强制字符串）：公司名是配置项、标题来自模板，
    # 都可能以 `=` 开头而被 Excel 当公式执行
    _put_text(sheet, ref="A1", value=_text(data.get("company_name")) or "本公司")
    _put_text(sheet, ref="A2", value=_text(data.get("title")))
    sheet["A2"].font = title_font
    # 元信息落在明细表上方，客户一眼能看到单号与版本——对账全靠这两个
    meta = [
        ("单据编号", _text(data.get("doc_no")), "单据版本", f"V{_text(data.get('version'))}"),
        ("客户", _text(data.get("customer_name")), "生成日期", _text(data.get("created_date"))),
        # 联系人此前不出现在对客 Excel 上：抬头少了人，客户不知道该找谁核价
        ("联系人", _text(data.get("contact_name")) or "未留存", "业务负责人", _text(data.get("owner_name")) or "未留存"),
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
        _put_text(sheet, row=row, column=1, value=left_label).font = bold
        _put_text(sheet, row=row, column=2, value=left_value)
        _put_text(sheet, row=row, column=3, value=right_label).font = bold
        _put_text(sheet, row=row, column=4, value=right_value)
        row += 1

    # 草稿（模板变量未解析）：纸面上就写明它不是正式对外文件，
    # 免得被当成已发出的报价单继续用
    if data.get("is_draft"):
        issues = data.get("token_issues") or []
        _put_text(sheet, row=row, column=1, value="草稿（非正式对外文件）").font = Font(
            bold=True, color="C0392B"
        )
        # ⚠️ 优先印**快照里记下的真实原因**（2026-10-14 审查修）：草稿有两条来源
        # （模板变量未解析 / 调用方标的原因：报价未过审批、主数据未确认、运费未确认），
        # 而这里原来写死"模板变量未解析"，情形②时与事实不符，事后也看不出为什么是草稿。
        issues_text = "；".join(_text(i.get("message") or i.get("token")) for i in issues)
        reason = _text(data.get("draft_reason")).strip()
        if issues_text:
            reason = (
                f"{reason}（模板变量未解析：{issues_text}）"
                if reason
                else f"模板变量未解析：{issues_text}"
            )
        detail = (reason or "原因见生成记录") + "；本表仅供内部核对"
        _put_text(sheet, row=row, column=2, value=detail)
        row += 1

    # 副本标记（第八批 §8.10）：这一份是现在按快照重出的，不是生成时存档的字节。
    # 不写明白，"重建副本 / 状态副本"就会被当成当初发出去的原件。
    if data.get("copy_notice"):
        _put_text(
            sheet, row=row, column=1, value=f"【{data['copy_notice']}】"
        ).font = Font(bold=True, color="C0392B")
        row += 1

    # 条款区放在明细上方：币种、有效期、付款/交付条件是客户先看的东西
    terms = _terms(data)
    row += 1
    _put_text(sheet, row=row, column=1, value="币种与条款").font = bold
    row += 1
    for label, value in terms["rows"]:
        _put_text(sheet, row=row, column=1, value=label).font = bold
        _put_text(sheet, row=row, column=2, value=value)
        row += 1

    header_row = row + 1
    column_index = 1
    for group, group_columns in ITEM_COLUMN_GROUPS:
        if len(group_columns) > 1:
            _put_text(sheet, row=header_row - 1, column=column_index, value=group).font = bold
        for label, _width, _key in group_columns:
            cell = sheet.cell(row=header_row, column=column_index, value=label)
            cell.font = bold
            cell.fill = HEAD_FILL
            cell.border = BORDER
            cell.alignment = Alignment(horizontal="center")
            column_index += 1

    # 金额列（单价/金额）从"价格"组开始，供下面的金额区对齐使用
    money_column = next(
        idx
        for idx, (_label, _width, key) in enumerate(columns, start=1)
        if key == "amount"
    )
    units = _frozen_item_units(data)
    items = data.get("items") or []
    total_row = header_row + 1
    for offset, item in enumerate(items, start=1):
        for idx, (_label, _width, key) in enumerate(columns, start=1):
            value = item.get(key)
            if key == "unit":
                # 单位只认冻结值：快照里没有就印"待核实"，
                # 绝不回查当前 SKU 的 unit（那会把今天的单位写成历史事实）
                value = _text(value) or (
                    units[offset - 1] if offset - 1 < len(units) else UNIT_PENDING
                )
            cell = sheet.cell(
                row=header_row + offset, column=idx, value=_cell_text(key, value)
            )
            # 单价/金额是文本形式的数字，其余（产品名、规格、单位、备注）
            # 都是用户文本 —— 一律钉成字符串，防止 `=` 开头被当成公式
            if key not in ("unit_price", "amount"):
                cell.data_type = "s"
            cell.border = BORDER
        total_row = header_row + offset
    if not items:
        _put_text(sheet, row=header_row + 1, column=1, value="（无明细）")
        total_row = header_row + 1

    # 金额区：小计 → 各项附加费 → 优惠 → 合计，逐行列全。
    # 此前表里只有"明细 + 合计"，而合计里含运费/折扣，客户按计算器加明细永远对不上。
    # 2026-10-04 口径：运费与折扣给客户看，所以把这几行补出来。金额一律取快照，
    # 不在这里对明细求和——两处各自算会出现"表内合计与报价版本对不上"的经典扯皮。
    def _money(value: Any) -> str:
        return "" if value is None else f"{Decimal(str(value)):,.2f}"

    summary_row = total_row + 2
    _put_text(sheet, row=summary_row, column=1, value="小计").font = bold
    sheet.cell(row=summary_row, column=money_column, value=_money(data.get("subtotal_amount")))
    # 产品单价不含运费（2026-10-09 运费分离）：这句话要紧挨着"小计"印出来，
    # 客户才会知道上面那一列单价里**没有**含运费、下面的运费是另算的。
    # 历史版本（`unit_price_note` 为空）不印，保持老文件原样。
    if _text(data.get("unit_price_note")).strip():
        _put_text(sheet, row=summary_row, column=2, value=data.get("unit_price_note"))
    summary_row += 1
    for charge in data.get("charges") or []:
        # 费用名称是业务员手写的（"宁波到苏州运费"这种），一律按字符串写
        _put_text(sheet, row=summary_row, column=1, value=charge.get("label"))
        sheet.cell(row=summary_row, column=money_column, value=_money(charge.get("amount")))
        summary_row += 1
    _put_text(sheet, row=summary_row, column=1, value="合计").font = bold
    total_cell = sheet.cell(
        row=summary_row, column=money_column, value=_money(data.get("total_amount"))
    )
    total_cell.font = bold

    row = summary_row + 1
    # ⚠️ 这一段**不再渲染**（2026-10-14 审查修）：`data["sections"]` 的内容
    # 与上面 `_terms(data)` 那一段**完全同源**（都取 `frozen["terms"]` 与
    # `frozen["header"]`）—— 实测渲染出来 A10:B16 与 A24:B30 逐行重复
    # （币种 / 有效期至 / 付款条件 / 交付条件 / 贸易条款 / 客户 / 联系人各印两遍，
    # 第 5-6 行还有第三次客户与联系人）。客户拿到的报价表上同一组条款出现两次。
    # `sections` 是 `frozen` 出现之前的旧渲染路径残留；术语区（"币种与条款"）
    # 在明细上方，位置也更合理。
    # 快照里仍然保留 `sections`（PDF 版式用它，且历史件要能重现）。
    body = _text(data.get("body")).strip()
    if body:
        _put_text(sheet, row=row + 1, column=1, value="说明").font = bold
        for line in body.splitlines():
            if line.strip():
                row += 1
                _put_text(sheet, row=row, column=1, value=line.strip())

    if data.get("content_sha256"):
        _put_text(
            sheet,
            row=row + 2,
            column=1,
            value=f"内容校验值（SHA-256）：{data['content_sha256']}",
        ).font = Font(size=8, color="888888")

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
