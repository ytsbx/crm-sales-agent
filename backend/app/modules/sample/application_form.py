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

判据：**值的格 = 标签所在合并块右边第一格**。用合并块边界判，不要"目测第几列" ——
这一处曾经整体右移一格（交样时间写到 K2、打样人写到 K3），
结果主持人打开生成的表看到那几格是**空的**，值跑进了右边的空白列：

    C2 打样类型        E2 产品名称        G2 打样数量        I2 交样时间
    C3 打样费用        E3 申请人          G3 打样部门        I3 打样人
    C4 打样目的（勾选）                   G4 紧急程度（勾选）
    C5 样品情况（勾选）
    B8 主要材质和规格   B9 产品尺寸   B10 产品印刷
    B11 主要辅料       B12 包装要求  B13 做法要求
    A6 那一块（A6:I6）「产品图片」：图片**浮在上面**，最多 4 张

## 一条规矩：**只对应"两边都有"的字段**（主人 2026-10-16）

> 主人原话：「啥呀，『打样产品大类』和『样品制作单位』这为什么要进 excel 呀」
> 「excel 没有的字段为什么要管啊」

**Excel 上没有那一格，就不要写它，更不要为了写它去给 Excel 加行加格。**
那份申请表是业务上定下来的纸（车间的做法要求、采购的回复区都印在上面），
**纸不改，程序围着纸转**。

按这条规矩，下面这些**钉钉上有的控件不进这里**：

    打样产品大类 / 样品制作单位 / 打样需求说明 / 本次打样积分 / 打样申请编号
    （钉钉单上填；Excel 本来就没有对应的格 —— **什么都不做是对的**）

反过来，**Excel 上有格、钉钉上没有对应控件的**（打样目的 / 紧急程度 / 样品情况 /
交样时间 / 打样人 / 打样费用 / 六项要求…）由 CRM 这边直接填，见上面的位图。


**刻意留空的格**（不是漏了，是"不该由 CRM 填"）：

    B14/B15 采购部门回复内容（采购接收人）—— 批注写着"此处为采购填写"
    B16     打样部回复内容
    C17/E17/G17 打样部的打样人 / 完成时间 / 样品确认人 —— 表上另有这三格，留给打样部

> 注意别和上面第 3 行的「打样人」搞混：那两个是**不同的格** ——
> 第 3 行 H3「打样人」在**申请抬头**里（与申请人/打样部门并列，CRM 会填申请人兜底），
> 第 17 行那个在**打样部回复内容**区里（谁实际动手打的样，打样部在钉钉里填）。

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

#: 「产品图片（多方位图片）」那一格最多放几张（主人 2026-10-16：「不是可以放4张吗」）
MAX_PRODUCT_IMAGES = 4

#: 单张产品图放进去之后的**最大高度**（像素）。撑高第 6 行时以它为准 ——
#: 太高会把下面的「打样具体要求」推得太靠下，申请表就不像一张单子了。
MAX_IMAGE_HEIGHT = 140


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


