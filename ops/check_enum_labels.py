#!/usr/bin/env python3
"""「英文枚举值 → 中文」这几张前端映射表，必须覆盖后端会产出的值。

## 为什么要有这道检查

这些地方都写成 `LABEL[value] ?? value` —— **查不到中文名就把英文原值印给用户**。
于是后端新增一个枚举值，界面就悄悄多一句英文，既不报错也不失败，
只能靠人正好点到那一页（主人 2026-10-13 看到 AI 分析里的 `overdue_count` 就是这么发现的；
审计动作名、审计字段名是同一类，已各有检查）。

这套件守的是**另外四处**（都是同一写法）：

| 位置 | 映射表 | 后端真源 |
| --- | --- | --- |
| 订单交期预览 | `planOffsetLabels` | `order/milestones.py` 的 `MILESTONE_NODES` |
| WhatIf 面板 | `APPROVAL_LABEL` | `quote/model.py` 的 `QUOTE_STATUS_LABEL` 同族审批状态 |
| 价格中心·核价历史 | `HISTORY_FIELD_LABEL` | 该页 `SUMMARY_FIELDS` 白名单里的字段 |
| 价格中心·枚举值 | `VALUE_LABEL` | 同上：报价状态 |

## 判据说明（为什么有的表不要求"全覆盖"）

- `planOffsetLabels`：交期表单**只会遍历那六个固定节点**，所以只要求覆盖
  `MILESTONE_NODES`。动态批次节点（`shipment_batch_*`）不进这张表单 ——
  但 `order.plan_offsets` 是 JSON、理论上可能被塞进别的键，
  所以对"多余键"另有一条提醒（见下面的 `EXTRA_KEYS_HINT`）。
- `APPROVAL_LABEL`：只要求覆盖**审批状态**那一族；报价状态不是它的职责。
- `HISTORY_FIELD_LABEL`：只要求覆盖**会显示的白名单字段**
  （`SUMMARY_FIELDS` 与两处硬编码清单）。快照里 `id` / `created_at` 之类
  内部字段本来就不显示，不要求配中文名 —— 配了反而会误导人以为会显示。
- `VALUE_LABEL`：只要求覆盖**报价状态**（`QUOTE_STATUS_LABEL`）。
  它是一张"跨枚举"的表，混进别的族的同名值会**误译**，所以另有
  `VALUE_LABEL_FORBIDDEN` 明确禁止几个值混进来（这条是 2026-10-13
  核对时真踩到的：`pending: '审批中'` 把报价状态里不存在的 pending 也译成了审批中）。
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BACKEND = REPO / "backend" / "app" / "modules"
FRONTEND = REPO / "frontend" / "src"

ORDER_DETAIL = FRONTEND / "modules" / "order" / "OrderDetailPage.tsx"
WHATIF = FRONTEND / "modules" / "quote" / "WhatIfPanel.tsx"
PRICE_CENTER = FRONTEND / "modules" / "pricing" / "PriceCenterPage.tsx"
MILESTONES = BACKEND / "order" / "milestones.py"
QUOTE_MODEL = BACKEND / "quote" / "model.py"

#: 「价格中心」的 `VALUE_LABEL` 里**不许出现**的值：它们属于别的枚举族，
#: 混进来会把同名但不同义的值译错（真实踩过 `pending` → 审批中）。
VALUE_LABEL_FORBIDDEN = {
    "not_submitted": "版本审批状态，不属于报价状态；核价历史里不会出现",
    "pending": "版本审批状态里的 pending 是「审批中」，而报价状态没有这个值 —— 混进来必误译",
    "rejected": "版本审批状态的 rejected 应叫「审批未通过」；「未通过」太含糊，也没说清是什么没通过",
}

problems: list[str] = []


def fail(message: str) -> None:
    problems.append(message)


def extract_map_block(path: Path, const_name: str) -> str:
    """取映射表**自己的**那一段（按大括号配对，不按"下一个行首 }"）。

    ⚠️ 这里踩过一次假绿：`planOffsetLabels` 是**一行写完**的
    （`const planOffsetLabels = { contract: '签订合同', ... }`），
    而原来的写法是"从 const 名切到下一个行首 `}`" —— 单行表的那一行里
    确实有个 `}`，但 `split("\n}")` 要找的是**换行后**的 `}`，
    于是一路切到几百行之后，把后面 state 对象里的
    `{ title, dataIndex, width, ... }` 全当成了"映射表的键"。
    键集合被撑大 → 删掉一个真键照样"全覆盖" → **检查恒绿**。
    假绿比不查更坏，所以改成老老实实数括号。
    """
    text = path.read_text(encoding="utf-8")
    marker = f"const {const_name}"
    if marker not in text:
        raise AssertionError(f"{path.name} 里找不到 {const_name}")
    rest = text.split(marker, 1)[1]
    start = rest.index("{")
    depth = 0
    for index, char in enumerate(rest[start:], start=start):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return rest[start + 1:index]
    raise AssertionError(f"{path.name} 里 {const_name} 的大括号不配对")


def map_keys(path: Path, const_name: str) -> set[str]:
    """映射表的键。

    ⚠️ 不要按"行首缩进"匹配：有的表是一行写完的
    （`const planOffsetLabels = { contract: '签订合同', deposit: '付定金', ... }`），
    按缩进匹配会**一个键都读不到**，于是检查恒绿 —— 假绿比不查更坏。
    这里只认"紧跟在 `{` 或 `,` 之后的 `标识符:`"，两种写法都能读。
    """
    block = extract_map_block(path, const_name)
    # 去掉行尾注释，免得把注释里的 `xxx:` 也当成键
    block = "\n".join(line.split("//")[0] for line in block.splitlines())
    # 行首也可以是一个键（取块时不带开头那个 `{`，所以第一个键前面什么都没有）——
    # 漏了这条会把**每张表的第一个键**判成缺失，方向正好反了。
    return set(re.findall(r"(?:^|[{,])\s*(\w+):", block, flags=re.MULTILINE))


def milestone_nodes() -> list[str]:
    tree = ast.parse(MILESTONES.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "MILESTONE_NODES":
            return [el.elts[0].value for el in node.value.elts]
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", "") == "MILESTONE_NODES" for t in node.targets
        ):
            return [el.elts[0].value for el in node.value.elts]
    raise AssertionError("读不到 MILESTONE_NODES")


def quote_statuses() -> set[str]:
    block = QUOTE_MODEL.read_text(encoding="utf-8").split("QUOTE_STATUS_LABEL = {", 1)[1].split("\n}", 1)[0]
    return set(re.findall(r'"(\w+)":', block))


def summary_fields() -> set[str]:
    """价格中心真正会显示的字段名（白名单 + 两处硬编码清单）。"""
    text = PRICE_CENTER.read_text(encoding="utf-8")
    keys: set[str] = set()
    block = text.split("const SUMMARY_FIELDS", 1)[1].split("\n}", 1)[0]
    for row in re.findall(r"\[([^\]]*)\]", block):
        keys |= set(re.findall(r"'([a-z_]+)'", row))
    # `summaryOfSnapshot` 之外，还有两处硬编码清单（明细增删、字段变更）
    for row in re.findall(r"const parts = \[([^\]]*)\]", text):
        keys |= set(re.findall(r"'([a-z_]+)'", row))
    return keys


def main() -> int:
    # ① 交期预览：六个里程碑节点
    nodes = milestone_nodes()
    labels = map_keys(ORDER_DETAIL, "planOffsetLabels")
    missing = sorted(set(nodes) - labels)
    if missing:
        fail(f"订单交期预览 planOffsetLabels 缺 {missing}（后端 MILESTONE_NODES 有这些节点）")

    # ② WhatIf：审批状态那一族
    approval = {"not_submitted", "pending", "approved", "rejected"}
    labels = map_keys(WHATIF, "APPROVAL_LABEL")
    missing = sorted(approval - labels)
    if missing:
        fail(f"WhatIf 面板 APPROVAL_LABEL 缺 {missing}")

    # ③ 价格中心：会显示的字段
    fields = summary_fields()
    labels = map_keys(PRICE_CENTER, "HISTORY_FIELD_LABEL")
    missing = sorted(fields - labels)
    if missing:
        fail(f"价格中心 HISTORY_FIELD_LABEL 缺 {missing}（这些字段会显示在白名单里）")

    # ④ 价格中心：报价状态
    statuses = quote_statuses()
    labels = map_keys(PRICE_CENTER, "VALUE_LABEL")
    missing = sorted(statuses - labels)
    if missing:
        fail(f"价格中心 VALUE_LABEL 缺报价状态 {missing}（历史行会原样印出英文）")
    mixed = sorted(set(VALUE_LABEL_FORBIDDEN) & labels)
    if mixed:
        for key in mixed:
            fail(f"价格中心 VALUE_LABEL 混进了别的枚举族的值 `{key}`：{VALUE_LABEL_FORBIDDEN[key]}")

    print(
        f"核对：里程碑节点 {len(nodes)} 个、审批状态 {len(approval)} 个、"
        f"价格历史字段 {len(fields)} 个、报价状态 {len(statuses)} 个"
    )
    if problems:
        print()
        for item in problems:
            print(f"  ✗ {item}")
        print()
        print("  改法：在对应的映射表里补中文名。确实不该给人看的值，")
        print("       在本脚本的说明里写清理由（别默默放过去）。")
        return 1
    print("✓ 四处「英文枚举 → 中文」映射都覆盖了后端会产出的值")
    return 0


if __name__ == "__main__":
    sys.exit(main())
