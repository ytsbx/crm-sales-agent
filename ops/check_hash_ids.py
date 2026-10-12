#!/usr/bin/env python3
"""静态检查：**用户可见的文案里不许再用 `#` 拼编号**。

背景（主人 2026-10-12 看着审计页问）：「报价单 #8。这个 # 啥意思」——
`#8` 是程序员写"编号 8"的习惯（英文 `No.` / `#`），中文界面上没必要让用户认它。
全站统一改成「报价单 编号 8」。

这份检查守两件事：

  ① **前端**：`.tsx` 里不许再出现 `#${...}` 这种模板拼接（`编号 ${...}` 才对）；
  ② **后端**：`.py` 里不许再出现 `#{...}`（f-string 里的编号拼接）；
⚠️ **为什么只扫 `frontend/src` 与 `backend/app`，不扫 `backend/scripts`**：
那些脚本的输出是**给开发者看的**（`=== #3 整单优惠…===`、`issue #11`），
`#3` 是 issue 编号而不是界面文案，改了反而丢信息。
我一度把扫描范围扩到 scripts，结果 11 处命中**全是开发标签、没有一处真问题** ——
**范围扩错了等于制造噪音，噪音会让这份检查被忽略。**

至于"脚本按文案做断言、文案改了脚本不改"那个真问题（`check_sixth_round_p2`
的 `re.findall(r"打样单 #(\d+)")` 就是这么失效的），
由 `check_copy_coupled_assertions.py` 单独守 —— 那件事和"`#` 拼编号"是两码事，别混在一个检查里。

⚠️ **只查"拼编号"**，不碰这两类合法的 `#`：
  · URL 片段（`to={... : '#'}`、`href="#"`）—— 用户看不到，改了反而坏；
  · Python 注释（`# 5`）—— 源码里的 `#` 是注释语法，不是文案。
  检查用"`#` 紧跟 `{` 或 `$`"这个特征把它们区分开。

跑法：`python3 ops/check_hash_ids.py`
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TARGETS = (
    (REPO / 'frontend' / 'src', '*.tsx', re.compile(r'#\$\{')),
    (REPO / 'frontend' / 'src', '*.ts', re.compile(r'#\$\{')),
    (REPO / 'backend' / 'app', '*.py', re.compile(r'#\{')),
)


def main() -> int:
    hits: list[str] = []
    scanned = 0
    for root, pattern, regex in TARGETS:
        for path in sorted(root.rglob(pattern)):
            scanned += 1
            for lineno, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
                if regex.search(line):
                    hits.append(
                        f'{path.relative_to(REPO)}:{lineno}  {line.strip()[:100]}'
                    )

    print(f'扫描 {scanned} 个文件')
    if hits:
        print()
        print('这些地方还在用 `#` 拼编号（用户会看到），请改成「编号 N」：')
        for hit in hits:
            print(f'  ✗ {hit}')
        return 1
    print('✓ 用户可见的文案里没有 `#` 拼编号（统一用「编号 N」）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
