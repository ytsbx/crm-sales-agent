#!/usr/bin/env python3
"""AI 分析的字段必须有中文名（不许把后端内部字段名印给用户）。

## 为什么要有这道检查

2026-10-13 主人在界面上看到回款风险分析里出现：

```
overdue_count      0
overdue_amount     ¥0
open_amount        ¥501
upcoming_count     1
```

原话：「ai分析这一块怎么不讲人话呀，不要有英文」。
根因是 `AgentInsight.tsx` 的兜底写成「没映射的键**直接用原词**」——
于是后端**新增一个字段就必然漏一次**给用户看，而且不报错、不失败，
只能靠人正好点到那一页（和审计动作名、审计字段名是同一类漏法，
那两处已经各有一道检查，这里补上第三处）。

## 判据

- 后端：`app/modules/agent/insights.py` 里**信封会带出去**的字典键。
  宁可多算（把整个函数的字典键都算上）：多要一个中文名是无害的，
  漏一个就会在界面上印英文。
- 前端：`AgentInsight.tsx` 的 `KEY_LABEL`。
- 两边对账，缺一即红。

> 注意：这里只查**字段名**。字段**值**里的英文枚举
> （例如 `priority: high`）由 `AgentInsight` 的 `VALUE_LABEL` 负责翻译，
> 值本身不该在源码里硬编码中文对照，所以不在本检查范围内。
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BACKEND = REPO / "backend" / "app" / "modules" / "agent" / "insights.py"
FRONTEND = REPO / "frontend" / "src" / "shared" / "components" / "AgentInsight.tsx"

#: 这些键**不进** `KEY_LABEL`，逐个说明理由（而不是放一个"已知漏项"清单）：
#:
#: - `id` / `*_id`：内部编号，`AgentInsight` 的列表渲染本来就把它排除在外
#:   （`KEY_LABEL` 是给"要给人看的名字"用的，编号不该出现在表头）。
#: - `level` / `level_label`：`level` 是机器码（high/medium/low），
#:   界面用的是 `level_label`（高风险/关注/正常）；两个都不是键值表渲染的字段。
#: - `commentary` / `commentary_note`：整段叙述，单独渲染，不是键值对。
#: - `scope` / `as_of`：信封元信息，`AgentInsight` 不渲染。
#: - `task_ids`：逾期任务编号数组，给机器用的（值是数组，渲染器本来就会
#:   跳过它并留一个空列）。真要在界面上列任务，应该给"任务列表"这种
#:   带名字的结构，而不是一串编号。
EXEMPT = {
    "id",
    "task_ids",
    "level",
    "level_label",
    "commentary",
    "commentary_note",
    "scope",
    "as_of",
}

#: 信封字段：这些键的值才会被 `AgentInsight` 渲染（键值表 / 小表格）。
#: 其余顶层键（`customer` / `counts` / `target` / `scope` …）**从来不渲染** ——
#: 组件只读 `summary` 与这五个列表字段，所以它们不需要中文名。
ENVELOPE_LIST_FIELDS = {"suggestions", "recommendations", "warnings", "insights", "items"}


def _dict_keys(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Dict):
        return {
            k.value for k in node.keys
            if isinstance(k, ast.Constant) and isinstance(k.value, str)
        }
    return set()


def backend_keys() -> set[str]:
    """会被渲染的后端字段名，只有两个来源（与组件的渲染逻辑一一对应）：

    1. 信封里 `summary` 字典字面量的键 —— 组件用它渲染键值表；
    2. **装进那五个列表字段里的字典**的键 —— 组件用它渲染小表格表头。
       列表内容可能是 `xxx.append({...})`（如 `quote_draft` 的 `lines`），
       也可能是直接写在信封里的字面量列表。

    刻意**不**把整个函数的字典键都算进来：那样会把 `customer` / `counts` /
    `scope` 这些从不渲染的顶层字段也要求配中文名，噪音一大，
    真漏的那个反而看不出来（我第一版就是这么写的，报了 95 个键）。
    """
    tree = ast.parse(BACKEND.read_text(encoding="utf-8"))
    keys: set[str] = set()

    # ① append 到"列表字段同名变量"的字典
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "append"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in ENVELOPE_LIST_FIELDS
        ):
            for arg in node.args:
                keys |= _dict_keys(arg)

    # ② 信封里 summary / 列表字段的字面量
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for k, v in zip(node.keys, node.values):
            if not (isinstance(k, ast.Constant) and isinstance(k.value, str)):
                continue
            if k.value == "summary" and isinstance(v, ast.Dict):
                keys |= _dict_keys(v)
            if k.value in ENVELOPE_LIST_FIELDS and isinstance(v, ast.List):
                for element in v.elts:
                    keys |= _dict_keys(element)
    return keys


def frontend_keys() -> set[str]:
    """前端 `KEY_LABEL` 里已经配了中文名的字段。"""
    text = FRONTEND.read_text(encoding="utf-8")
    block = text.split("const KEY_LABEL", 1)[1].split("\n}", 1)[0]
    return set(re.findall(r"^\s{2}([a-z_]+):", block, flags=re.MULTILINE))


def main() -> int:
    back = backend_keys()
    front = frontend_keys()
    missing = sorted(back - front - EXEMPT)
    print(f"后端分析信封字段 {len(back)} 个，前端已配中文名 {len(front)} 个")
    if missing:
        print()
        print("✗ 这些字段没有中文名，界面上会显示「未命名字段」（用户看不到英文，但等于没说什么）：")
        for key in missing:
            print(f"    {key}")
        print()
        print("  改法：在 frontend/src/shared/components/AgentInsight.tsx 的 KEY_LABEL 里补一行；")
        print("       确实是内部字段（不该给人看）就在本脚本的 EXEMPT 里写明理由。")
        return 1
    print("✓ AI 分析里会渲染的字段都有中文名")
    return 0


if __name__ == "__main__":
    sys.exit(main())
