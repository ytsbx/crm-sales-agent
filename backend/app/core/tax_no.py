"""统一社会信用代码（GB 32100-2015）：规范化与校验。

为什么单独一层：税号在系统里被当"公司身份证号"用——查重、合并冲突判断、
ERP 回款自动认客户都靠它。一条录错的税号会让回款认错客户、让同一家公司
永远合并不上，所以写入前必须按国标校验，比较前必须用同一套规范化。
"""
import unicodedata

#: 国标字符集：0-9 与 A-Z，去掉易混淆的 I、O、S、V、Z，共 31 个。
CHARSET = "0123456789ABCDEFGHJKLMNPQRTUWXY"
#: 前 17 位的加权因子：W(i) = 3^(i-1) mod 31（1、3、9、27、19、26、16、17、20、29、25、13、8、24、10、30、28）。
WEIGHTS = (1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28)
LENGTH = 18


def normalize_tax_no(value: str | None) -> str | None:
    """去空格、全角转半角、统一大写；空值返回 None。"""
    if value is None:
        return None
    text = unicodedata.normalize("NFKC", str(value))
    text = "".join(text.split()).upper()
    return text or None


def _check_char(first17: str) -> str:
    total = sum(CHARSET.index(char) * WEIGHTS[index] for index, char in enumerate(first17))
    return CHARSET[(31 - total % 31) % 31]


def validate_tax_no(value: str | None) -> str | None:
    """校验并返回规范化后的税号；不合法抛 ValueError（文案给业务看）。"""
    text = normalize_tax_no(value)
    if text is None:
        return None
    if len(text) == 15 and text.isdigit():
        raise ValueError("15 位旧工商注册号不再受理，请填写营业执照上的 18 位统一社会信用代码")
    if len(text) != LENGTH:
        raise ValueError(f"统一社会信用代码应为 18 位，当前 {len(text)} 位，请对照营业执照核对")
    illegal = sorted({char for char in text if char not in CHARSET})
    if illegal:
        raise ValueError(
            "统一社会信用代码不能包含字符 " + "、".join(illegal)
            + "（国标不使用 I、O、S、V、Z，请对照营业执照核对）"
        )
    if _check_char(text[:17]) != text[17]:
        raise ValueError("统一社会信用代码校验位不通过，请对照营业执照核对是否抄错")
    return text
