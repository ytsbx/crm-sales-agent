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
    """把可空数字列解析成 Decimal；空串返回 None，非法值抛 ValueError。

    NaN / Infinity 必须挡掉：`Decimal("NaN")` 是合法 Decimal，
    但写进 Numeric 列会报数据库错、参与比较又一路 False，
    最后表现成"这个 SKU 的数据看着不对劲"，而不是"导入时告诉用户填错了"。
    """
    from decimal import Decimal, InvalidOperation

    text = (raw or "").strip()
    if not text:
        return None
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"「{raw}」不是合法数字") from exc
    if not value.is_finite():
        raise ValueError(f"「{raw}」不是有限数字")
    return value


def _int(raw: str | None):
    """整数列。**不做 `int(float(x))` 截断**。

    原来 `int(float("2.9"))` = 2：装箱数、起订量这类字段被静默改小，
    用户看到的和文件里的不一致，而且没有任何提示 —— 比直接报错难查得多。
    """
    from decimal import Decimal, InvalidOperation

    text = (raw or "").strip()
    if not text:
        return None
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"「{raw}」不是合法整数") from exc
    if not value.is_finite() or value != value.to_integral_value():
        raise ValueError(f"「{raw}」不是合法整数")
    return int(value)


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
    moq = _int(row.get("起订量") if row.get("起订量") is not None else row.get("MOQ"))
    if moq is not None and moq < 0:
        raise ValueError("起订量不能为负数")
    carton_qty = _int(row.get("装箱数"))
    if carton_qty is not None and carton_qty < 0:
        raise ValueError("装箱数不能为负数")
    for label, value in (("长", _decimal(row.get("长"))), ("宽", _decimal(row.get("宽"))),
                         ("高", _decimal(row.get("高"))), ("重量", _decimal(row.get("重量"))),
                         ("箱规体积", _decimal(row.get("箱规体积")))):
        if value is not None and value < 0:
            raise ValueError(f"{label}不能为负数")
    return {
        "sku_code": _text(row.get("SKU编码"), "SKU编码", 64) or "",
        "name": _text(row.get("规格名称"), "规格名称", 200),
        "specification": _text(row.get("规格"), "规格", 200),
        "color": _text(row.get("颜色"), "颜色", 64),
        "material": _text(row.get("材质"), "材质", 64),
        "length": _decimal(row.get("长")),
        "width": _decimal(row.get("宽")),
        "height": _decimal(row.get("高")),
        "weight": _decimal(row.get("重量")),
        "carton_qty": carton_qty,
        "carton_volume": _decimal(row.get("箱规体积")),
        "moq": moq,
        "package_type": _text(row.get("包装方式"), "包装方式", 64),
        "unit": _text(row.get("单位"), "单位", 16) or "件",
    }
