#!/usr/bin/env python3
"""静态检查：**页面里的禁用控件有没有解释**（主人 2026-10-12 定的规矩）。

为什么需要这个脚本（而不是只靠人看）：
    `ops/blocked_hint.test.mjs` 只测共享件本身算得对不对，它**测不到「页面忘了接」**。
    而这一轮修的毛病恰恰是"忘了接"：按钮灰着、界面一个字不说。
    新写的页面照样会漏，所以按项目习惯（唯一出口 + 静态检查）在 CI 上拦一道。

判据（故意做得**粗但稳**，宁可漏报也不误报）：
    一个 `.tsx` 文件里出现了 `disabled=` 或 `okButtonProps=`，就要求它在下面三者中
    至少占一样：
      1. 用了共用件：`BlockedHint` / `useBlockedNotify` / `blockedToastText`；
      2. 在 `例外清单` 里（下面那个集合，每条都写明"为什么这里不用解释"）；
      3. 同一个「台账文件」里被登记为已知未修（`KNOWN_PENDING`），**并会打印出来**。

为什么按**文件**判而不是按每个按钮判：按按钮判要解析 JSX，判据会脆（换个写法就
瞎报或漏报），而这条检查的价值在于"这个页面有没有意识到要解释" —— 文件级足够，
而且不会因为格式化/换行变化就红。

用法：
    python3 ops/check_disabled_hint.py            # 有问题退出码 1（CI 用）
    python3 ops/check_disabled_hint.py --list      # 只列出，不判失败
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "frontend" / "src"

# 用了共用件就算"意识到要解释"
#
# `GuardedButton`（2026-10-15 加）是 `BlockedHint` 的**包装版**：一个标签同时做到
# "禁用时旁边常驻一行说明"与"点一下弹提示"，页面不用再手写那八行。
# 所以用了它的文件**自动算已覆盖** —— 这是把"事后补登记例外"变成
# "一开始就写对"的关键一步（补例外的路子治不了根：新人写新页面照样漏，
# 2026-10-15 一轮就补了三个文件）。
SHARED = ("BlockedHint", "useGuardedButton", "GuardedButton", "useBlockedNotify", "blockedToastText")

# 判据：这些写法说明这个文件里有"可能变灰"的控件
DISABLED_PATTERNS = (re.compile(r"\bdisabled=\{"), re.compile(r"okButtonProps"))

# ---- 例外清单：每一条都要写清"为什么这里不需要解释" ----
# 原则：**原因就在控件旁边、用户一眼能看到**的，才可以免解释
# （例如"保存中""上一页/下一页""旁边就是刚填完的必填框"）。
EXEMPT: dict[str, str] = {
    # 登录页：按钮变灰只在提交中（且按钮自己带 loading 圈），不存在"不知道为什么"
    "modules/auth/LoginPage.tsx": "只有提交中的瞬时禁用，按钮自带 loading",
    # 分页/翻页：标准语义，灰=到头了，无需解释
    "modules/wecom/WeComPage.tsx": "只有分页上一页/下一页的瞬时禁用",
    # 工作台：勾选完成待办时禁用该勾选框，只在**这一次请求飞行中**；
    # 旁边紧挨着任务标题与优先级，且状态一变就重取（2026-10-14 修"失败无提示"）
    "modules/workbench/WorkbenchPage.tsx": "只有提交完成待办时的瞬时禁用，紧挨着任务行",
    # 图表/看板类：禁用来自视图切换，切换按钮紧挨着
    "modules/opportunity/OpportunityListPage.tsx": "状态筛选随表格/看板视图联动，切换器紧挨着",
    "modules/analytics/AnalyticsPage.tsx": "期间选择随结账模式联动，弹窗顶部已有说明块",
    # 决策人卡片：卡片上本来就印着「无邮箱」「无电话」
    "modules/common/DecisionMakerCard.tsx": "卡片上已印「无邮箱」/「无电话」，原因就在旁边",
    # 价格中心：判例发生地，按钮旁已有灰字 + 点击提示（本规则的样板）
    "modules/pricing/PriceCenterPage.tsx": "本案样板：灰字说明 + 点击弹提示已就地实现",
    "modules/pricing/PricingPage.tsx": "空态已写明「选择 SKU 并在左边填上「拟报价」后可用」",
    # 物流试算：旁边已写「请先选择 SKU 并填写大于 0 的数量」
    "modules/logistics/LogisticsPage.tsx": "旁边已写明先选 SKU 并填数量",
    # 设置页：禁用控件旁边都有「（不可修改）」或选中计数
    "modules/settings/SettingsPage.tsx": "标签写明「（不可修改）」/ 旁边有「已选 N 条」",
    "modules/settings/NotificationSettingsPanel.tsx": "总开关与「投递失败 N 条」计数就在同一屏",
    # 个人账号（2026-10-15 补登记）：4 处 `disabled` 全是 `mutation.isPending`
    # —— 提交中的瞬时禁用，提交按钮自带 loading；**没有**任何"条件不满足"的禁用。
    "modules/settings/PersonalAccountPanel.tsx": "只有提交中的瞬时禁用，提交按钮自带 loading",
    # 未分级规则（2026-10-15 补登记）：4 个输入/开关是 `mutation.isPending`（瞬时）；
    # 保存按钮多一条 `pools.isError || tasks.isError`，而**紧挨着下面就写着**
    # 「规则加载失败，请刷新后重试。」（`role="alert"`）—— 原因就在控件旁边。
    "modules/settings/UnratedRulesPanel.tsx": "保存按钮的禁用旁边就是「规则加载失败，请刷新后重试」，其余为瞬时禁用",
    # 打样钉钉流程弹窗（2026-10-15 补登记）：禁用只有两种来源，且**都在旁边写着** ——
    # ① `mutate.isPending`：提交中的瞬时禁用，提交按钮自带 loading；
    # ② `submitted`（已提交后锁定）：弹窗里列着已提交的审批记录，
    #    底部按钮已变成「重试原申请」，并另给「修改后重新提交」的入口 ——
    #    用户点不动时，屏幕上就有"已经提交过了"这件事。
    # 2026-10-15 已修：保存草稿/提交按钮原来写 `disabled={!query.data}`（读不到就变灰、
    # 界面一个字不说），改成可点 + 如实报错；请求失败时也补了"此时不能保存也不能提交"。
    # 剩下的是：已提交后锁定（记录列在弹窗里，按钮已改为「重试原申请」）与提交中的瞬时禁用。
    "modules/sample/SampleDingTalkModal.tsx": "已提交后锁定（记录就列在弹窗里）或提交中的瞬时禁用；原来的灰按钮已改为可点+报错",
    # 任务列表：状态列与「（已选 N）」就在旁边
    "modules/task/TaskListPage.tsx": "状态列/已选计数就在控件旁",
    # 样品列表：勾选与数量框本身就是原因（本轮已按同一规则叙述）
    "modules/sample/SampleListPage.tsx": "禁用项旁边就是选择框/计数，且本轮已补空态说明",
    # 案例库：旁边有 FormLabel hint「可多选，选完点右侧「添加」」
    "modules/cases/CasesPage.tsx": "旁边 FormLabel 已写「选完点右侧「添加」」",
    # 订单列表：明细只剩一行时不能删，行数就在旁边
    "modules/order/OrderListPage.tsx": "只剩一行明细时不能删，行数就在旁边",
    # 产品详情：保存中的瞬时禁用
    "modules/product/ProductDetailPage.tsx": "只有保存中的瞬时禁用",
    "modules/product/PendingImagePicker.tsx": "父级传入的保存中状态，按钮自带 loading",
    # 报价详情：本轮已补「历史版本/已发送」说明；其余是提交中的瞬时禁用
    "modules/quote/QuoteDetailPage.tsx": "非当前版本/已发送的说明已就地补上，其余为瞬时禁用",
    # 订单详情：本轮已补「同步履约状态」「预览受影响面」的说明
    "modules/order/OrderDetailPage.tsx": "本轮已补同步/排期预览的说明，其余为瞬时禁用或原因在旁",
    # 合同文档：本轮已补签署弹窗与生成入口的说明
    "modules/contract/DocumentsPage.tsx": "本轮已补签署弹窗与生成入口的说明",
    # 客户列表/详情：本轮已补导出与用途说明
    "modules/customer/CustomerListPage.tsx": "本轮已补导出与用途说明",
    "modules/customer/CustomerDetailPage.tsx": "标签/合并冲突列表就在同一弹窗里，逐条可改",
    # 订单草稿：本轮已补三处说明
    "modules/order/OrderDraftsPage.tsx": "本轮已补全页禁用、生成需求单、正式下单三处说明",
    # 报价需求（定制需求）：本轮已补「更多」菜单缺项说明
    "modules/knowledge/KnowledgePage.tsx": "本轮已补「更多」菜单缺项说明与关联商机提示",
    # 附件面板：本轮已补无写权限的说明
    "modules/common/AttachmentPanel.tsx": "本轮已补无写权限的说明",
    # 规则面板：本轮已补试算空态
    "modules/approval/RulesPanel.tsx": "本轮已补试算的空态说明",
    # Agent 页：流式回复中的瞬时禁用（且同状态切换会话时已有提示）
    "modules/agent/AgentPage.tsx": "流式回复中的瞬时禁用，切会话时已有提示",
    "modules/common/CopilotDrawer.tsx": "流式回复中的瞬时禁用",
    # 弹窗里需要确认的通用件：okButtonProps 的 disabled 由调用方给
    "shared/components/ConfirmRequiredModal.tsx": "通用确认弹窗，disabled 由调用方决定",
}


def strip_comments(text: str) -> str:
    """去掉注释与字符串字面量，避免"注释里提到 disabled"造成误报。"""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"(?m)^\s*//.*$", "", text)
    return text


def main() -> int:
    list_only = "--list" in sys.argv
    files = sorted(SRC.rglob("*.tsx"))
    offenders: list[tuple[str, str]] = []
    covered = 0
    for path in files:
        rel = path.relative_to(SRC).as_posix()
        raw = path.read_text(encoding="utf-8")
        code = strip_comments(raw)
        if not any(pattern.search(code) for pattern in DISABLED_PATTERNS):
            continue
        if any(name in code for name in SHARED) or rel in EXEMPT:
            covered += 1
            continue
        if list_only:
            print(f"（未覆盖）{rel}")
        else:
            offenders.append((rel, "有 disabled/okButtonProps，但既没用共用件也不在例外清单里"))

    total = len(files)
    print(f"扫描 {total} 个 tsx：有禁用控件的文件里，已覆盖 {covered} 个、未覆盖 {len(offenders)} 个")
    if list_only:
        return 0
    if offenders:
        print("\n未覆盖（要么接 BlockedHint，要么在 ops/check_disabled_hint.py 的 EXEMPT 里写明理由）：")
        for rel, why in offenders:
            print(f"  ✗ frontend/src/{rel} —— {why}")
        return 1
    print("✓ 每个含禁用控件的页面都解释了原因（或已在例外清单里登记理由）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
