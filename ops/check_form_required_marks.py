#!/usr/bin/env python3
"""静态检查：**"必填"这件事有没有标清楚**（主人 2026-10-13 定）。

## 起因（主人原话）

    截图：案例弹窗里「可复用做法」下面一行红字
    「可复用做法 与「关键动作」至少填一项」
    主人：「这个必填的怎么没有标注 * 号，之前不是修过吗」

查下来：那一处是**二选一**（提交审核时「关键动作」或「可复用做法」至少一项，
`03-API` 设计文档写的是"不是各自必填"），2026-10-06 **故意**去掉了星号
（打实心星是撒谎：看起来两项都必填、而且存草稿就要填）。
但只留一行灰色小字又不显眼 —— 于是主人的结论是"必填的没标星号"。
现在补第三档**空心灰星**（`FormLabel` 的 `conditionallyRequired`）。

## 这份检查守什么

 ① `conditionallyRequired` **必须**配 `hint`：标了星却不说条件，
    用户还是不知道什么时候要填。
 ② `hint` 里写明"至少 / 二选一 / 任一 / 至少填一项"这类**条件必填**规则的字段，
    **必须**标 `conditionallyRequired` —— 这正是这次漏掉的那种
    （判据来自主人的实际反应：光有灰字，扫一眼以为没标）。
 ③ `required` 与 `conditionallyRequired` **不能同时出现**：
    一个字段不能既"现在就必填"又"有条件下必填"，那是自相矛盾。

用法：
    python3 ops/check_form_required_marks.py          # 有问题退出码 1（CI 用）
    python3 ops/check_form_required_marks.py --list    # 只列出，不判失败
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / 'frontend' / 'src'

#: hint 里出现这些词 = 这条规则是"条件必填"，必须标 `conditionallyRequired`
CONDITIONAL_WORDS = ('至少填一项', '至少填', '二选一', '任一', '其一', '至少一项')

#: 允许不标的情形（每条都要写明理由）
EXEMPT: dict[str, str] = {}


def form_labels(text: str) -> list[tuple[int, str]]:
    """抠出每个 `<FormLabel …>` 的开标签（含跨行），返回 (行号, 标签文本)。"""
    found: list[tuple[int, str]] = []
    for match in re.finditer(r'<FormLabel\b', text):
        # 从 `<FormLabel` 往后扫到**不在引号里**的第一个 `>`：属性值里可能出现
        # `>`（现在没有，但不能假设没有）
        index = match.end()
        quote: str | None = None
        while index < len(text):
            char = text[index]
            if quote:
                if char == quote:
                    quote = None
            elif char in '"\'':
                quote = char
            elif char == '>':
                break
            index += 1
        tag = text[match.start() : index + 1]
        line = text[: match.start()].count('\n') + 1
        found.append((line, tag))
    return found


def main() -> int:
    list_only = '--list' in sys.argv
    problems: list[str] = []
    checked = 0
    marked = 0

    for path in sorted(SRC.rglob('*.tsx')):
        text = path.read_text(encoding='utf-8')
        relative = path.relative_to(REPO)
        for line, tag in form_labels(text):
            checked += 1
            has_required = bool(re.search(r'(?<![\w.])required(?![\w])', tag))
            has_conditional = 'conditionallyRequired' in tag
            hint_match = re.search(r'hint=(?:"([^"]*)"|\{`([^`]*)`\})', tag)
            hint = (hint_match.group(1) or hint_match.group(2)) if hint_match else ''

            if has_conditional:
                marked += 1
                key = f'{relative}:{line}'
                if key in EXEMPT:
                    continue
                if not hint:
                    problems.append(
                        f'{relative}:{line} 标了 conditionallyRequired 却没写 hint —— '
                        '用户不知道"什么条件下必填"（标了星却不说条件，等于没标）'
                    )
            if has_required and has_conditional:
                problems.append(
                    f'{relative}:{line} 同时标了 required 与 conditionallyRequired —— '
                    '一个是"现在就必填"、一个是"有条件下必填"，不能并存'
                )
            if not has_conditional and hint:
                word = next((w for w in CONDITIONAL_WORDS if w in hint), None)
                if word:
                    problems.append(
                        f'{relative}:{line} 的 hint 写着「{word}」这种条件必填规则，'
                        f'但字段没标 conditionallyRequired —— 扫一眼会以为没标\n'
                        f'        现在：hint="{hint}"\n'
                        f'        应改成：<FormLabel conditionallyRequired hint="…">…</FormLabel>'
                    )

    print(f'核对：{checked} 个 FormLabel，其中 {marked} 个标了条件必填')
    if problems and not list_only:
        print()
        for item in problems:
            print(f'  ✗ {item}')
        return 1
    if problems:
        for item in problems:
            print(f'  · {item}')
    else:
        print('✓ 必填/条件必填都标清楚了，且没有自相矛盾的标法')
    return 0


if __name__ == '__main__':
    sys.exit(main())
