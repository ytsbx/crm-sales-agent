#!/usr/bin/env python3
"""静态检查：**用户可见的文案里不许出现 Markdown 的 `**` 标记**。

背景（主人 2026-10-12 看「标记已发送」弹窗）：弹窗里印出来的是

    这是**发给客户**的记录：某某公司

`**` 是 Markdown 的加粗语法，**React 不会把它变成粗体，它会原样显示**。
这个项目的中文强调写法是「」（直角引号），源码里写 `**…**` 一律是给读代码的人看的，
**不能出现在会渲染出来的文本里**。

## 为什么必须用静态检查

这种错**不报错、不失败、类型也检查不出来**（`tsc` 全绿），只有人打开那一页才看得见。
我自己在写这个弹窗时就犯了，同一个文件里还犯了第二处
（`RoutePermissionGuard` 的"权限改完**刷新本页**即可生效"）——
靠人眼复核拦不住，所以钉成检查。

## 判据（只报"一定会渲染出来"的）

用一个**逐字符状态机**剥掉注释（`//` 与 `/* */`，并正确跳过字符串），
再看剩下的代码里还有没有 `**`：

- 注释里的 `**` 是**正常的**（这个项目的注释大量用 `**强调**`），必须放过；
- 字符串字面量里的 `**` **要报** —— 那种字符串基本都是文案
  （`placeholder="…"`、`Toast.success('…')`、`title={…}`）。
  这个项目里没有哪处"字符串里含 `**` 是必要的"，所以不做例外清单。

⚠️ 顺带说明为什么不查 `.ts`：中文文案都在 `.tsx` 的 JSX 里，
`.ts` 里出现的 `**` 基本都在注释中，扫它只会制造噪音。
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / 'frontend' / 'src'


def strip_comments(source: str) -> str:
    """把 `//` 与 `/* */` 注释抹成空白（保留换行），字符串原样保留。

    ## 为什么逐行处理，而不是整份文件跑字符状态机

    我第一版是整份跑状态机（遇到引号就进字符串态），**结果漏报了**：
    `RulesPanel.tsx` 里一个字符串起于第 696 行，而它下面的 `//` 注释中出现了
    引号字符 —— 状态机从此再也走不出字符串态，后面所有注释都不再被识别，
    于是这个检查**恰好漏掉了它该抓的那一行**。

    **逐字符状态机在"注释里出现引号"这件事上是原理性失效的。**
    改成按行扫：JS 的 `//` 本来就以行为界，整行是注释就直接抹掉，
    根本不去猜那行里的引号是什么 —— 这样两者互不干扰。

    代价：字符串跨行（模板字符串）时，第二行起会被当成代码。
    对这个检查是可接受的：那种行里出现 `**` 同样属于"会渲染出来的文案"，该报。
    """
    out_lines: list[str] = []
    in_block = False          # 在 /* ... */ 或 {/* ... */} 里
    for line in source.split('\n'):
        stripped = line.lstrip()
        if in_block:
            out_lines.append('')
            # JSX 注释的收尾是 `*/}`，普通块注释是 `*/` —— 两者都算收尾
            if '*/' in line:
                in_block = False
            continue
        # ⚠️ JSX 的 `{/* … */}` 也要算：它**不是行首**（前面有缩进的 `{`）。
        # 只判 `startswith('/*')` 会漏掉它 —— 而 JSX 里的说明注释大多是这种写法，
        # 漏掉就会把它当成"会渲染的文本"大批误报（我第一版报了 55 处，全是这个原因）。
        opener = None
        for candidate in ('{/*', '/*'):
            idx = line.find(candidate)
            # 只认"前面全是空白或只有 `{`"的，避免把 `a /* x */` 这种行尾注释误判
            if idx >= 0 and line[:idx].strip() in ('', '{'):
                opener = idx
                break
        if opener is not None:
            out_lines.append('')
            # 收尾可能在**同一行**（`{/* … */}` 或 `/* … */`）
            if '*/' not in line[opener + 2:]:
                in_block = True
            continue
        if stripped.startswith('//'):
            out_lines.append('')
            continue
        out_lines.append(line)
    return '\n'.join(out_lines)


def main() -> int:
    hits: list[str] = []
    scanned = 0
    for path in sorted(SRC.rglob('*.tsx')):
        scanned += 1
        original = path.read_text(encoding='utf-8')
        code = strip_comments(original)
        for lineno, (line, raw) in enumerate(
            zip(code.split('\n'), original.split('\n')), 1
        ):
            if '**' in line:
                hits.append(f'{path.relative_to(REPO)}:{lineno}  {raw.strip()[:100]}')

    print(f'扫描 {scanned} 个 .tsx 文件')
    if hits:
        print()
        print('这些 `**` 会**原样显示**给用户（React 不认 Markdown 加粗）。')
        print('中文强调请用「」（本项目的写法），或把加粗交给 <strong>：')
        for hit in hits:
            print(f'  ✗ {hit}')
        return 1
    print('✓ 用户可见的文案里没有 Markdown 的 `**` 标记')
    return 0


if __name__ == '__main__':
    sys.exit(main())
