"""把 CRM 的打样单填进钉钉那份《打样申请单》Excel。

## 为什么需要它

钉钉上的「定制打样审批」用的是**公司现有表单**
（表头印着"表单编码：D01-003"，主人提供的实物文件）。这份单子的格式**不能改**，
而 CRM 原来缺它上面一大半的格 —— 业务员每次都要**手打一遍 Excel** 再去钉钉上传。

这个模块让 CRM **一键生成填好的那份申请单**：业务员下载即传，不用重打。

## 底稿从哪来

`app/modules/sample/templates/打样申请单模板.xlsx`（从钉钉下载的原件，原样入库）。
用底稿而不是"代码画一张"的理由：**出来的一模一样** —— 业务员看着眼熟，
钉钉那边也不会觉得格式变了。原表的版式（合并单元格 26 处、字体、底色、批注）全部保留。

## ⚠️ 两个必须知道的坑（都踩过、都已在代码里处理）

### 1. WPS 生成的 xlsx，openpyxl 读不了

原件是 WPS 存的，`openpyxl.load_workbook()` 直接抛：

    TypeError: Fill() takes no arguments

根因在 `xl/styles.xml` 的 `<fills>` 段里有两个**空标签** `<fill/>`
（index 2、3），openpyxl 无法把空标签解析成 `Fill` 对象。
`_patch_wps_styles()` 在内存里把它们补成合法的
`<fill><patternFill patternType="none"/></fill>` 就能读了 —— **这是甲方案的命门**，
没有这一步就只能退化成"代码重画一张"（版式与原版不完全一样）。

### 2. 用户文本进单元格必须强制成字符串

以 `=` 开头的文本（例如产品名叫 `=1+1`）会被 openpyxl 当成**公式**写进去
（`t="f"`），Excel 打开时真的求值，`=HYPERLINK(...)` 这类还能变成外部引用。
所以一律走 `bizdoc.xlsx._put_text`（它把 `data_type` 定成 `s`）——
**复用那一份实现，不在这里抄第二份**，否则两处的判据迟早走散。

## 填写位图（从实物文件逐格读出，别凭印象改）

申请单是 A1:M21，标签在合并块里、**空位是标签右边的格**：

    C2 打样类型        G2 产品名称        I2 打样数量        K2 交样时间
    C3 打样费用        G3 申请人          I3 打样部门        K3 打样人
    C4 打样目的（勾选）                   G4 紧急程度（勾选）
    C5 样品情况（勾选）
    B8 主要材质和规格   B9 产品尺寸   B10 产品印刷
    B11 主要辅料       B12 包装要求  B13 做法要求

**刻意留空的格**（不是漏了，是"不该由 CRM 填"）：

    B14/B15 采购部门回复内容（采购接收人）—— 批注写着"此处为采购填写"
    B16     打样部回复内容
    C17/E17/G17 打样人 / 完成时间 / 样品确认人 —— 打样部填
    A6 区的产品图片 —— 附件形式，CRM 的文件不往 Excel 里塞

这四类都是**生产部在钉钉里填**的；生产部不在 CRM 里（主人 2026-10-15 口径），
CRM 填了反而是假的。
"""
from __future__ import annotations

import io
import re
import warnings
import zipfile
from decimal import Decimal
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from app.modules.bizdoc.xlsx import _put_text

#: 底稿（从钉钉下载的原件，原样入库）
TEMPLATE_PATH = Path(__file__).with_name("templates") / "打样申请单模板.xlsx"

#: 工作表名（模板里的三张表；本模块只填第一张）
SHEET_APPLICATION = "打样申请单"

#: 一单多个明细时，申请单那一格用这个连接（申请单只有一格，装不下多行）
ITEM_NAME_SEPARATOR = "、"


def _patch_wps_styles(raw: bytes) -> bytes:
    """把 WPS 写出的两个空 `<fill/>` 补成合法填充，让 openpyxl 能读。

    见模块文档第 1 条。**只动 styles.xml**，其余部件原样透传（保住版式与批注）。
    """
    src = zipfile.ZipFile(io.BytesIO(raw))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "xl/styles.xml":
                text = data.decode("utf-8")
                text = re.sub(
                    r"<fill\s*/>", '<fill><patternFill patternType="none"/></fill>', text
                )
                data = text.encode("utf-8")
            dst.writestr(item, data)
    return out.getvalue()


def _text(value: Any) -> str:
    """任何值 → 单元格文本。`None` 与空串都当"没填"。"""
    if value is None:
        return ""
    if isinstance(value, Decimal):
        # 去掉小数末尾多余的 0（200.000 → 200），避免单子上出现 200.000 这种写法
        normalized = value.normalize()
        return f"{normalized:f}"
    return str(value)


