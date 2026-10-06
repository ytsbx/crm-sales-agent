"""对外单据模板的变量登记与填充（第八批 §8.8）。

为什么单独一层：模板正文里的 `{{...}}` 以前只有 `service._fill_tokens` 一处实现，
它认不出的 token **原样保留**（"宁可让人看见这里没填上"）。这个策略本身没错，
但**正式对外文件里印出模板语法**是事故：客户/工厂看到的是 `{{order.payment_terms}}`，
既不是条款也不是提示。所以这里把三件事分开、各自可测：

1. `analyze_template`：模板保存/预览时校验"引用的变量我们是否支持"
   （拼错 `{{customer.nmae}}`、引用不存在的来源都在这里被指出来）；
2. `resolve_tokens`：生成时真正填值，并**分别记录**"来源不支持"、"值未留存"、
   "值缺失"三种情况——不是所有填不出来都等于"模板写错了"；
3. `find_unresolved_syntax`：兜底扫一遍结果里还有没有 `{{`，
   有就不许作为有效对外文件出图（正式 Excel/PDF 不得印出模板语法）。

变量清单是**白名单**，不是"ORM 对象上有什么就能填什么"：客户对象上还有等级、
欠款、成本等内部字段，模板作者随口写一个就能把它们印到对客文件上。
白名单同时给出错误文案里可用的字段名，让人能照着自己改。
"""

import re
from datetime import UTC, datetime
from typing import Any, Mapping

#: 模板语法。与 `_fill_tokens` 的既有实现保持同一个正则（含 `{{ a.b }}` 里的空格）。
TOKEN_PATTERN = re.compile(r"\{\{([^}]+)\}\}")

#: 可引用的对象来源 → 允许字段 → 字段中文名（用于错误文案，让人知道该填什么）。
#: 新增字段时**两边一起加**：service 提供的 source 与这里的白名单。
SOURCE_FIELDS: dict[str, dict[str, str]] = {
    "customer": {
        "name": "客户名称",
        "level": "客户等级",
        "phone": "客户电话",
        "address": "客户地址",
    },
    "order": {
        "order_no": "订单编号",
        "currency": "币种",
        "payment_terms": "付款条件",
        "delivery_date": "客户交期",
        "remark": "订单备注",
    },
}

#: 固定来源：不依赖任何业务对象，永远填得出。
#: `today` 是生成日期；`extra.*` 的键由调用方自由提供（合法 extra 字段），
#: 因此不列入 `SOURCE_FIELDS`，只在这里登记名前缀。
FIXED_SOURCES = {"today"}
EXTRA_PREFIX = "extra."

#: 填不出来的三种原因。分开记是因为**处置方式不同**：
#: - source_missing：这次生成根本没有这个来源（订单草稿引用正式订单字段）→ 模板或入口选错了；
#: - value_missing：来源在，但这个字段是空的（如订单没填付款条件）→ 去业务单据补资料；
#: - unsupported：字段名不在白名单（拼错、或引用了内部字段）→ 改模板。
REASON_SOURCE_MISSING = "source_missing"
REASON_VALUE_MISSING = "value_missing"
REASON_UNSUPPORTED = "unsupported"

REASON_LABEL = {
    REASON_SOURCE_MISSING: "来源不可用",
    REASON_VALUE_MISSING: "值缺失",
    REASON_UNSUPPORTED: "不支持的变量",
}


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def split_token(token: str) -> tuple[str, str]:
    """把 `customer.name` 拆成 (来源, 字段)；`today` 这类没有点号的字段为空。"""
    prefix, _, field = token.partition(".")
    return prefix.strip(), field.strip()


def is_known_token(token: str) -> bool:
    """这个变量的**写法**是否受支持（不看本次生成有没有值）。"""
    token = (token or "").strip()
    if not token:
        return False
    if token in FIXED_SOURCES:
        return True
    if token.startswith(EXTRA_PREFIX):
        # 自定义字段的键是调用方定义的，只校验前缀不为空
        return bool(token[len(EXTRA_PREFIX):].strip())
    prefix, field = split_token(token)
    if not field:
        return False
    return field in SOURCE_FIELDS.get(prefix, {})


def analyze_template(body: str) -> dict:
    """模板保存/预览用：列出用到的变量，并指出写法不受支持的。

    返回：
      - `variables`：出现过的变量名（保序去重）；
      - `unsupported`：[{token, reason}]，写法不受支持的那些；
      - `available`：本类型模板**可以**引用的变量清单（给作者照着改）。
    """
    tokens: list[str] = []
    unsupported: list[dict] = []
    for match in TOKEN_PATTERN.finditer(body or ""):
        token = match.group(1).strip()
        if token in tokens:
            continue
        tokens.append(token)
        if not is_known_token(token):
            unsupported.append(
                {
                    "token": token,
                    "reason": REASON_UNSUPPORTED,
                    "message": (
                        f"变量 {{{{{token}}}}} 不受支持；"
                        f"{_token_hint(token)}"
                    ),
                }
            )
    return {
        "variables": tokens,
        "unsupported": unsupported,
        "available": available_tokens(),
    }


