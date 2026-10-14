#!/usr/bin/env python3
"""前端自检：**不要靠切 ISO 串来显示时间**。

## 为什么要有这条检查

后端下发的时间是**带 UTC 偏移的瞬时**（`2026-10-14T13:15:00+00:00`）。
把它当字符串切一刀（`.slice(0, 10)` / `.slice(0, 16)` / 配上 `.replace('T', ' ')`）
看起来"格式化好了"，实际是**按 UTC 显示**：

- 北京时间比 UTC 早 8 小时 → 时间列差 8 小时；
- 跨零点时**整个日期差一天** —— 而"约定下次跟进""签署时间""发送时间"
  这类字段恰恰是**用户据以行动的**（凌晨签的合同显示成昨天，
  工作台提示的"约定下次跟进"比实际早一天）。

这不是个别手滑：2026-10-14 全项目并行审查一次抓到 **13 处**，
分布在 quote / contract / order / cases / sample / workbench / bizdoc / product，
而且**同一页里往往两种口径并存**（一处 `toLocaleString`、一处切串），
用户看到的是自相矛盾的两个时间。

项目既有的正确写法有 90 处：`new Date(v).toLocaleString('zh-CN')` /
`toLocaleDateString('zh-CN')`；只取日期时用 `shared/calendar-day.ts` 的 `calendarDay`。

## 判据

扫 `frontend/src` 下所有 `.ts/.tsx`，命中"切片 + 时间语境"即报：
1. `.slice(0, 10)` / `.slice(0,10)` / `.slice(0, 16)` / `.slice(0, 19)`
   **且**同一行含 `replace('T'` 或 `.slice` 的目标看起来是时间（含 T 分隔符）；
2. `toISOString().slice(0, 10)`（UTC 取日，必错）。

例外要写进下面的 `EXEMPT` 并说明理由 —— 不要为了变绿删判据。
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / 'frontend' / 'src'

#: `{相对路径: 为什么这里可以切 ISO 串}`
EXEMPT: dict[str, str] = {
    # 时间解析**失败时**的兜底显示：拿不到合法 Date 才退化成原串，不是显示路径
    'modules/settings/SettingsPage.tsx': '解析失败时的兜底，正常路径走 toLocale*',
    # 注释里提到这个写法（判据自身的说明）
    'shared/calendar-day.ts': '文件头就是在解释为什么不能这么写',
}

#: `xxx.slice(0, 10/16/19)` —— 但**接收方必须是时间形态的标识符**
#: （`_at` / `At` / `_time` / `.at`），否则就是数组截断（`rows.slice(0, 10)`）。
#:
#: ⚠️ 这里返工过两次，记下来免得再踩：
#:   第一版 TIME_HINT 里含 `slice(...)`，等于"有 slice 就报" → 误报 `rows.slice(0, 10)`；
#:   第二版要求"取值形态"（`词.词(`），结果 `event.at?.slice(...)` 因为中间有 `?.`
#:   又漏了。最终改成**直接看接收方名字**，与调用形态无关。
TIME_SLICE = re.compile(
    r"([A-Za-z_]\w*(?:\s*\??\.\s*[A-Za-z_]\w*)*)\s*\??\.\s*slice\(\s*0\s*,\s*(?:10|16|19)\s*\)"
)
#: `String(row.next_followup_at).slice(0, 10)` —— 被包了一层函数调用。
#: 这种形态接收方名字不以 `_at` 结尾，得单独认。
WRAPPED_TIME_SLICE = re.compile(
    r"[A-Za-z_]\w*\(\s*[^)]*?_at\b[^)]*?\)\s*\??\.\s*slice\(\s*0\s*,\s*(?:10|16|19)\s*\)"
)
#: `toISOString().slice(0,10)` —— UTC 取日，必错
ISO_SLICE = re.compile(r"toISOString\(\)\s*\.\s*slice\(\s*0\s*,\s*10\s*\)")
#: 同一行出现 `replace('T'` —— ISO 串特征
ISO_REPLACE = re.compile(r"replace\(\s*['\"]T['\"]")
#: 时间形态的接收方名字
TIME_NAME = re.compile(r"(_at|At|_time|Time|\.at)\s*$")

#: 行首是 `//` 的整行注释（说明文字里会出现这些写法）
LINE_COMMENT = re.compile(r"^\s*//")


def strip_comment(line: str) -> str:
    """去掉行内的 `//` 注释部分（避免把说明文字当成代码）。"""
    idx = line.find('//')
    if idx == -1:
        return line
    # 简单的引号保护：注释符出现在字符串里的情况这里不深究，宁可多报不漏报
    return line[:idx]


def main() -> int:
    hits: list[tuple[str, int, str]] = []
    for path in sorted(SRC.rglob('*.ts')) + sorted(SRC.rglob('*.tsx')):
        rel = path.relative_to(SRC).as_posix()
        if rel in EXEMPT:
            continue
        for n, raw in enumerate(path.read_text(encoding='utf-8').split('\n'), start=1):
            line = strip_comment(raw)
            if ISO_SLICE.search(line):
                hits.append((rel, n, 'toISOString().slice(0,10) 取的是 UTC 日历日'))
                continue
            # ① 同一行有 `replace('T'`：不管接收方叫什么，都是 ISO 串
            if ISO_REPLACE.search(line) and ".slice(" in line:
                hits.append((rel, n, '切 ISO 串 = 按 UTC 显示'))
                continue
            # ② 接收方是时间形态的标识符（`xxx_at` / `row.at` / `xxx_time`）
            m = TIME_SLICE.search(line)
            if m and TIME_NAME.search(m.group(1)):
                hits.append((rel, n, '切 ISO 串 = 按 UTC 显示'))
                continue
            # ③ `String(row.xxx_at).slice(0, 10)` 这种被包一层的形态
            if WRAPPED_TIME_SLICE.search(line):
                hits.append((rel, n, '切 ISO 串 = 按 UTC 显示'))

    print(f'前端时间显示自检：扫了 {len(list(SRC.rglob("*.ts"))) + len(list(SRC.rglob("*.tsx")))} 个文件，'
          f'命中 {len(hits)} 处')
    for rel, n, why in hits:
        print(f'  ✗ [{why}] frontend/src/{rel}:{n}')
    if hits:
        print()
        print('处置：改用 `new Date(v).toLocaleString(\'zh-CN\')`（带时分）或')
        print('      `new Date(v).toLocaleDateString(\'zh-CN\')`（只要日期）；')
        print('      只取"当地那一天"用 `shared/calendar-day.ts` 的 `calendarDay`。')
        print('      时间解析失败时的兜底可以登记进 EXEMPT 并写明理由。')
        return 1
    print('✓ 没有靠切 ISO 串显示时间的地方')
    return 0


if __name__ == '__main__':
    sys.exit(main())
