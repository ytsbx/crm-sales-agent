"""客户查重的相似度打分。

设计取舍：不用机器学习，也不用模糊字符串库，而是**可解释的加权规则**——
每一分都能说清是从哪来的（名称完全一致？手机号一致？），
这样业务才敢用，也才能在界面上看到"疑似 91%"时知道为什么是 91%。

权重与阈值都从系统配置读（key: dedup_scoring），可调。
"""

from typing import Any

# 公司名里不影响主体的后缀，比较前先去掉
NAME_SUFFIXES = (
    "股份有限公司",
    "有限责任公司",
    "有限公司",
    "集团有限公司",
    "集团",
    "公司",
    "工厂",
    "厂",
)


def normalize_name(name: str | None) -> str:
    text = (name or "").strip().replace(" ", "").replace("　", "")
    for suffix in NAME_SUFFIXES:
        text = text.replace(suffix, "")
    return text.lower()


def longest_common_substring_ratio(a: str, b: str) -> float:
    """最长公共子串占较短串的比例，用来判断"像不像同一个名字"。

    比"编辑距离"更符合直觉：'宁波宏远包装制品' 与 '宁波宏远包装制品有限公司'
    去掉后缀后完全一致；而 '宁波宏远' 与 '苏州恒达' 的公共子串很短，得分自然低。
    """
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 1.0
    best = 0
    prev = [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                best = max(best, cur[j])
        prev = cur
    return best / min(len(a), len(b))


def similarity_score(
    *,
    left: dict[str, Any],
    right: dict[str, Any],
    weights: dict[str, Any] | None = None,
) -> tuple[int, list[str]]:
    """给两个客户实体打分，返回 (0~100 的分数, 命中原因)。"""
    w = weights or {}
    score = 0
    reasons: list[str] = []

    left_name = normalize_name(left.get("name"))
    right_name = normalize_name(right.get("name"))
    if left_name and right_name:
        if left_name == right_name:
            score += int(w.get("weight_name_exact", 60))
            reasons.append("企业名称一致")
        elif left_name in right_name or right_name in left_name:
            score += int(w.get("weight_name_contains", 45))
            reasons.append("企业名称互相包含")
        else:
            ratio = longest_common_substring_ratio(left_name, right_name)
            if ratio >= 0.6:
                score += int(int(w.get("weight_name_contains", 45)) * ratio)
                reasons.append(f"企业名称相似度 {int(ratio * 100)}%")

    for field, weight_key, label in (
        ("mobile", "weight_mobile", "手机号一致"),
        ("tax_no", "weight_tax_no", "统一社会信用代码一致"),
        ("domain", "weight_domain", "官网域名一致"),
        ("address", "weight_address", "地址一致"),
    ):
        lv = (left.get(field) or "").strip().lower()
        rv = (right.get(field) or "").strip().lower()
        if lv and rv and lv == rv:
            score += int(w.get(weight_key, 0))
            reasons.append(label)

    return min(score, 100), reasons