def available_tokens() -> list[str]:
    """当前支持的变量清单（含字段中文名，供模板作者对照）。"""
    items = [f"{{{{{name}}}}}" for name in sorted(FIXED_SOURCES)]
    for source, fields in SOURCE_FIELDS.items():
        items.extend(
            f"{{{{{source}.{field}}}}}（{label}）" for field, label in fields.items()
        )
    items.append(f"{{{{{EXTRA_PREFIX}自定义键}}}}（调用方传入的自定义字段）")
    return items


def _token_hint(token: str) -> str:
    prefix, field = split_token(token)
    if token.startswith(EXTRA_PREFIX):
        return "自定义字段的写法是 extra.键名，键名不能为空"
    if prefix in SOURCE_FIELDS:
        allowed = "、".join(f"{prefix}.{name}" for name in SOURCE_FIELDS[prefix])
        return f"{prefix} 只支持：{allowed}"
    known = "、".join(sorted(SOURCE_FIELDS)) or "（无）"
    return f"不认识的来源「{prefix}」；当前支持的来源：{known}、{EXTRA_PREFIX}键名、today"


def resolve_tokens(
    body: str,
    sources: Mapping[str, Any] | None,
    extra: Mapping[str, Any] | None = None,
) -> tuple[str, list[dict]]:
    """填充模板正文，返回 (正文, 未解析变量列表)。

    **未解析的 token 不再原样保留**：换成 `（未解析变量：来源不可用 xxx）` 这类
    明确标记。理由有三个，缺一不可：
    - 正式对外文件（Excel/PDF）不许印出 `{{...}}` 模板语法；
    - 标记里带上**原因**，读的人知道该去补资料还是改模板；
    - 标记里带上变量名，用户看得到自己该改什么。

    只有 `extra.*`、`customer.*`、`order.*`、`today` 会被取值；其他写法一律
    记成 `unsupported`（与保存时的校验同一判据，不出现"存得下、生不成"）。
    """
    sources = sources or {}
    extra = extra or {}
    unresolved: list[dict] = []

    def _replace(match: "re.Match[str]") -> str:
        token = match.group(1).strip()
        if token == "today":
            return datetime.now(UTC).date().isoformat()
        if token.startswith(EXTRA_PREFIX):
            key = token[len(EXTRA_PREFIX):].strip()
            if key in extra and extra[key] is not None:
                return _text(extra[key])
            return _mark(token, REASON_VALUE_MISSING, unresolved)
        prefix, field = split_token(token)
        allowed = SOURCE_FIELDS.get(prefix)
        if not allowed or not field or field not in allowed:
            return _mark(token, REASON_UNSUPPORTED, unresolved)
        obj = sources.get(prefix)
        if obj is None:
            return _mark(token, REASON_SOURCE_MISSING, unresolved)
        # 来源对象的字段直读：白名单已经挡住了成本、欠款这类内部字段
        value = obj.get(field) if isinstance(obj, Mapping) else getattr(obj, field, None)
        if value is None:
            return _mark(token, REASON_VALUE_MISSING, unresolved)
        return _text(value)

    return TOKEN_PATTERN.sub(_replace, body or ""), unresolved


def _mark(token: str, reason: str, bucket: list[dict]) -> str:
    """记一条未解析，并给出不会被人误读成条款的替代文案。

    文案要同时说清三件事（§8.8 的验收是"用户可理解并修正错误"）：
    - 这是**未解析变量**，不是业务上真的写了这几个字；
    - 为什么填不出来（来源不可用 / 值缺失 / 不支持的变量——处置方式完全不同）；
    - 是哪个变量，好回模板里找到那一行。
    此前只写「（值缺失：xxx）」，读的人未必意识到这是系统占位符而不是条款文字。
    """
    bucket.append(
        {
            "token": token,
            "reason": reason,
            "label": REASON_LABEL.get(reason, reason),
            "message": unresolved_message(token, reason),
        }
    )
    return f"（未解析变量：{REASON_LABEL.get(reason, reason)} {token}）"


def unresolved_message(token: str, reason: str) -> str:
    if reason == REASON_SOURCE_MISSING:
        prefix, _ = split_token(token)
        return f"本次生成没有「{prefix}」来源，变量 {{{{{token}}}}} 填不出值"
    if reason == REASON_VALUE_MISSING:
        return f"变量 {{{{{token}}}}} 的来源存在，但该字段当前没有值"
    return f"变量 {{{{{token}}}}} 不受支持；{_token_hint(token)}"


def find_unresolved_syntax(text: str) -> list[str]:
    """结果里残留的模板语法（兜底检查：正式文件不得印出 `{{...}}`）。"""
    return [m.group(0) for m in TOKEN_PATTERN.finditer(text or "")]
