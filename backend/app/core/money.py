"""Shared stored line amounts for quotes, orders and generated documents."""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def line_amount(quantity: Decimal, unit_price: Decimal) -> Decimal:
    """Round each line to cents, using PostgreSQL numeric rounding."""
    quantity = Decimal(quantity).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    return (quantity * Decimal(unit_price)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def format_unit_price(value) -> str:
    """对客文件里的**单价**怎么印：保留实际成交精度（最多四位小数），末尾不补零。

    为什么不能和金额用同一套两位小数（第二轮审查 P2，2026-10-12）：

    单价列存的是 `Numeric(16,4)`，成交价 33.3333、数量 1000 时，两位格式会印成
        单价 33.33 × 1000 = 33330.00，而金额那一栏是 33333.30 —— 差 3.30，
        客户拿计算器一按就对不上，还说不清是谁算错了。

    所以：单价按库里的精度印（`33.3333`、`100`、`12,500.5`），金额继续按
    "逐行两位"的既定口径（`line_amount`）印。数量同理，不做四舍五入
    （`12.5` 不能印成 `12.50` 或 `13`）。

    两位小数的金额 + 四位小数的单价之间**仍可能有分位以内的尾差**（例如
    33.3333 × 3 = 99.9999 而金额按逐行规则印 100.00）。这是"逐行四舍五入"
    这个既定口径自带的，不能靠改单价去凑；口径本身由 `line_amount` 一处定义。
    """
    if value is None:
        return ""
    text_in = str(value).strip()
    if not text_in:
        return ""
    try:
        amount = Decimal(text_in)
    except (InvalidOperation, ValueError):
        # 传进来的不是数字（历史脏数据）：原样返回，别把它变成"0"
        return str(value)
    text = f"{amount.quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP):f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    integer, _, fraction = text.partition(".")
    sign = "-" if integer.startswith("-") else ""
    digits = integer.lstrip("-")
    grouped = f"{int(digits):,}" if digits.isdigit() else digits
    return f"{sign}{grouped}" + (f".{fraction}" if fraction else "")
