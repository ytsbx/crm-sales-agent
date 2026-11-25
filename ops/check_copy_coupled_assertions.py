#!/usr/bin/env python3
"""静态检查：**测试脚本里"抠产品文案"的正则，不能和文案脱钩**。

## 缘起（一个查了半天的假故障）

`check_sixth_round_p2.py` 这么断言"哪几张打样单在保护名单里"：

    re.findall(r"打样单 #(\\d+)", "；".join(detail[customer_id]))

全站把 `#` 拼编号统一成「编号 N」之后，这个正则**永远返回空列表**，于是：

- "**应该**被保护"的两条断言 → 必红；
- "**不该**被保护"的那条 → **假绿**（空集合里当然没有它）。

表象是"实现坏了"——我一开始就是这么误判的，还去翻保护逻辑的判据；
实际是**断言读的文案已经不存在了**。这类问题最难的地方在于
「只改文案、没改实现却红」，看见红灯的人方向从一开始就错了。

## 判据（窄而准，宁可少报也不误报）

把正则里的**字面量片段**抠出来（去掉 `\\d{4}`、`(\\d+)`、`.+?` 这些模式部分），
每一段都要能在 `backend/app/**` 的源码里找到。找不到 → 报出来。

⚠️ **为什么只查字面量、不去"整条正则匹配产品文案"**（我两个方向都试过，都错）：

- 拿正则去匹配 app 的字面量：产品文案是 **f-string**（`f"打样单编号 {sample_id}…"`），
  占位符没展开就匹配不上 → **误报**（我差点又去改一处没坏的东西）；
- 拿正则去匹配产品的**渲染结果**：渲染要跑代码，而且像
  `记录时约定：\\d{4}-\\d{2}-\\d{2}` 这种断言的本来就是**输出格式**、
  不是文案本身 → 结构上做不到，还是**误报**。

**范围扩错的代价是噪音，噪音会让检查被忽略**（这个项目里有过教训）。
所以这里只回答一个能确定回答的问题：**这段中文还在不在产品代码里？**
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / 'backend' / 'scripts'
APP = REPO / 'backend' / 'app'

#: 正则里的"模式部分"：转义序列、字符类、量词、分组括号。抠掉它们剩下的就是字面量。
_PATTERN_BITS = re.compile(
    r'\\[a-zA-Z]'         # \d \s \w …
    r'|\\[0-9]+'          # \1 反向引用
    r'|\[[^\]]*\]'        # [abc] [^abc]
    r'|\{\d+(?:,\d*)?\}'  # {4} {2,3}
    r'|[()+*?^$|.]'       # 元字符
)
#: 字面量片段至少要这么长才值得检查（太短的说明不了问题）
_MIN_RUN = 2


def _app_source() -> str:
    """`backend/app/**` 的全部源码文本。

    这里用**整份源码**（不是只看字面量）：要回答的问题是
    "这段中文还在不在产品代码里"，注释里出现也算它还在 ——
    宁可少报，也不要把"文案搬到了注释里"当成脱钩。
    """
    return '\n'.join(
        path.read_text(encoding='utf-8') for path in sorted(APP.rglob('*.py'))
    )


def _literal_runs(pattern: str) -> list[str]:
    """把正则拆成字面量片段（去掉模式部分），返回含中文且长度达标的那些。"""
    stripped = _PATTERN_BITS.sub('\x00', pattern)
    runs = [part for part in stripped.split('\x00') if len(part) >= _MIN_RUN]
    # 只保留含中文的片段：纯 ASCII/标点的片段（"："、"（）"）说明不了脱钩
    return [r for r in runs if re.search(r'[\u4e00-\u9fff]', r)]


def main() -> int:
    app_src = _app_source()
    problems: list[str] = []
    scanned = 0
    checked = 0

    for path in sorted(SCRIPTS.glob('*.py')):
        scanned += 1
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not (isinstance(fn, ast.Attribute) and fn.attr in ('findall', 'search', 'match')):
                continue
            if not (isinstance(fn.value, ast.Name) and fn.value.id == 're'):
                continue
            if not node.args:
                continue
            first = node.args[0]
            if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                continue
            runs = _literal_runs(first.value)
            if not runs:
                continue
            checked += 1
            missing = [r for r in runs if r not in app_src]
            if missing:
                problems.append(
                    f'{path.relative_to(REPO)}:{node.lineno}  正则 r"{first.value}" 里的'
                    f'「{"」「".join(missing)}」在 backend/app 里已经找不到 —— '
                    f'文案改过了，这条断言会静默变成"永远匹配不到"'
                    f'（"不该出现"那一侧还会假绿）'
                )

    print(f'扫描 {scanned} 个测试脚本，检查了 {checked} 处"抠中文文案"的正则')
    if problems:
        print()
        print('这些断言可能已经和产品文案脱钩了：')
        for item in problems:
            print(f'  ✗ {item}')
        return 1
    print('✓ 没有发现与产品文案脱钩的断言')
    return 0


if __name__ == '__main__':
    sys.exit(main())
