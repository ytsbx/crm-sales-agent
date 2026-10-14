#!/usr/bin/env python3
"""登记项自检：**清单/工作流里登记的东西，文件是否真在仓库里**。

## 为什么要这一步（2026-10-14 复盘：CI 连红十几次全是同一类）

这个仓库经常有**多个会话同时改**。反复出现的病程是：
**登记项与其对应物被拆到不同的提交里** ——

  · 有人往 `ops/check_suites.txt` 加了一行，脚本还在他工作区；
  · 有人在 `.github/workflows/ci.yml` 里写了 `node --test ../ops/x.test.mjs`，
    文件还没提交；
  · 有人先写断言、实现还在改。

而只有一条 `pending-acceptance` 分支、CI 跑"分支上的一切"，于是**所有人**的 CI 红，
且红在**下游**（"找不到脚本/文件"、某条看着不相干的断言），
指向的是受害者，不是漏提交的人。这几轮我就在这上面反复花时间。

## 这一步做什么

在跑任何一条套件之前，把三处登记项与仓库里实际存在的文件对一遍，
**逐个点名**缺哪个文件，然后退出码 1。

## 为什么用 Python 而不是一段 bash

第一版我写成 CI 里的一段 bash（`while read` + 嵌套 `$(grep … | sed …)`），
**本地和干净检出都通过、CI 上却红**，而且没打印出缺哪个文件 ——
嵌套命令替换 + `sed` 的转义在不同 shell/版本下行为不一样，是最难查的那种红。
按项目口径：这种判定要写成**能离线跑、能自己测**的脚本，
所以这里用 Python（`--list` 只列不判失败）。

## 用法

    cd backend && PYTHONPATH=. .venv/bin/python scripts/check_registered_files.py
    # 或只列出：--list
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SUITES = REPO / "ops" / "check_suites.txt"
CI_YML = REPO / ".github" / "workflows" / "ci.yml"
RUN_CHECKS = REPO / "ops" / "run_checks.sh"


def _strip_comments(text: str) -> str:
    """去掉整行注释 —— 否则注释里举的例子会被当成真实登记项。

    ⚠️ 实测踩过：我第一版直接 grep `ci.yml`，把自己注释里写的
    `node --test ../ops/xxx.test.mjs` 也当成了登记项，自检立刻假红。
    """
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def registered_suites() -> list[str]:
    """`ops/check_suites.txt` 里登记的套件名（忽略注释与空行）。"""
    names: list[str] = []
    for line in SUITES.read_text(encoding="utf-8").splitlines():
        name = line.split("#", 1)[0].strip()
        if name:
            names.append(name)
    return names


def registered_frontend_tests() -> list[str]:
    """`ci.yml` 与 `ops/run_checks.sh` 里登记的前端单测文件名。"""
    found: set[str] = set()
    for path, pattern in (
        (CI_YML, r"ops/([A-Za-z0-9_.-]+\.test\.mjs)"),
        (RUN_CHECKS, r"\$\{?SCRIPT_DIR\}?/([A-Za-z0-9_.-]+\.test\.mjs)"),
    ):
        if not path.exists():
            continue
        text = _strip_comments(path.read_text(encoding="utf-8"))
        found |= set(re.findall(pattern, text))
    return sorted(found)


def referenced_frontend_sources() -> list[tuple[str, str]]:
    """前端单测里 `readFileSync(new URL('../frontend/src/…'))` 引用的源文件。

    ## 为什么要查这条（2026-10-14，我自己刚犯过）

    我提交了 `ops/payment_allocation.test.mjs`（它会读
    `frontend/src/shared/payment-allocation.ts`），**那个模块没提交** ——
    本地因为它在我工作区里所以是绿的，CI 上直接
    `ENOENT: no such file or directory`。

    与"清单登记了套件但脚本没提交"是**同一个病**：登记/引用方进来了，
    被引用方没进来。既然要防，就把引用关系也一起查。
    """
    found: list[tuple[str, str]] = []
    pattern = re.compile(r"new URL\('(\.\./frontend/src/[^']+)'")
    for test_file in sorted((REPO / "ops").glob("*.test.mjs")):
        text = test_file.read_text(encoding="utf-8")
        for rel in pattern.findall(text):
            found.append((test_file.name, rel))
    return found


def main() -> int:
    list_only = "--list" in sys.argv
    missing: list[str] = []

    suites = registered_suites()
    for name in suites:
        target = REPO / "backend" / "scripts" / f"{name}.py"
        if not target.exists():
            missing.append(
                f"套件清单 ops/check_suites.txt 登记了 {name}，"
                f"但 backend/scripts/{name}.py 不在仓库里（脚本还在别人工作区？）"
            )

    tests = registered_frontend_tests()
    for name in tests:
        target = REPO / "ops" / name
        if not target.exists():
            missing.append(
                f"前端单测登记了 ops/{name}（ci.yml 或 ops/run_checks.sh），"
                "但文件不在仓库里"
            )

    referenced = referenced_frontend_sources()
    for test_name, rel in referenced:
        target = (REPO / "ops" / rel).resolve()
        if not target.exists():
            missing.append(
                f"{test_name} 引用了 {rel.replace('../frontend/', 'frontend/')}，"
                "但那个源文件不在仓库里"
            )

    print(
        f"登记项自检：套件 {len(suites)} 条、前端单测 {len(tests)} 个、"
        f"单测引用的前端源文件 {len(referenced)} 处"
    )
    if not missing:
        print("✓ 登记项都有对应文件")
        return 0
    for item in missing:
        print(f"  ✗ {item}")
    if list_only:
        return 0
    print()
    print("这类红的根因几乎总是：**登记项与其对应物被拆到了不同的提交里**。")
    print("处置：确认文件是否在别人工作区；跑通之后与登记项**一起**提交。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
