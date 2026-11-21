#!/usr/bin/env python3
"""静态检查：**算钱的下游必须拒外币**（主人 2026-10-12 的口径）。

## 背景

主人的原话：「只会在国内，没有外币这个东西，不要考虑这个，只要确保不会有人
可以用外币这个东西钻空子就行，有人用外币就给他报错」。

所以外币的处理方式是**不做折算、见到就报错**。危险不在入口（入口有
`ensure_currency_allowed` 那道闸），而在**下游算钱的地方**：
第四批审查实测 —— `concession_amount`（「较上一版让价金额」，单位「元」）
直接相减两个版本的 `total_amount`，外币单上算出来的是**外币差额**，
却被当成人民币去匹配"让价 ≤ 1000 元走极速通道"，本该人工审的单子被免审。

## 这份检查守什么

`rules_engine.build_context` 是**把单据翻译成条件上下文**的唯一入口，
它算出来的每个金额字段都会直接参与规则匹配。所以：

  它必须调用 `reject_foreign_currency`（或 `is_foreign` 判断），
  否则外币金额会被当成人民币。

## 为什么用静态检查而不是单测

单测只能证明"闸函数本身好使"，证明不了"它被接在了这条计算上"——
反向验证时撤掉调用，单测照样全绿（实测踩过）。这条接线只能靠对源码断言。

跑法：`python3 ops/check_foreign_currency_rejected.py`
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TARGET = REPO / 'backend' / 'app' / 'modules' / 'approval' / 'rules_engine.py'

#: 会算金额、并拿去匹配规则的函数（新增这类函数时同步加进来）
MONEY_CONTEXT_FUNCS = ('build_context',)

#: 认可的做法：拒外币，或者显式判断是不是外币
GUARD = re.compile(r'\b(reject_foreign_currency|is_foreign)\s*\(')


def enclosing_function(lines: list[str], name: str) -> str | None:
    """抠出某个函数的函数体（到下一个顶格 def 为止）。"""
    start = None
    for index, line in enumerate(lines):
        if re.match(rf'\s*(async )?def {name}\(', line):
            start = index
            break
    if start is None:
        return None
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if re.match(r'(async )?def \w+\(', lines[index]):
            end = index
            break
    return '\n'.join(lines[start:end])


def main() -> int:
    lines = TARGET.read_text(encoding='utf-8').splitlines()
    problems: list[str] = []
    for name in MONEY_CONTEXT_FUNCS:
        body = enclosing_function(lines, name)
        if body is None:
            problems.append(f'找不到函数 {name}（改名了？同步改这份检查）')
            continue
        if GUARD.search(body):
            print(f'  ✓ {name}：算金额的上下文里拒了外币')
        else:
            problems.append(
                f'{name} 里没有拒外币 —— 外币金额会被当成人民币参与规则匹配'
            )

    print()
    print(f'核对 {TARGET.relative_to(REPO)}：算钱上下文函数 {len(MONEY_CONTEXT_FUNCS)} 个')
    if problems:
        print()
        for item in problems:
            print(f'  ✗ {item}')
        print()
        print('  改法：在这个函数里调用 `reject_foreign_currency(version.currency, ...)`，')
        print('  或先 `is_foreign(...)` 判断再决定。**不要**去乘汇率折算 ——')
        print('  主人的口径是"没有外币这个东西，见到就报错"。')
        return 1
    print('✓ 算金额的上下文都拒了外币')
    return 0


if __name__ == '__main__':
    sys.exit(main())
