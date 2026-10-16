"""《打样申请单》导出：填写位图、选项校验、注入防护、派生积分（2026-10-15）。

## 这些用例守的是什么

钉钉那份《打样申请单》是**公司现有表单**（表头印着"表单编码：D01-003"），
格式不能改。CRM 把数据填进**原版式的底稿**，业务员下载即传钉钉 ——
所以"填对了哪一格"是这件事的全部正确性所在。

四个容易错、且错了不容易发现的地方：

1. **填错格**：申请单是"标签在左、值在右"的表单版式（C2 才是打样类型的值格，
   B2 属于合并块"打样类型"），填错一格业务员在钉钉里就会看到错位的单子。
2. **把生产部/采购的格也填了**：B14/B15/B16 与 C17/E17/G17 是采购与打样部
   在钉钉里填的（批注原文"此处为采购填写"）。CRM 填了就是**造假**。
3. **用户文本变成公式**：产品名/规格这些是外部输入，`=HYPERLINK(...)` 这类
   会被 Excel 当公式求值。
4. **积分被手填**：口径是"工时 × 10"，若允许传入，两个人会填出两个数。
"""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.modules.sample.application_form import (
    _patch_wps_styles,
    _total_quantity,
    render_application_form,
)


def _sample(**over):
    base = dict(
        sample_type="销售客户打样",
        product_name="出口纸箱 400×300×250",
        target_completion_date=date(2026, 11, 5),   # 钉钉的「要求完成日期」
        deliver_date=date(2026, 11, 20),            # 申请表的「交样时间」（**另一个日期**）
        sample_maker="李师傅",                       # 申请表的「打样人」
        sample_fee=Decimal("800.00"),
        sample_purpose="报价核算样",
        urgency="急",
        sample_situation="客户首次要求",
        material_spec="牛皮纸 120g/㎡",
        product_size="400×300×250mm",
        printing="四色印刷 + 覆膜",
        accessories="PE 内袋、打包带",
        packaging="5 层瓦楞外箱，每箱 20 只",
        craft_requirement="模切后手工粘盒",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _items():
    return [
        SimpleNamespace(item_name="出口纸箱 A", quantity=Decimal("120")),
        SimpleNamespace(item_name="出口纸箱 B", quantity=Decimal("80")),
    ]


def _cells(data: bytes) -> dict[str, str]:
    """把生成的 xlsx 读成 `{格: 文本}`（绕开 openpyxl，直接读 XML，顺带能看 `t=` 类型）。"""
    import io
    import xml.etree.ElementTree as ET
    import zipfile

    NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    z = zipfile.ZipFile(io.BytesIO(data))
    shared: list[str] = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(f"{NS}si"):
            shared.append("".join(t.text or "" for t in si.iter(f"{NS}t")))
    root = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
    out: dict[str, str] = {}
    for row in root.iter(f"{NS}row"):
        for c in row.findall(f"{NS}c"):
            v = c.find(f"{NS}v")
            inline = c.find(f"{NS}is")
            if inline is not None:
                text = "".join(x.text or "" for x in inline.iter(f"{NS}t"))
            elif v is not None and v.text is not None:
                if c.get("t") == "s" and v.text.isdigit():
                    text = shared[int(v.text)]
                else:
                    text = v.text
            else:
                text = ""
            if text.strip():
                out[c.get("r")] = text.strip()
    return out


def _types(data: bytes) -> dict[str, str]:
    """每格的 `t` 属性（`inlineStr`/`s` = 文本；`f` 或含 `<f>` = 公式）。"""
    import io
    import xml.etree.ElementTree as ET
    import zipfile

    NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    root = ET.fromstring(
        zipfile.ZipFile(io.BytesIO(data)).read("xl/worksheets/sheet1.xml")
    )
    out = {}
    for row in root.iter(f"{NS}row"):
        for c in row.findall(f"{NS}c"):
            t = c.get("t") or ""
            if c.find(f"{NS}f") is not None:
                t = "f"
            out[c.get("r")] = t
    return out


# ---------------------------------------------------------------- 填写位图


@pytest.mark.parametrize(
    "ref,expect",
    [
        ("C2", "销售客户打样"),          # 打样类型（标签块 A2:B2 右边第一格）
        ("E2", "出口纸箱 400×300×250"),  # 产品名称（标签 D2）
        ("G2", "200"),                   # 打样数量 = 明细合计 120+80（标签 F2）
        ("I2", "2026-11-20"),            # 交样时间（标签 H2）取 deliver_date，不是要求完成日
        ("C3", "800"),                   # 打样费用
        ("E3", "张三"),                 # 申请人（标签 D3）
        ("G3", "销售一部"),             # 打样部门（标签 F3）
        ("I3", "李师傅"),               # 打样人（标签 H3）取 sample_maker
        ("C4", "报价核算样"),            # 打样目的
        ("G4", "急"),                    # 紧急程度
        ("C5", "客户首次要求"),          # 样品情况
    ],
)
def test_values_land_in_the_right_cells(ref, expect):
    """填错一格的后果是业务员在钉钉里看到错位的单子，所以逐格钉住。"""
    data = render_application_form(_sample(), _items(), applicant_name="张三", department_name="销售一部", sample_maker="李师傅")
    assert _cells(data)[ref] == expect


@pytest.mark.parametrize(
    "ref,keyword",
    [
        ("B8", "牛皮纸"),
        ("B9", "400×300×250mm"),
        ("B10", "四色印刷"),
        ("B11", "PE 内袋"),
        ("B12", "5 层瓦楞外箱"),
        ("B13", "模切后手工粘盒"),
    ],
)
def test_requirement_cells_keep_the_template_label(ref, keyword):
    """B8~B13 模板里本来就写着"主要材质和规格："，值要**接在标签后面**而不是覆盖它。"""
    data = render_application_form(_sample(), _items())
    text = _cells(data)[ref]
    assert text.startswith(("主要材质和规格：", "产品尺寸：", "产品印刷：", "主要辅料：", "包装要求：", "做法要求："))
    assert keyword in text


def test_multi_item_names_are_joined_not_silently_truncated():
    """一单多样时申请单只有一格：要**都列出来**，不能只取第一条。"""
    data = render_application_form(_sample(product_name=None), _items())
    assert _cells(data)["E2"] == "出口纸箱 A、出口纸箱 B"


def test_explicit_product_name_wins():
    data = render_application_form(_sample(product_name="整单名称"), _items())
    assert _cells(data)["E2"] == "整单名称"


# ------------------------------------------------- 生产部/采购的格必须留空


@pytest.mark.parametrize("ref", ["B15", "C17", "E17", "G17"])
def test_production_and_purchase_cells_keep_only_template_labels(ref):
    """采购与打样部在钉钉里填的格 —— CRM **不能**填（生产部不在 CRM 里，填了是假的）。

    这几格模板里本来就有标签（"采购接收人："等），所以判据是"只有标签、没被追加内容"。
    """
    data = render_application_form(_sample(), _items(), applicant_name="张三", department_name="销售一部", sample_maker="李师傅")
    assert _cells(data)[ref] in {"采购接收人：", "打样人：", "完成时间：", "样品确认人："}


def test_empty_fields_do_not_write_empty_strings():
    """没填的字段**不写空串**：保留模板原样，而不是把标签擦掉。"""
    sample = _sample(sample_type="", product_name="", material_spec="", urgency="")
    data = render_application_form(sample, [])
    cells = _cells(data)
    assert "C2" not in cells                      # 没填就不写
    assert cells["B8"] == "主要材质和规格："       # 标签还在，没被空串覆盖


# ---------------------------------------------------------------- 注入防护


@pytest.mark.parametrize(
    "evil",
    [
        '=HYPERLINK("http://evil.example","点我")',
        "=1+1",
        "+1+1",
        "-1+1",
        "@SUM(A1)",
    ],
)
def test_user_text_never_becomes_a_formula(evil):
    """用户文本进 Excel 必须当**文本**：`=` 开头若被当公式，Excel 打开就求值。"""
    data = render_application_form(_sample(product_name=evil, material_spec=evil), [])
    cells, types = _cells(data), _types(data)
    assert cells["E2"] == evil and types["E2"] != "f"
    assert types["B8"] != "f"


# ---------------------------------------------------------------- 派生与边界


def test_craft_points_is_not_an_input_field():
    """积分是派生值（工时 × 10），入参里**不该有**它 —— 否则两个人会填出两个数。"""
    from app.modules.sample.schema import SampleUpdate

    fields = SampleUpdate.model_fields
    assert "estimated_hours" in fields
    # craft_points 是输出字段，不是入参
    assert "craft_points" not in fields


def test_total_quantity_is_a_sum():
    assert _total_quantity(_items()) == "200"
    assert _total_quantity([]) == ""


def test_decimal_quantity_has_no_trailing_zeros():
    """200.000 这种写法不该出现在单子上（金额/数量都走同一套归一）。"""
    items = [SimpleNamespace(item_name="A", quantity=Decimal("200.000"))]
    assert _total_quantity(items) == "200"


def test_template_is_readable_despite_wps_styles():
    """底稿是 WPS 存的，`styles.xml` 里有两个空 `<fill/>`，openpyxl 直接读会抛
    `TypeError: Fill() takes no arguments`。这条守住补丁这一步 ——
    去掉它整个功能就没了（而且报错信息完全看不出原因）。"""
    from app.modules.sample.application_form import TEMPLATE_PATH

    raw = TEMPLATE_PATH.read_bytes()
    import io

    import openpyxl

    with pytest.raises(Exception):
        openpyxl.load_workbook(io.BytesIO(raw))  # 原样读 → 抛
    import warnings

    with warnings.catch_warnings():
        # 打过补丁后能读，但 openpyxl 会警告"没有默认样式"（WPS 文件固有，见导出模块）
        warnings.filterwarnings("ignore", message="Workbook contains no default style")
        workbook = openpyxl.load_workbook(io.BytesIO(_patch_wps_styles(raw)))
    assert workbook.sheetnames[0] == "打样申请单"
    # 版式要保住：合并块与批注都还在
    assert len(workbook["打样申请单"].merged_cells.ranges) == 26


def test_schema_rejects_values_outside_the_dingtalk_options():
    """选项**照抄钉钉下拉**。写错一个字的后果是导出单子与钉钉单据对不上，
    而"外协厂打样"和"外协厂"人眼很难发现。"""
    from app.modules.sample.schema import SampleUpdate

    SampleUpdate(maker_unit="外协厂")  # 合法
    with pytest.raises(ValidationError):
        SampleUpdate(maker_unit="外协厂打样")  # 差一个字 → 拒
    with pytest.raises(ValidationError):
        SampleUpdate(urgency="非常紧急")
    with pytest.raises(ValidationError):
        SampleUpdate(sample_type="销售打样")


def test_schema_rejects_negative_hours():
    from app.modules.sample.schema import SampleUpdate

    with pytest.raises(ValidationError):
        SampleUpdate(estimated_hours="-1")
    SampleUpdate(estimated_hours="0")  # 0 小时合法（工时可以很小）

# ------------------------------------------- 申请表上那三个勾选项（C4/G4/C5）


@pytest.mark.parametrize(
    "ref,field,value",
    [
        ("C4", "sample_purpose", "报价核算样"),
        ("G4", "urgency", "急"),
        ("C5", "sample_situation", "客户首次要求"),
    ],
)
def test_choice_fields_land_in_their_cells(ref, field, value):
    """打样目的 / 紧急程度 / 样品情况 → 申请表的 C4 / G4 / C5。

    这三项**不在钉钉模板的 13 个控件里**（2026-10-16 拉模板核对过），只能由 CRM 带过去。
    它们曾经"哪里都填不了"（主人：「还有打样目的，紧急程度…为什么不让我填」）——
    详情页能填但钉钉弹窗是建完单立刻弹出来的，那时还没填过。现在弹窗里能填了，
    这条守住"填了真的会出现在表上"。
    """
    sample = _sample(**{field: value})
    assert _cells(render_application_form(sample, []))[ref] == value


@pytest.mark.parametrize("ref", ["C4", "G4", "C5"])
def test_choice_cells_keep_template_text_when_not_filled(ref):
    """没填时**保留模板原有的勾选文字**，不要拿坏值去凑。

    申请表原件这三格印着「¨客审确认样 ¨报价核算样 …」这样的勾选项 ——
    没填就让它保持原样（打样师傅/主管看得懂），而不是写个空白或占位符。
    """
    sample = _sample(sample_purpose=None, urgency=None, sample_situation=None)
    text = _cells(render_application_form(sample, []))[ref]
    assert ("¨" in text) or ("□" in text)

# ------------------------------------------- 产品图片（浮在 A6:I6 那一块上）


def _make_png(path, size=(400, 300), color=(200, 40, 40)):
    from PIL import Image as PILImage

    PILImage.new("RGB", size, color).save(path)
    return path


def test_product_images_are_embedded_and_scaled(tmp_path):
    """产品图片**真的进得去**（主人 2026-10-16：「产品图片不是可以放4张吗」）。

    Excel 的单元格装不了图片，图片是**独立的绘图对象**、锚在格子上。所以这条测的是：
    工作簿里多出图片对象、**尺寸被缩到限值内**、且锚在第 6 行（A6:I6 那一块）。

    ⚠️ 缩放必须是**真的重采样**：实测 openpyxl 保存时会从底图重新算尺寸，
    只赋 `img.width` 的值会被丢掉（存回来还是 400x300，图就盖住了下面的行）。
    """
    import io

    from openpyxl import load_workbook

    paths = [_make_png(tmp_path / f"p{i}.png", size) for i, size in enumerate(
        [(400, 300), (300, 400), (500, 200), (250, 250)], start=1
    )]
    data = render_application_form(_sample(), _items(), product_images=paths)
    workbook = load_workbook(io.BytesIO(data))
    sheet = workbook["打样申请单"]
    on_row7 = [i for i in sheet._images if (i.anchor._from.row + 1) == 7]
    assert len(on_row7) == 4
    for image in on_row7:
        assert image.width <= 180 and image.height <= 140, (image.width, image.height)
    # 第 7 行是模板预留的图片框，下面的要求行仍然存在。
    assert sheet.row_dimensions[7].height and sheet.row_dimensions[7].height > 40
    assert sheet["A8"].value == "打样具体要求"


def test_no_images_keeps_the_template_untouched(tmp_path):
    """没有图片时不撑行高、不加图 —— 不能因为新功能把正常的单子也改了。"""
    import io

    from openpyxl import load_workbook

    data = render_application_form(_sample(), _items())
    sheet = load_workbook(io.BytesIO(data))["打样申请单"]
    assert [i for i in sheet._images if (i.anchor._from.row + 1) == 6] == []
    assert sheet.row_dimensions[6].height in (None, 18.75)


def test_broken_image_is_rejected(tmp_path):
    """无效图片要明确拒绝，避免把坏附件静默传给钉钉。"""
    import io

    from openpyxl import load_workbook

    good = _make_png(tmp_path / "ok.png")
    missing = tmp_path / "nope.png"  # 不存在
    with pytest.raises(ValueError, match="第1张产品图片无法读取"):
        render_application_form(_sample(), _items(), product_images=[missing, good])


def test_at_most_four_images(tmp_path):
    """最多 4 张，超出时明确提示整理附件。"""
    import io

    from openpyxl import load_workbook

    paths = [_make_png(tmp_path / f"q{i}.png") for i in range(6)]
    with pytest.raises(ValueError, match="产品图片最多4张"):
        render_application_form(_sample(), _items(), product_images=paths)


@pytest.mark.parametrize(
    "filename,mime,expected",
    [
        ("照片.jpg", "", True),
        ("图.PNG", "", True),
        ("a.bmp", "", True),
        ("x.pdf", "", False),
        ("图纸.pdf", "application/pdf", False),
        ("明细.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", False),
        ("无扩展名", "image/png", True),
        ("无扩展名", "", False),
    ],
)
def test_is_image_judgement(filename, mime, expected):
    """只把**图片**放进产品图片那格；PDF / Excel 不算（它们仍走钉钉附件）。

    两个判据都用（mime 与扩展名），任一命中即算 —— 宁多多试一张，
    因为生成器里读不出来的图会跳过，不会坏事。
    """
    from app.modules.sample.dingtalk import _is_image

    assert _is_image(filename, mime) is expected
