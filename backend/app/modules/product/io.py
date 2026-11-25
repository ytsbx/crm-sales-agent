"""产品与 SKU 批量导入导出。

与客户/线索导入同一套规矩（见 app/core/csvio.py），字段口径按 02-ER §9/§10：

- 产品是"款"，SKU 是"款下的具体规格"，所以 SKU 的表里要带**产品名称**
  （人对得上），导入时按名称找产品；找不到就报错，不自动建产品 ——
  自动建会把"型号录错"变成一堆重复产品，比导入失败难收拾得多。
- SKU 的 `sku_code` 全局唯一，导入时按它查重。
"""
from app.core.errors import AppError
from app.core.timebase import to_business
from app.modules.product.field_rules import normalize_numeric


PRODUCT_TEMPLATE_HEADERS = [
    "产品名称",
    "产品线",
    "分类",
    "品牌",
    "描述",
    "产品知识",
]

PRODUCT_EXPORT_HEADERS = [
    "产品名称",
    "产品线",
    "分类",
    "品牌",
    "状态",
    "SKU 数",
    "创建时间",
]

SKU_TEMPLATE_HEADERS = [
    "SKU编码",
    "产品名称",
    "规格名称",
    "规格",
    "颜色",
    "材质",
    "长",
    "宽",
    "高",
    "重量",
    "装箱数",
    "箱规体积",
    "起订量",
    "包装方式",
    "单位",
]

SKU_EXPORT_HEADERS = [
    "SKU编码",
    "产品名称",
    "规格名称",
    "规格",
    "颜色",
    "材质",
    "单位",
    "起订量",
    "状态",
    "创建时间",
]


def product_export_row(product, sku_count: int) -> list:
    return [
        product.name,
        product.product_line or "",
        product.category or "",
        product.brand or "",
        product.status,
        sku_count,
        to_business(product.created_at).strftime("%Y-%m-%d") if product.created_at else "",
    ]


def sku_export_row(sku, product_name: str | None) -> list:
    return [
        sku.sku_code,
        product_name or "",
        sku.name or "",
        sku.specification or "",
        sku.color or "",
        sku.material or "",
        sku.unit or "",
        sku.moq if sku.moq is not None else "",
        sku.status,
        to_business(sku.created_at).strftime("%Y-%m-%d") if sku.created_at else "",
    ]


def _sku_number(field: str, raw: str | None):
    try:
        return normalize_numeric(field, raw, where="SKU 导入")
    except AppError as exc:
        raise ValueError(exc.message) from exc


def _text(raw: str | None, label: str, max_length: int) -> str | None:
    """文本列 + 长度校验。

    超长时数据库会抛 `value too long for type character varying(64)`，
    用户看到的是一句数据库方言的错误码；这里提前说清是哪个字段、有多长。
    """
    text = (raw or "").strip()
    if not text:
        return None
    if len(text) > max_length:
        raise ValueError(f"{label}长度不能超过 {max_length}（当前 {len(text)}）")
    return text


# 供 router 使用：把一行 CSV 变成 ORM 字段。
# 中文表头 -> 列名的映射集中在这里，router 不再自己拼。

PRODUCT_COLUMN_LABELS = {
    "product_line": "产品线",
    "category": "分类",
    "brand": "品牌",
    "description": "描述",
    "knowledge": "产品知识",
}

#: 文本列的库内长度上限（与 product/model.py 的 String(...) 对齐）。
#: 导入时就按它校验，超长给一句人话，而不是让数据库抛方言错误。
PRODUCT_TEXT_LIMITS = {
    "product_line": 64,
    "category": 64,
    "brand": 64,
    # description / knowledge 是 Text 列，不设实际上限（给一个防爆值即可）
    "description": 100000,
    "knowledge": 100000,
}


def product_fields_from_row(row: dict) -> dict:
    return {
        column: _text(row.get(label), label, PRODUCT_TEXT_LIMITS[column])
        for column, label in PRODUCT_COLUMN_LABELS.items()
    }


def sku_fields_from_row(row: dict) -> dict:
    """一行 CSV -> Sku 字段。

    非法值一律抛 `ValueError`（带人话原因），由调用方决定记成哪一行的失败；
    **不做截断、不把非法值当空白**：静默改数比导入失败难查得多。
    """
    #: 表头兼容两种写法（2026-10-10 把模板/导出改成中文「起订量」）：
    #: **已经导出去的表格里写的是 `MOQ`**，只认中文会让那些人一导入就失败。
    #: 读的时候两种都收，新的写出去用中文。
    moq = row.get("起订量") if row.get("起订量") is not None else row.get("MOQ")
    return {
        "sku_code": _text(row.get("SKU编码"), "SKU编码", 64) or "",
        "name": _text(row.get("规格名称"), "规格名称", 200),
        "specification": _text(row.get("规格"), "规格", 200),
        "color": _text(row.get("颜色"), "颜色", 64),
        "material": _text(row.get("材质"), "材质", 64),
        "length": _sku_number("length", row.get("长")),
        "width": _sku_number("width", row.get("宽")),
        "height": _sku_number("height", row.get("高")),
        "weight": _sku_number("weight", row.get("重量")),
        "carton_qty": _sku_number("carton_qty", row.get("装箱数")),
        "carton_volume": _sku_number("carton_volume", row.get("箱规体积")),
        "moq": _sku_number("moq", moq),
        "package_type": _text(row.get("包装方式"), "包装方式", 64),
        "unit": _text(row.get("单位"), "单位", 16) or "件",
    }
