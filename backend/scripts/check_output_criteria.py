#!/usr/bin/env python3
"""静态检查：**同类判据有没有在所有出口都铺开**。

## 为什么要做这个（2026-10-14 复盘）

我这一轮审查报了 20 条缺陷，其中**至少 8 条**是同一个病程：

    **同一个判据在 N 个出口里只铺了 M 个。**

而且"正确写法"往往就在**同一个文件、甚至相邻一行**里 —— 例如：

  · `agent/tools.py` 的 `get_contact` 手写字典把手机号原样返回，而**同一个文件**
    的 `get_customer_overview` 早就调了 `contact_util.can_view_full_contact`；
  · `agent/insights.py` 的 `quote_draft` 逐行返回底价，而**同一个文件**的
    `pricing_analysis` 早就调了 `_sanitize_pricing_result`；
  · `followup_suggestion` 里的**商机**计数既无权限也无范围，而**紧邻上一行**的
    **任务**计数两道都有，注释还写着"原来这里不按负责人过滤会混进别人的"。

这类缺陷人工审查能发现，但**不该靠人工审查** —— 判据一旦成文，就该由 CI 守。

## 这里先落 3 条（误报率低的）

1. **联系方式出口**：任何 `return` 里出现 `contact.mobile/phone/email/wechat`
   的地方，必须已经过 `serialize_contact_masked` / `can_view_full_contact` /
   `mask_contact_fields` 三者之一。
2. **定价结果出口**：任何 `return` 里出现 `result["protection_price"]` /
   `minimum_price` / `standard_price` 的地方，必须已经过
   `_sanitize_pricing_result`。
3. **纸面日期**：任何对 `*_at` / `created_at` 调 `.strftime(` 的地方，
   必须已经过 `to_business(` / `today_business(` / `business_day` ——
   否则印出去的是 **UTC 日历日**（北京时间 00:00–08:00 会差一天）。

## 为什么用"函数体"而不是"逐行"判断

判据是"**这个出口有没有经过那道闸**"，而闸通常写在函数开头（有时在调用方）。
所以按**函数体**扫：只要函数体里出现过那道闸，就认为这个出口是过的。
调用方那一层看不到 —— **宁可漏报也不刷屏**（与 `check_model_attrs.py` 同一取舍），
漏掉的那些仍需人工核。

## 用法

    cd backend && PYTHONPATH=. .venv/bin/python scripts/check_output_criteria.py
"""

from __future__ import annotations

import pathlib
import re
import sys

APP = pathlib.Path(__file__).resolve().parents[1] / "app"


def functions(text: str) -> list[tuple[str, int, str]]:
    """(函数名, 起始行, 函数体)。用缩进无关的粗糙切分：从 def 到下一个同级 def。"""
    out: list[tuple[str, int, str]] = []
    lines = text.split("\n")
    marks: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        match = re.match(r"^(\s*)(?:async )?def (\w+)\(", line)
        if match:
            marks.append((index, match.group(2)))
    for position, (index, name) in enumerate(marks):
        end = marks[position + 1][0] if position + 1 < len(marks) else len(lines)
        out.append((name, index + 1, "\n".join(lines[index:end])))
    return out


#: 判据 ①：联系方式字段
CONTACT_FIELDS = re.compile(r"\bcontact\.(mobile|phone|email|wechat)\b")
CONTACT_GATES = ("serialize_contact_masked", "can_view_full_contact", "mask_contact_fields")

#: 判据 ②：定价结果里的成本派生字段
PRICE_FIELDS = re.compile(
    r"result\[[\'\"](protection_price|minimum_price|minimum_price_cny|standard_price)[\'\"]\]"
)
PRICE_GATES = ("_sanitize_pricing_result", "strip_cost_context", "can_view_floor", "price:manage")

#: 判据 ③：纸面日期必须换算到业务时区
DATE_FIELD = re.compile(r"\b\w*(?:_at|created_at|updated_at)\b")
DATE_GATES = ("to_business(", "today_business(", "business_day", "business_month(", "business_year(")

#: 已核实的例外：这些函数**故意**返回未脱敏的值，逐条写明为什么。
#: 登记在这里而不是删判据 —— 判据要拦住的是"新写的出口忘了过闸"。
EXCEPTIONS: dict[tuple[str, str], str] = {
    ("app/modules/customer/service.py", "serialize_contact"): (
        "**内部口径**序列化器：审计 before/after 与写库后的返回值拼装都用它，"
        "审计必须留完整值（'谁把电话改成了什么'要查得出来）。"
        "它对外的脱敏版本是同文件的 `serialize_contact_masked`。"
    ),
    ("app/modules/search/router.py", "_serialize_contact"): (
        "调用方 `search_contacts` 已按 `contact_util.full_contact_customer_ids` "
        "决定是否 mask（闸在调用方那一层，本函数只做拼装）。"
    ),
    ("app/modules/contact_util.py", "find_duplicate_contacts"): (
        "撞单查重要求**按完整手机号比对** —— 脱敏了就查不出重复。"
        "结果只给有 `customer:merge` 权限的人，且返回体里带 `masked` 标记。"
    ),
    ("app/modules/quote/service.py", "build_item_snapshot"): (
        "这是**落库**不是出口：把 `minimum_price_cny`（人民币口径）冻结进快照，"
        "审批要按它判定。快照字段对无 `price:manage` 者的隐藏发生在 "
        "`_COST_ONLY_ITEM_FIELDS`（序列化层）。"
    ),
}


RULES = (
    ("联系方式出口未经脱敏", CONTACT_FIELDS, CONTACT_GATES,
     "把 contact.mobile/phone/email/wechat 直接放进返回体"),
    ("定价结果未经脱敏", PRICE_FIELDS, PRICE_GATES,
     "把 protection_price / minimum_price / standard_price 直接返回"),
    ("纸面日期用 UTC 日历日", re.compile(r"\b\w*(?:_at|created_at)\.strftime\("), DATE_GATES,
     "对 UTC 时间戳直接 strftime，北京时间 00:00–08:00 会印成前一天"),
)


def main() -> int:
    findings: list[str] = []
    scanned = 0
    for path in sorted(APP.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for name, line, body in functions(text):
            scanned += 1
            for label, trigger, gates, _desc in RULES:
                if not trigger.search(body):
                    continue
                if any(gate in body for gate in gates):
                    continue
                rel = path.relative_to(APP.parent)
                if (str(rel), name) in EXCEPTIONS:
                    continue
                findings.append(f"  ✗ [{label}] {rel}:{line} {name}()")

    print(f"输出判据自检：扫了 {scanned} 个函数，命中 {len(findings)} 处")
    if not findings:
        print("✓ 三类判据在每个出口都铺开了")
        return 0
    for item in findings:
        print(item)
    print()
    print("处置：这些出口可能把**内部数据原样发出去**（成本派生值 / 联系方式 / UTC 日期）。")
    print("若确认已经过闸（例如闸在调用方那一层），把闸名写进该函数体或在此登记例外 ——")
    print("不要为了让 CI 变绿而把判据删掉：这条检查存在的理由见文件头。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
