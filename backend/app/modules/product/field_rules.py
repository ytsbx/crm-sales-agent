"""SKU 主数据里**物理量字段**的唯一约束来源。

## 为什么要有这个文件（第九批问题 ④，2026-10-13）

同一个字段在项目里有两条写入路径：

1. **常规接口**：`SkuCreate` / `SkuUpdate`（`product/schema.py`）—— 这些约束
   早就写好了（`ge=0`、`max_digits=12`、小数位 3/4）。
2. **主数据核定**：外部来源报值 → 人工核定"以来源为准" → `master.normalize_field_value`
   → 直接写 `skus`。

第 2 条路**绕过了第 1 条的约束**，实测能落库的脏数据：

- 重量 `-5` → 落库 `weight = -5.000`（负重量会一路算进运费试算）；
- 装箱数 `"1.9"` → `int(Decimal("1.9"))` **静默截断成 1**（"不截断、要么拒要么收"）；
- 重量 `1e30` → 撞数据库列范围，**500**（应该是 422）。

修法不是"在核定那边再抄一遍校验"——两份约束迟早漂移（这个项目里已经反复出现
同类问题）。所以把**约束定义抽到这里**，`schema.py` 与 `master.py` 都读它。

## 一条口径

**要么接受、要么拒绝，绝不静默改值。** 小数装箱数是拒绝（422），不是截断。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from app.core.errors import AppError, ErrorCode


@dataclass(frozen=True)
class NumericRule:
    """一个物理量字段的约束。`max_digits` / `decimal_places` 与库列一致。"""

    label: str
    #: 是否必须是整数（装箱数、起订量）
    integer: bool
    max_digits: int
    decimal_places: int
    #: 允许的最小值。物理量一律 0（负数没有意义）
    minimum: Decimal = Decimal(0)


#: 与 `product/model.py` 的列定义逐条对齐：
#: `length/width/height/weight` 是 `Numeric(12,3)`、`carton_volume` 是 `Numeric(12,4)`、
#: `carton_qty/moq` 是 `BigInteger`。
NUMERIC_RULES: dict[str, NumericRule] = {
    "length": NumericRule("长(mm)", integer=False, max_digits=12, decimal_places=3),
    "width": NumericRule("宽(mm)", integer=False, max_digits=12, decimal_places=3),
    "height": NumericRule("高(mm)", integer=False, max_digits=12, decimal_places=3),
    "weight": NumericRule("单重(kg)", integer=False, max_digits=12, decimal_places=3),
    "carton_volume": NumericRule("箱体积(m³)", integer=False, max_digits=12, decimal_places=4),
    "carton_qty": NumericRule("装箱数", integer=True, max_digits=12, decimal_places=0),
    "moq": NumericRule("起订量", integer=True, max_digits=12, decimal_places=0),
}


def parse_numeric_field(field: str, value: Any, *, where: str) -> Decimal | int | None:
    """按 `NUMERIC_RULES` 校验并返回归一后的值。

    `where` 只影响报错措辞（"导入"还是"核定"），判据完全一样 —— 这正是共用它的意义。

    拒绝的情形（一律 422，不静默改值）：
    - 不是数字、或不是有限数（NaN / Infinity）；
    - 小于 0（物理量没有负数，"还没填"请传空值）；
    - 位数超出库列能放的范围（否则落库时是 500）；
    - 该是整数的字段给了小数（**不截断**）。
    """
    rule = NUMERIC_RULES.get(field)
    if rule is None:
        raise AppError(
            ErrorCode.PARAM_ERROR, f"字段 {field} 没有数值约束定义，不能写入", 422
        )
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError, AttributeError) as error:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"{where}：{rule.label}（{field}）需要一个数字，收到 {value!r}",
            422,
        ) from error
    if not number.is_finite():
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"{where}：{rule.label}（{field}）必须是有限数值，收到 {value!r}",
            422,
        )
    if number < rule.minimum:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"{where}：{rule.label}（{field}）不能是负数（收到 {number}）。"
            "要表示「还没填」请传空值，不要传负数",
            422,
        )
    if rule.integer:
        if number != number.to_integral_value():
            raise AppError(
                ErrorCode.PARAM_ERROR,
                f"{where}：{rule.label}（{field}）必须是整数，收到 {number}。"
                "系统不会替你四舍五入 —— 请改成整数再提交",
                422,
            )
        # 正指数也占整数位：1E+30 的系数只有一个数字，但实际是 31 位整数。
        # 先比较范围，再转 int，避免超大科学计数法绕过校验。
        if number >= Decimal(10) ** rule.max_digits:
            raise AppError(
                ErrorCode.PARAM_ERROR,
                f"{where}：{rule.label}（{field}）超出可保存范围（最多 {rule.max_digits} 位），"
                f"收到 {number}",
                422,
            )
        return int(number)
    # Numeric(p,s) 固定只有 p-s 个整数位，不能借用没填的小数位。
    # 尾随零不改变精度：7.7700 与 7.77 应接受同一结论。
    digits = list(number.as_tuple().digits)
    exponent = number.as_tuple().exponent
    while digits and digits[-1] == 0:
        digits.pop()
        exponent += 1
    fraction_digits = max(-exponent, 0) if digits else 0
    if (fraction_digits > rule.decimal_places
            or number >= Decimal(10) ** (rule.max_digits - rule.decimal_places)):
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"{where}：{rule.label}（{field}）最多 {rule.max_digits} 位（含 "
            f"{rule.decimal_places} 位小数），收到 {number}",
            422,
        )
    return number


def normalize_numeric(field: str, value: Any, *, where: str) -> Decimal | int | None:
    """核定路径用得更多的别名（语义同上，空值照样返回 None）。"""
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return parse_numeric_field(field, value, where=where)
