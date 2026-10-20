#!/usr/bin/env python3
"""静态检查：**核价历史那页读到的每个动作，前端都有中文名**。

为什么要有这一条（主人 2026-10-12 反馈的原话：「一堆不说人话，非技术人员看这个干啥」）：

「价格中心 › 核价历史」的「变更内容」是一层**翻译**（`historySummary`）——
把审计快照翻成人话。它的分支按**动作名**走，而动作名是后端写审计时定的字符串。
只要后端加了一个新动作、前端没跟上，那一条就会掉进"逐字段比对"的兜底分支，
把内部计数原样印出来。实测就是：

- 后端写 `price_refresh`，前端判的是 `refresh_prices` → 掉兜底 →
  屏幕上出现 `skipped: 空 → 1; refreshed: 空 → 0`；
- 后端写 `quote_link_master`，前端**根本没这个分支** → 掉兜底 →
  出现 `missing: 空 → [object Object]`（对象被 `String()` 了）。

这两个都不是"文案不好"，是**机器话漏到界面上**。而它们**不会报错、不会失败**，
只有人去看那页才会发现 —— 所以必须靠静态检查拦：

  ① 动作清单是**硬编码**在这里的（并注明出处）；
  ② 先从后端源码里**扫出**这些文件真正写过的 action，跟清单对账 ——
     对不上就说明"有人加了审计动作但没来更新这份清单"，直接失败（防清单烂掉）；
  ③ 再要求前端 `ACTION_LABEL` 里每个动作都有中文名。

跑法：`python3 ops/check_action_labels.py`（CI 里跟其它前端检查一起跑）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FRONTEND_PAGE = REPO / 'frontend' / 'src' / 'modules' / 'pricing' / 'PriceCenterPage.tsx'

#: 扫这些文件，把 `action="..."` 抓出来（`/pricing/history` 只读这 5 类业务，
#: 所以只需要看写这几类的模块）。
BACKEND_FILES = [
    REPO / 'backend' / 'app' / 'modules' / 'quote' / 'router.py',
    REPO / 'backend' / 'app' / 'modules' / 'quote' / 'service.py',
    REPO / 'backend' / 'app' / 'modules' / 'pricing' / 'service.py',
    REPO / 'backend' / 'app' / 'modules' / 'pricing' / 'router.py',
]

#: 「价格中心 › 核价历史」会显示的动作（`business_type in
#: ('quote','quote_item','product_cost','price_rule','customer_price_rule')`）。
#: 出处：上面 BACKEND_FILES 里的 `write_audit(action=...)`。
EXPECTED_ACTIONS = {
    'add_item', 'auto_pass', 'clone', 'copy_version', 'create', 'create_version',
    # enable 目前后端没写（停用后没有再启用的入口），先留着：
    # 一旦有人加了"启用"，这条清单会先失败提醒，免得又漏到界面上
    'delete', 'delete_item', 'disable', 'expire', 'mark_sent', 'price_refresh', 'cost_refresh',
    'quote_link_master', 'recalculate', 'refresh_prices', 'send_email',
    'set_items', 'submit_approval', 'supersede_approval', 'update', 'update_charge',
    'update_item', 'update_version', 'withdraw', 'withdraw_approval',
}


def frontend_actions() -> set[str]:
    """前端 `ACTION_LABEL` 里已经翻成中文的动作名。"""
    text = FRONTEND_PAGE.read_text(encoding='utf-8')
    start = text.index('const ACTION_LABEL')
    block = text[start : text.index('}', start)]
    return {m.group(1) for m in re.finditer(r"(?m)^\s{2}([a-z_]+):\s*'", block)}


def backend_actions() -> set[str]:
    """后端源码里**写审计**时用过的 action（用于跟上面的清单对账）。

    ⚠️ 只认 `write_audit(...)` 里的 action。第一版是全文搜 `action="..."`，
    于是把 `ApprovalRecord(action="submit")` 也当成了审计动作 ——
    那个 `submit` 是审批节点的动作，跟核价历史半点关系没有，白报一条。
    """
    found: set[str] = set()
    for path in BACKEND_FILES:
        if not path.exists():
            continue
        text = path.read_text(encoding='utf-8')
        for call in re.finditer(r'write_audit\(', text):
            # 从 write_audit( 往后取一小段，抓里面第一个 action=
            window = text[call.start() : call.start() + 700]
            hit = re.search(r'action=["\']([a-z_]+)["\']', window)
            if hit:
                found.add(hit.group(1))
    return found


def main() -> int:
    if not FRONTEND_PAGE.exists():
        print(f'✗ 找不到前端页面：{FRONTEND_PAGE}')
        return 1

    labelled = frontend_actions()
    written = backend_actions()
    problems: list[str] = []

    missing = sorted(EXPECTED_ACTIONS - labelled)
    if missing:
        problems.append(
            '这些动作会出现在核价历史里，但页面没有中文名（会原样印出代码词）：\n    '
            + '、'.join(missing)
        )

    # 清单对账：后端新写了动作却没登记进 EXPECTED_ACTIONS
    unregistered = sorted(written - EXPECTED_ACTIONS)
    if unregistered:
        problems.append(
            '后端写了新的审计动作，但这份清单没更新（请登记到 EXPECTED_ACTIONS，'
            '并确认页面有中文名）：\n    ' + '、'.join(unregistered)
        )

    print(f'检查 {len(EXPECTED_ACTIONS)} 个动作：页面已翻中文 {len(EXPECTED_ACTIONS & labelled)} 个；'
          f'后端源码里扫到 {len(written)} 个')
    if problems:
        print()
        for item in problems:
            print(f'  ✗ {item}')
        return 1
    print('✓ 核价历史里会出现的动作，界面上一律有中文名')
    return 0


if __name__ == '__main__':
    sys.exit(main())
