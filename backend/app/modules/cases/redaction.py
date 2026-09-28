"""案例分享版脱敏（CRM 完整实现方案 §3.7/场景15，与 §权限 :161 同一口径）。

文档原话：「案例分享版对**客户电话、合同、成本、特殊价**等做权限控制或
脱敏；原单据仍按业务权限访问。」同一份文档的权限章又要求「前端隐藏、后端
接口、Excel/PDF 导出、通知内容保持同一权限口径」——所以脱敏只能落在后端
序列化层，靠前端不显示不算数（换个接口/直接调 API 就绕过去了）。

对应文档那句里的「或」，这里两条都做：

1. **正文文本脱敏**：案例正文是自由文本，销售顺手就会把手机号、"8.5 元"、
   "成本 5 元"、"95 折"写进去。分享版把这些**片段**换成 〔金额〕〔手机号〕
   这类占位，而不是整段隐藏——培训要学的是做法，抹掉数字后叙述仍然成立。
2. **单据引用权限控制**：案例只存外部单据 id（quote_id/order_id/sample_id/
   opportunity_id）。对没有对应查看权限的读者不下发这些 id，这是「原单据
   仍按业务权限访问」的落地：不给指针，就不会绕开单据权限点进去。

为什么不按字段整段藏：案例的价值在"怎么谈成的"，把"异议处理"整段藏掉，
案例库就退化成标题列表；只抹数字既满足脱敏又保住培训价值。
"""

import re

#: 参与脱敏的正文段落（案例模型里的全部叙述字段）
NARRATIVE_FIELDS = (
    "background",
    "goal",
    "key_actions",
    "objection_handling",
    "process",
    "result",
    "lessons",
)

#: 单据引用 → 打开该单据所需权限（与 seed.py 的权限码一致）
EVIDENCE_PERMISSIONS: dict[str, str] = {
    "quote_id": "quote:view",
    "order_id": "order:view",
    "sample_id": "sample:view",
    "opportunity_id": "opportunity:view",
}

#: 脱敏规则。顺序有意义：先抹"带单位的金额/折扣"，再抹"价格词后面的数字"——
#: 反过来会出现「成本 〔金额〕元」这种半截替换。
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # 客户联系方式（§3.7 点名第一类）
    ("手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("座机", re.compile(r"(?<!\d)0\d{2,3}[-\s]?\d{7,8}(?!\d)")),
    ("邮箱", re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}")),
    # 合同（§3.7 第二类）：只在"合同/协议"字样附近才认，避免把 SKU 编码抹掉
    (
        "合同编号",
        re.compile(
            r"(?:合同|协议)\s*(?:编号|号|NO\.?|No\.?)?\s*[:：]?\s*[A-Za-z]{2,8}[-_]?\d{4,}"
        ),
    ),
    ("折扣", re.compile(r"\d+(?:\.\d+)?\s*折")),
    # 成本与特殊价（§3.7 第三、四类）：带币种符号或单位的金额
    (
        "金额",
        re.compile(
            r"(?:[¥￥]\s*\d[\d,]*(?:\.\d+)?"
            r"|\d[\d,]*(?:\.\d+)?\s*(?:万元|万|元|美元|美金|RMB|USD|CNY))",
            re.IGNORECASE,
        ),
    ),
    # 价格词后面裸跟数字的形式（"单价 8.5"、"成本 5"、"毛利 30%"）
    (
        "价格",
        re.compile(
            r"(?:单价|报价|价格|成本价?|底价|保护价|特殊价|专属价|毛利|净利|利润|利润率)"
            r"\s*(?:为|是|约|在|:|：)?\s*\d[\d,]*(?:\.\d+)?\s*(?:%|％)?"
        ),
    ),
)


def mask_text(text: str | None) -> tuple[str | None, dict[str, int]]:
    """抹掉正文里的敏感片段，返回（脱敏后文本, 命中统计）。

    统计而不是布尔：审核人要看到"这条案例分享出去会被抹掉 2 处金额、1 处
    手机号"这种可核对的信息，才知道该不该先改正文再发布。
    """
    if not text:
        return text, {}
    masked = text
    counters: dict[str, int] = {}
    for label, pattern in _PATTERNS:
        masked, count = pattern.subn(f"〔{label}〕", masked)
        if count:
            counters[label] = counters.get(label, 0) + count
    return masked, counters


def summarize(counters: dict[str, int]) -> list[str]:
    """把命中统计整理成给界面显示的短句清单。"""
    return [f"{label} {count} 处" for label, count in sorted(counters.items())]


def evidence_scope_for(user) -> set[str]:
    """读者能拿到哪些单据引用 id。

    管理员默认全给（与 require_permission 的"管理员默认放行"同一口径）；
    其余按单据模块的查看权限逐个判定——案例只是入口，权限边界仍由单据模块决定。
    """
    if "admin" in user.roles:
        return set(EVIDENCE_PERMISSIONS)
    return {
        field
        for field, permission in EVIDENCE_PERMISSIONS.items()
        if user.has(permission)
    }