def _embed_product_images(sheet: Any, paths: list[Path]) -> int:
    """把产品图片**浮在「产品图片（多方位图片）」那一块上**（A6:I6），返回放进去的张数。

    ## 为什么是"浮在上面"而不是"存进单元格"

    Excel 的单元格里装不了图片（只能装文字/数字），图片是**独立的绘图对象**、锚在某个格上。
    实测确认可行：插入后工作簿里会多出 `xl/media/imageN.png` 与 `xl/drawings/drawing1.xml`。

    ## 为什么要把第 6 行撑高

    那一块（A6:I6）**只有一行高**（实测 18.75 磅 ≈ 25 px），横着 839 px 宽。
    图片若按原尺寸放进去会**盖住下面的行**（紧接着就是第 8 行的「打样具体要求」六格，
    那是给车间看的正文，被压住就白做了）。所以：

    - **横排 4 张**（主人 2026-10-16：「产品图片不是可以放4张吗」——正好对上"多方位图片"）；
    - 每张按可用宽度等分并留间隙，**保持长宽比**缩放；
    - 把第 6 行的高度撑到图片高度 —— 这样下面的内容是被"推下去"，而不是被盖住。

    ## 放不进去的不硬塞

    - 只认 Excel 能嵌的格式（png/jpg/jpeg/gif/bmp）。**webp / heic / pdf 一律跳过** ——
      转换失败宁可少一张，也不要让整份申请表生成失败（那会让"发起审批"这个动作整个卡住）；
    - 单张读不出来（文件损坏、权限）**跳过这一张**，不影响其余。
    """
    if not paths:
        return 0
    try:  # 延迟导入：只有真的要放图时才需要 PIL
        from openpyxl.drawing.image import Image as XLImage
        from PIL import Image as PILImage
    except ImportError:  # pragma: no cover - 依赖缺失时静默跳过，不阻断发单
        return 0

    # A6:I6 的可用宽度：按各列列宽之和换算（见模块文档里的量法）
    # A~I 各列宽（像素，模板实测）：31/66/108/87/122/94/115/94/122，合计 839。
    # 四张图分别锚在 **C / E / G / I** 列（大列），这样每张图落在自己那一列里，
    # 不会靠"浮在合并块上"去压别人的格子。
    anchors = ("C6", "E6", "G6", "I6")
    per_image = 180  # 每张图的最大边长（像素）；C 列 108px 放不下 180 是正常的 ——
    # 图片是浮动对象，允许略宽于所在列（两侧的 B/D 在本行是同一合并块的空格）

    placed = 0
    for index, path in enumerate(paths[:MAX_PRODUCT_IMAGES]):
        try:
            with PILImage.open(path) as probe:
                width, height = probe.size
                scale = min(per_image / width, MAX_IMAGE_HEIGHT / height, 1.0)
                target = (max(1, int(width * scale)), max(1, int(height * scale)))
                # ⚠️ 必须**真的把图缩小**（PIL 重采样），不能只设 `img.width`：
                # 实测 openpyxl 保存时会**从底图重新算尺寸**，赋给 `image.width`
                # 的值会被丢掉（存回来还是原始尺寸 400x300，图就盖住了下面的行）。
                buffer = io.BytesIO()
                probe.convert("RGB").resize(target, PILImage.LANCZOS).save(buffer, format="PNG")
        except Exception:
            # 单张坏图不该毁掉整份申请表（否则"发起审批"这个动作会整个卡住）
            continue
        sheet.add_image(XLImage(buffer), anchors[index])
        placed += 1

    if placed:
        # 撑高第 6 行：图片高度 + 余量（磅 = 像素 × 72 / 96）——
        # 这样下面的「打样具体要求」是被**推下去**，而不是被盖住。
        sheet.row_dimensions[6].height = (MAX_IMAGE_HEIGHT + 10) * 72 / 96
    return placed


def render_application_form(sample: Any, items: list[Any], *, applicant_name: str = "",
                            department_name: str = "", product_images: list[Path] | None = None,
                            sample_maker: str = "") -> bytes:
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
    #
    # ⚠️ **值的格 = 标签合并块右边第一格**（2026-10-16 修正一处系统性错位）。
    # 判据用**合并块边界**，不要用"目测第几列"——模板上 "A2:B2" 是"打样类型"这个
    # 标签块，值要写在它**右边**的 C2。原来这一行我整体右移了一格
    # （交样时间写到 K2、打样人写到 K3），于是主持人在生成的表上看不到
    # 「交样时间 / 打样人」—— 那几格是空的，值跑到右边的空白列里去了。
    put("C2", getattr(sample, "sample_type", None))       # 标签 A2:B2
    put("E2", _items_product_name(sample, items))          # 标签 D2
    put("G2", _total_quantity(items))                      # 标签 F2
    # ⚠️「交样时间」取 `deliver_date`，**不是** `target_completion_date`
    # （主人 2026-10-16：「要求完成时间和交样时间不一定是一个时间」）：
    # 前者是"什么时候交给客户"，后者是钉钉单上的"要求完成日期"（车间什么时候做完）。
    put("I2", getattr(sample, "deliver_date", None))  # 标签 H2「交样时间」

    # ---- 第 3 行：打样费用 / 申请人 / 打样部门 / 打样人 ----
    put("C3", getattr(sample, "sample_fee", None))         # 标签 A3:B3
    put("E3", applicant_name)                              # 标签 D3「申请人」
    put("G3", department_name)                             # 标签 F3「打样部门」
    # 「打样人」= 谁动手打的样（主人 2026-10-16：「打样人我也没看见有填的地方」）。
    # ⚠️ **不填就留空**，不再回落到申请人：那等于替看表的人断言
    # "申请人就是打样的人"—— 两个是不同的角色（主人 2026-10-16 也否掉了
    # 我在界面上加的那句"谁动手打的样"的解释）。
    put("I3", sample_maker)                                # 标签 H3「打样人」

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

    # 产品图片：浮在 A6:I6 那一块上（见 `_embed_product_images` 的说明）
    _embed_product_images(sheet, list(product_images or []))

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
