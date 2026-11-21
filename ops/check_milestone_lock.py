#!/usr/bin/env python3
"""静态检查：**会写跟单节点的写接口必须持有订单锁**。

## 背景（第三批复审遗留 P1，2026-10-12 真库复现）

`GET /orders/{id}/milestones` 会写库（首次访问建六个空节点 + 按财务事实同步），
而它读的"应收合计 / 已确认收款"是财务那边随时会改的事实。取订单时**不带锁**
就是"读旧汇总、写覆盖"：

    T1 页面 GET   : 读订单(不带锁) → 建节点 → 汇总 = 应收100/已收100
    T2 财务改应收 : 取订单锁 → 应收改 200 → 节点转 partial → 提交
    T1 页面 GET   : 拿旧汇总判定"已收齐" → 把「收款」节点写成完成 → 提交
    终态：应收 200、已收 100，**收款节点却是完成** —— 钱没到齐却说收齐了，
    看订单页的人据此**不再催款**。

真库两会话的复现与修复验证在 `scripts/check_milestone_lock_race.py`；**这份检查
是它的离线替身**：真库脚本要起库起后端才能跑，而 CI 上更需要一条"改回去就红"的
快速守门 —— 谁把 `for_update=True` 删了，这里立刻报出来。

## 判据

`app/modules/order/router.py` 里，**凡是调用 `milestones_svc.ensure_initialized(`
或 `milestones_svc.sync_payment_milestones(` 的函数**（这两个都会写跟单节点），
它取订单的那一行必须写成 `get_visible_order(..., for_update=True)`。

⚠️ 只查"取订单"这一件事，不猜别的：不解析调用图、不判断写没写库 ——
那类推断一错就是误报，而误报会让人开始忽略这份检查。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TARGET = REPO / 'backend' / 'app' / 'modules' / 'order' / 'router.py'

#: 会写跟单节点的调用（出现其一，这个函数就必须持锁）
WRITES_MILESTONES = re.compile(r'milestones_svc\.(ensure_initialized|sync_payment_milestones)\(')
#: 取订单：带锁与不带锁两种写法
TAKE_ORDER_LOCKED = re.compile(r'get_visible_order\([^)]*for_update\s*=\s*True')
TAKE_ORDER_PLAIN = re.compile(r'get_visible_order\((?![^)]*for_update)')


def enclosing_functions(lines: list[str]) -> list[tuple[str, int, int]]:
    """粗分函数块：`async def name(` 到下一个顶格 `def`/`@router` 之前。"""
    starts = [
        (index, line)
        for index, line in enumerate(lines)
        if re.match(r'\s*(async )?def \w+\(', line)
    ]
    blocks: list[tuple[str, int, int]] = []
    for position, (index, line) in enumerate(starts):
        name = re.search(r'def (\w+)\(', line).group(1)
        end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        blocks.append((name, index, end))
    return blocks


def main() -> int:
    lines = TARGET.read_text(encoding='utf-8').splitlines()
    problems: list[str] = []
    checked = 0

    for name, start, end in enclosing_functions(lines):
        body = '\n'.join(lines[start:end])
        if not WRITES_MILESTONES.search(body):
            continue
        checked += 1
        if TAKE_ORDER_LOCKED.search(body):
            print(f'  ✓ {name}：写了跟单节点，且取订单带了锁')
            continue
        detail = (
            '它根本没取订单'
            if not TAKE_ORDER_PLAIN.search(body)
            else '它取订单时**没有** for_update=True'
        )
        problems.append(f'{name}（第 {start + 1} 行起）：{detail}')

    print()
    print(f'核对 {TARGET.relative_to(REPO)}：会写跟单节点的函数 {checked} 个')
    if problems:
        print()
        print('这些函数会写跟单节点，但没持订单锁 —— 并发下会把"未收齐"写成"收款完成"：')
        for item in problems:
            print(f'  ✗ {item}')
        print()
        print('  改法：`get_visible_order(session, user, order_id, for_update=True)`，')
        print('  并在同一个事务里做完"读汇总 → 写节点 → 提交"。')
        print('  真库复现：backend/scripts/check_milestone_lock_race.py')
        return 1
    print('✓ 所有会写跟单节点的函数都持了订单锁')
    return 0


if __name__ == '__main__':
    sys.exit(main())
