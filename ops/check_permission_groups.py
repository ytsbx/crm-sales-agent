#!/usr/bin/env python3
"""静态检查：角色弹窗的**权限分组**是否覆盖了后端所有权限模块。

背景（主人 2026-10-12）：「权限这里太乱了，我想要一目了然的感觉，排版也不好，
要找具体的权限要翻半天」——于是权限选择器改成按业务条线分组
（`frontend/src/shared/components/PermissionPicker.tsx`）。

分组靠的是后端权限表里的 `resource` 字段。这份检查守两件事：

  ① 后端 `PERMISSIONS` 里出现过的每个 `resource`，都要在
     `PERMISSION_RESOURCE_LABEL` 里有中文名（否则组名会显示成 `wecom` 这种英文）；
  ② 每个 `resource` 都要被编进 `PERMISSION_SECTIONS` 的某个条线
     （否则它会掉进「其他权限」—— 不丢权限，但用户"找半天"的老毛病又回来了）。

两条都不会报错、不会失败，只在界面上难看，所以只能静态查。

跑法：`python3 ops/check_permission_groups.py`
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SEED = REPO / 'backend' / 'scripts' / 'seed.py'
PICKER = REPO / 'frontend' / 'src' / 'shared' / 'components' / 'PermissionPicker.tsx'


def backend_resources() -> set[str]:
    """从种子权限表里抠出所有 `resource`（纯静态，不 import 后端）。"""
    source = SEED.read_text(encoding='utf-8')
    match = re.search(
        r'PERMISSIONS: list\[tuple\[str, str, str, str\]\] = \[(.*?)\n\]', source, re.S
    )
    if not match:
        raise SystemExit('✗ 在 seed.py 里找不到 PERMISSIONS')
    return set(re.findall(r'\(\s*"[a-z_]+:[a-z_]+",\s*"[^"]+",\s*"([a-z_]+)",', match.group(1)))


def frontend_sets() -> tuple[set[str], set[str]]:
    """返回（有中文名的 resource，被编进条线的 resource）。"""
    text = PICKER.read_text(encoding='utf-8')

    start = text.index('export const PERMISSION_RESOURCE_LABEL')
    labelled = set(re.findall(r'(?m)^\s*([a-z_]+)\s*:', text[start : text.index('\n}', start)]))

    start = text.index('export const PERMISSION_SECTIONS')
    block = text[start : text.index('\n]', start)]
    sectioned = set(re.findall(r"'([a-z_]+)'", block))
    return labelled, sectioned


def main() -> int:
    resources = backend_resources()
    labelled, sectioned = frontend_sets()
    problems: list[str] = []

    missing_label = sorted(resources - labelled)
    if missing_label:
        problems.append(
            '这些权限模块没有中文名（组名会显示成英文）：\n    ' + '、'.join(missing_label)
        )
    missing_section = sorted(resources - sectioned)
    if missing_section:
        problems.append(
            '这些权限模块没有被编进任何业务条线（会掉进「其他权限」）：\n    '
            + '、'.join(missing_section)
        )

    print(f'核对：后端权限模块 {len(resources)} 个，'
          f'有中文名 {len(resources & labelled)} 个，已编条线 {len(resources & sectioned)} 个')
    if problems:
        print()
        for item in problems:
            print(f'  ✗ {item}')
        return 1
    print('✓ 权限分组覆盖了后端所有模块')
    return 0


if __name__ == '__main__':
    sys.exit(main())
