"""Decimal 精确 JSON 出口（float→Decimal 专项 · 批1，2026-11-24）。

背景（本机实测）：FastAPI 默认 `jsonable_encoder` 会把 Decimal 转成 float，
`Decimal('99999999999999.99')` 在出口变成 `99999999999999.98`，差一分钱。
service 层全程保留 Decimal 也没用——最后一跳照样被吃掉。所以出口必须一起改。

这里做两件事（不引入新依赖）：

1. 把 Decimal 注册进 `fastapi.encoders.ENCODERS_BY_TYPE` 为「原样透传」。
   `jsonable_encoder` 按精确类型查这个注册表找转换函数（见其源码
   `if type(obj) in ENCODERS_BY_TYPE` 分支），注册后 Decimal 原样通过，
   不再被偷偷转 float。这是 FastAPI 的类型编码注册点，不是私有 API  hack；
   回归 `tests/test_decimal_json_exit.py` 把该行为钉死，FastAPI 升级若改动
   这条路径，测试会立刻红，不会静默回退。
2. `DecimalJSONResponse` 把 Decimal 按原值渲染成**精确 JSON 数字字面量**。
   JSON 报文本来就是文本，数字直接写出去一位不丢；接口契约仍是数字，
   前端零改动。字符串转义仍交给标准库 `json.dumps`，只自渲染数字与容器，
   避免手写转义漏掉引号/控制字符这类坑。

边界：只处理接口响应里会出现的类型（dict/list/tuple/str/int/float/Decimal/
bool/None）。datetime/date/UUID/set 等仍由 `jsonable_encoder` 先转成字符串/
列表，到不了这里。NaN/Infinity 显式拒绝（对齐标准库 `allow_nan=False`），
不让非法数字上线。

注意：浏览器 JS 把 JSON 数字解析成 Number 时 15-16 位以上仍会丢精度，那是
前端既有上限；编辑/保存往返的接口另补 `*_exact` 字符串字段供前端精确读回
（见 review/FLOAT-REFACTOR.md），本模块只保证后端与线路上一位不差。
"""

import json
import math
from decimal import Decimal
from typing import Any

from fastapi.encoders import ENCODERS_BY_TYPE
from fastapi.responses import JSONResponse


def _decimal_passthrough(value: Decimal) -> Decimal:
    """注册用恒等函数：Decimal 原样通过 jsonable_encoder，不被转 float。"""
    return value


# 模块导入即注册：main 与任何导入本模块的测试同时生效（幂等）。
ENCODERS_BY_TYPE[Decimal] = _decimal_passthrough


def _render(value: Any, out: list[str]) -> None:
    """把单个值渲染成 JSON 文本片段，追加进 out。"""
    if value is None:
        out.append("null")
    elif value is True:  # bool 是 int 子类，必须先于 int 判断
        out.append("true")
    elif value is False:
        out.append("false")
    elif isinstance(value, str):
        out.append(json.dumps(value, ensure_ascii=False))
    elif isinstance(value, int):
        out.append(str(value))
    elif isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError(f"非有限金额不能出 JSON 出口: {value!r}")
        # format(..., 'f') 按十进制原值展开，不走科学计数、不经过 float。
        out.append(format(value, "f"))
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"非有限数字不能出 JSON 出口: {value!r}")
        out.append(repr(value))
    elif isinstance(value, dict):
        out.append("{")
        first = True
        for key, item in value.items():
            if not first:
                out.append(",")
            first = False
            # JSON 键必须是字符串；非 str 键对齐 json.dumps 的字符串化行为。
            if isinstance(key, str):
                out.append(json.dumps(key, ensure_ascii=False))
            elif key is None:
                out.append('"null"')
            elif key is True:
                out.append('"true"')
            elif key is False:
                out.append('"false"')
            elif isinstance(key, (int, float, Decimal)):
                out.append(json.dumps(str(key), ensure_ascii=False))
            else:
                raise TypeError(f"JSON 出口不支持的键类型: {type(key)!r}")
            out.append(":")
            _render(item, out)
        out.append("}")
    elif isinstance(value, (list, tuple)):
        out.append("[")
        for index, item in enumerate(value):
            if index:
                out.append(",")
            _render(item, out)
        out.append("]")
    else:
        raise TypeError(f"JSON 出口不支持的类型: {type(value)!r}")


def exact_json_dumps(content: Any) -> str:
    """渲染整棵响应树为紧凑 JSON 文本（分隔符与 JSONResponse 默认一致）。"""
    out: list[str] = []
    _render(content, out)
    return "".join(out)


class DecimalJSONResponse(JSONResponse):
    """按 Decimal 原值渲染的 JSON 响应；其余类型行为与 JSONResponse 一致。"""

    def render(self, content: Any) -> bytes:
        return exact_json_dumps(content).encode("utf-8")