def _total_quantity(items: list[Any]) -> str:
    """打样数量 = 明细数量合计（申请单只有一格）。"""
    total = Decimal("0")
    for item in items:
        qty = getattr(item, "quantity", None)
        if qty is not None:
            total += Decimal(str(qty))
    if total == 0:
        return ""
    return _text(total)


def _items_product_name(sample: Any, items: list[Any]) -> str:
    """申请单上的「产品名称」。

    优先用打样单上显式存的 `product_name`；没存就回退明细的名称
    （多行时用顿号连接）。**不在这里猜"取第一条"** —— 那会让"一单多样"的单子
    在单子上只剩一个名字，而看不出还有别的。
    """
    explicit = _text(getattr(sample, "product_name", None)).strip()
    if explicit:
        return explicit
    names = [_text(getattr(i, "item_name", None)).strip() for i in items]
    return ITEM_NAME_SEPARATOR.join(n for n in names if n)


def _require_label(existing: Any, label: str, value: str) -> str:
    """把值**追加**在标签后面（B8~B13 那 6 格）。

    模板里这些格已经写着"主要材质和规格："这样的标签，所以要保留标签、把内容接上；
    值得空就原样返回（宁可留一句标签，也不要出现"主要材质和规格："后面空着
    更让人以为漏填 —— 实际上空着就是没填，导出时调用方会提示）。
    """
    base = _text(existing)
    if not value:
        return base
    if not base:
        return value
    return f"{base}{value}"


def render_application_form(sample: Any, items: list[Any], *, applicant_name: str = "",
                            department_name: str = "") -> bytes:
    """生成填好的《打样申请单》xlsx 字节。

    `sample`：打样单（`SampleRequest`）；`items`：它的明细（`SampleItem` 列表）。
    `applicant_name` / `department_name`：申请人与其部门（由调用方查好传入 ——
    本模块不碰数据库，纯函数好测）。
    """
    raw = _patch_wps_styles(TEMPLATE_PATH.read_bytes())
    # `styles.xml` 里没有默认样式（WPS 生成的文件就是这样），openpyxl 会警告
    # "Workbook contains no default style"。它会自己套一份默认样式，
    # 而我们写的每一格都用 `_put_text` 显式给了类型 —— 这条警告在这里没有信息量，
    # 但**每次导出都会刷一行日志**，所以就地静音（不要用全局 filterwarnings：
    # 那会把别人的警告一起吞掉）。
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Workbook contains no default style")
        workbook = load_workbook(io.BytesIO(raw))
    sheet = workbook[SHEET_APPLICATION]

    def put(ref: str, value: Any) -> None:
        text = _text(value)
        if not text:
            return  # 没填就**不动那一格**：保留模板原有的空白，不写空串
        _put_text(sheet, ref=ref, value=text)

    # ---- 第 2 行：打样类型 / 产品名称 / 打样数量 / 交样时间 ----
    put("C2", getattr(sample, "sample_type", None))
    put("G2", _items_product_name(sample, items))
    put("I2", _total_quantity(items))
    put("K2", getattr(sample, "target_completion_date", None))

    # ---- 第 3 行：打样费用 / 申请人 / 打样部门 / 打样人 ----
    put("C3", getattr(sample, "sample_fee", None))
    put("G3", applicant_name)
    put("I3", department_name)
    put("K3", applicant_name)

    # ---- 第 4/5 行：三个勾选项 ----
    put("C4", getattr(sample, "sample_purpose", None))
    put("G4", getattr(sample, "urgency", None))
    put("C5", getattr(sample, "sample_situation", None))

    # ---- B8~B13：六个"要求"格（保留模板里的标签，值追加在后面）----
    _put_text(sheet, ref="B8", value=_require_label(sheet["B8"].value, "主要材质和规格：", _text(getattr(sample, "material_spec", None))))
    _put_text(sheet, ref="B9", value=_require_label(sheet["B9"].value, "产品尺寸：", _text(getattr(sample, "product_size", None))))
    _put_text(sheet, ref="B10", value=_require_label(sheet["B10"].value, "产品印刷：", _text(getattr(sample, "printing", None))))
    _put_text(sheet, ref="B11", value=_require_label(sheet["B11"].value, "主要辅料：", _text(getattr(sample, "accessories", None))))
    _put_text(sheet, ref="B12", value=_require_label(sheet["B12"].value, "包装要求：", _text(getattr(sample, "packaging", None))))
    _put_text(sheet, ref="B13", value=_require_label(sheet["B13"].value, "做法要求：", _text(getattr(sample, "craft_requirement", None))))

    # 刻意不填：B14/B15/B16、C17/E17/G17（生产部与采购在钉钉里填，见模块文档）

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
