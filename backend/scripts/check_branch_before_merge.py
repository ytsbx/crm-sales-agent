#!/usr/bin/env python3
"""合并/推送前的分支自查：**确认自己在哪条分支上**。

## 为什么需要它（一次真实事故，2026-10-14）

这个项目里多个 agent 各用一个 `git worktree` + 独立分支并行干活，
而**主工作区会被别人切到他们自己的分支**（`codex/financial-repairs-isolated` 等）。

当时的操作是"在共享分支上合并远端"：

    git merge --no-ff origin/pending-acceptance

看着没问题，但**本地 `pending-acceptance` 已经被切到了别人的合并点**，
于是这一合就把对方 **6 个还没验收的提交一起带上了共享分支并推了出去** ——
内容没坏（自带守卫与单测），但**绕过了他给自己设的隔离/验收环节**。

根因不是"不懂 git"，是**没在动手前确认分支名**。

## 它做什么

在合并/推送这类"动作会外溢到别人那里"的命令之前跑一次：

    python3 scripts/check_branch_before_merge.py            # 看当前分支与预期是否一致
    python3 scripts/check_branch_before_merge.py --expect pending-acceptance

- 当前分支 == `--expect`（或默认的 `ALLOWED`）→ 退出 0，打印分支与 HEAD，可以继续；
- 不一致 → 退出 1，并把**两个分支各差几个提交、差在哪**打出来，
  让人一眼看出"我是不是站错地方了"。

它**不修改任何东西**，只读。
"""
from __future__ import annotations

import argparse
import subprocess
import sys

#: 允许在其上做"合并/推送"的分支。**共享分支只有这一条** ——
#: 别人的 `codex/*`、`fix/*`、`deepseekharness/*` 一律不在其中。
ALLOWED = ("pending-acceptance",)


def git(*args: str) -> str:
    return subprocess.run(
        ['git', *args], capture_output=True, text=True, check=False
    ).stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--expect', action='append', default=None,
                    help='预期所在分支（可多次传；默认只允许 pending-acceptance）')
    ap.add_argument('--quiet', action='store_true')
    args = ap.parse_args()
    expect = tuple(args.expect) if args.expect else ALLOWED

    # 是不是 git 仓库
    if git('rev-parse', '--git-dir') == '':
        print('  ✗ 当前目录不是 git 仓库')
        return 1

    branch = git('rev-parse', '--abbrev-ref', 'HEAD')
    head = git('rev-parse', '--short', 'HEAD')
    dirty = len([l for l in git('status', '--porcelain').split('\n') if l.strip()])

    if branch in expect:
        if not args.quiet:
            print(f'  ✓ 分支自查通过：当前在 `{branch}`（{head}），{dirty} 个未提交改动')
        return 0

    print(f'  ✗ **站错分支了**：当前在 `{branch}`（{head}）')
    print(f'    这里只允许在 {", ".join(f"`{b}`" for b in expect)} 上做合并/推送')
    print()
    print('    两个分支的差异（帮你判断是不是站错了）：')
    for target in expect:
        if git('rev-parse', '--verify', '--quiet', target) == '':
            print(f'      {target}: 本地没有这个分支')
            continue
        ahead = git('rev-list', '--count', f'{target}..HEAD') or '0'
        behind = git('rev-list', '--count', f'HEAD..{target}') or '0'
        print(f'      相对 `{target}`：领先 {ahead} 个、落后 {behind} 个')
        only = [l for l in git('log', '--oneline', f'{target}..HEAD').split('\n') if l.strip()]
        if only:
            print(f'      只在 `{branch}` 上的提交（前 5 个）：')
            for line in only[:5]:
                print(f'        {line}')
    print()
    print('    处置：先 `git switch <正确的分支>` 再动手 ——')
    print('    在别人的 `codex/*` / `fix/*` 分支上合并，会把对方**未验收的提交**')
    print('    一起带进共享分支（2026-10-14 真实发生过一次）。')
    return 1


if __name__ == '__main__':
    sys.exit(main())
