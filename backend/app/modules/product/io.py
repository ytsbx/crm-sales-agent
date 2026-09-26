"""产品与 SKU 批量导入导出。

与客户/线索导入同一套规矩（见 app/core/csvio.py），字段口径按 02-ER §9/§10：

- 产品是"款"，SKU 是"款下的具体规格"，所以 SKU 的表里要带**产品名称**
  （人对得上），导入时按名称找产品；找不到就报错，不自动建产品 ——
  自动建会把"型号录错"变成一堆重复产品，比导入失败难收拾得多。
- SKU 的 `sku_code` 全局唯一，导入时按它查重。
"""

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
    "MOQ",
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
    "MOQ",
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
        product.created_at.strftime("%Y-%m-%d") if product.created_at else "",
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
        sku.created_at.strftime("%Y-%m-%d") if sku.created_at else "",
    ]


def _decimal(raw: str | None):
    """把可空数字列解析成 Decimal；空串返回 None，非法值抛 ValueError。"""
    from decimal import Decimal, InvalidOperation

    text = (raw or "").strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"「{raw}」不是合法数字") from exc


def _int(raw: str | None):
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError as exc:
        raise ValueError(f"「{raw}」不是合法整数") from exc


# 供 router 使用：把一行 CSV 变成 ORM 字段。
# 中文表头 -> 列名的映射集中在这里，router 不再自己拼。

PRODUCT_COLUMN_LABELS = {
    "product_line": "产品线",
    "category": "分类",
    "brand": "品牌",
    "description": "描述",
    "knowledge": "产品知识",
}


def product_fields_from_row(row: dict) -> dict:
    return {
        column: ((row.get(label) or "").strip() or None)
        for column, label in PRODUCT_COLUMN_LABELS.items()
    }


def sku_fields_from_row(row: dict) -> dict:
    return {
        "sku_code": (row.get("SKU编码") or "").strip(),
        "name": (row.get("规格名称") or "").strip() or None,
        "specification": (row.get("规格") or "").strip() or None,
        "color": (row.get("颜色") or "").strip() or None,
        "material": (row.get("材质") or "").strip() or None,
        "length": _decimal(row.get("长")),
        "width": _decimal(row.get("宽")),
        "height": _decimal(row.get("高")),
        "weight": _decimal(row.get("重量")),
        "carton_qty": _int(row.get("装箱数")),
        "carton_volume": _decimal(row.get("箱规体积")),
        "moq": _int(row.get("MOQ")),
        "package_type": (row.get("包装方式") or "").strip() or None,
        "unit": (row.get("单位") or "").strip() or "件",
    }
