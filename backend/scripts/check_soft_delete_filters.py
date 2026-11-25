#!/usr/bin/env python3
"""静态检查：**软删模型的统计类查询有没有排除已删除行**。

## 为什么

这个项目对 13 个模型用**软删**（`deleted_at`）：客户、联系人、线索、商机、报价、
定制需求、案件、产品、SKU、合同模板、合同文档、产品洞察、销售目标。

软删的代价是：**每一条统计都必须自己记得加 `deleted_at is None`** ——
漏一处，被删掉的数据就会继续出现在数字里。而这类缺陷**在界面上看不出来**
（数字只差一点，没人会去对数），所以特别适合用静态检查守。

这不是假想的：2026-10-14 的审查就在 `analytics/service.py::quote_stats` 里
查到一条 —— 报价统计把**已软删的报价及其版本**仍计入
`quote_count / average_versions / accept_rate / approval_rate / average_discount`
的分子分母；而同模块的 `product_stats` 明确写了"已删除的报价整单排除"，
两处口径本就该一致。

## 判据（只查"统计"，不查明细列表）

只对**聚合查询**报警：语句里同时出现
  · 软删模型（`select(func.count(Quote.id))` / `func.sum(SalesOrder.total_amount)`
    / `select(Quote)` 用于 `len(...)` 之类），且
  · 聚合函数 `func.count` / `func.sum` / `func.avg` / `func.min` / `func.max`，
而该**函数体**里没有 `deleted_at`。

**明细列表不查**：列表通常走分页 helper 或统一的可见性函数，误报率会高得多
（与 `check_model_attrs.py` 一样，宁可漏报也不刷屏）。

## 用法

    cd backend && PYTHONPATH=. .venv/bin/python scripts/check_soft_delete_filters.py
"""

from __future__ import annotations

import pathlib
import re
import sys

APP = pathlib.Path(__file__).resolve().parents[1] / "app"

#: 软删模型（从 model.py 的 `deleted_at: Mapped[...]` 扫出来的事实，不是手抄）
SOFT_DELETE_MODELS = {
    "Opportunity",
    "ContractTemplate",
    "ContractDocument",
    "CustomInquiry",
    "Quote",
    "SalesCase",
    "Product",
    "Sku",
    "Lead",
    "ProductInsight",
    "Customer",
    "Contact",
    "SalesTarget",
}

AGGREGATE = re.compile(r"\bfunc\.(count|sum|avg|min|max)\s*\(")
MODEL_USE = re.compile(r"\b(" + "|".join(sorted(SOFT_DELETE_MODELS)) + r")\b")
DELETED_FILTER = re.compile(r"deleted_at")

#: 已核实的例外：这些统计**故意**包含已删除行，或过滤发生在别处。逐条写明理由。
#: 登记而不删判据 —— 判据要拦的是"新写的统计忘了排软删"。
EXCEPTIONS: dict[tuple[str, str], str] = {
    # ── 聚合作用在**无软删**的模型上，软删模型只是旁查（显示名/归属判断）──
    ("app/modules/analytics/service.py", "delivery_stats"): (
        "指标（准时率/延迟天数/风险单）全部基于 `SalesOrder`（**无 deleted_at**）；"
        "`Customer` 只用于取显示名。"
    ),
    ("app/modules/opportunity/service.py", "enrichment"): (
        "聚合的是 `OpportunityItem`（无软删），且上层已对商机做过软删过滤；"
        "`Customer` 只用于取显示名。"
    ),
    ("app/modules/order/service.py", "order_context"): (
        "聚合的是 `PaymentRecord` / `SalesOrderItem`（均无软删）；"
        "`Customer` 只用于取显示名。"
    ),
    ("app/modules/wecom/service.py", "readiness"): (
        "数的是 `WeComUser` / `WeComExternalContact`（**企微侧**的同步健康检查，"
        "无软删概念）；`Contact` 只是注释里提到的字符串。"
    ),
    # ── 聚合的模型无软删 ──
    ("app/modules/customer/duplicates.py", "list_cases"): (
        "聚合的是 `CustomerDuplicateCase`（**无 deleted_at**，它自己用 status 表达"
        "已解决）；条件里已对 `Customer` 子查询加了软删过滤。"
    ),
    ("app/modules/settings/service.py", "list_candidates"): (
        "聚合的是 `PublicPoolRecycleCandidate`（无软删）；`Customer` 只用于取显示名。"
    ),
    ("app/modules/product/master.py", "_maybe_snapshot"): (
        "聚合的是 `SkuFieldAuthority`（无软删），按 `sku_id` 关联一个**已存在**的 SKU。"
    ),
    # ── 状态闸已经等价于软删过滤 ──
    ("app/modules/order/router.py", "cancel_order"): (
        "只数 `ContractDocument.status == 'signed'` 的合同 —— 作废/软删的合同"
        "`status` 就是 `void`，**状态闸已等价于软删过滤**，业务上正确"
        "（这条闸的目的正是「已签合同不许取消订单」）。"
    ),
    ("app/modules/inquiry/router.py", "status_summary"): (
        "用了同模块的 `svc.not_deleted(stmt)`（内部即 `deleted_at.is_(None)`）——"
        "判据只认字面 `deleted_at`，所以这条是**辅助函数命名**造成的假阳性。"
        "把过滤抽成命名函数是好写法，不该为此报警。"
    ),
}


def functions(text: str) -> list[tuple[str, int, str]]:
    out: list[tuple[str, int, str]] = []
    lines = text.split("\n")
    marks: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        match = re.match(r"^(\s*)(?:async )?def (\w+)\(", line)
        if match:
            marks.append((index, match.group(2)))
    for position, (index, name) in enumerate(marks):
        end = marks[position + 1][0] if position + 1 < len(marks) else len(lines)
        out.append((name, index + 1, "\n".join(lines[index:end])))
    return out


def main() -> int:
    findings: list[str] = []
    scanned = 0
    for path in sorted(APP.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for name, line, body in functions(text):
            scanned += 1
            if not AGGREGATE.search(body):
                continue
            if not MODEL_USE.search(body):
                continue
            if DELETED_FILTER.search(body):
                continue
            rel = path.relative_to(APP.parent)
            if (str(rel), name) in EXCEPTIONS:
                continue
            models = sorted(set(MODEL_USE.findall(body)))
            findings.append(f"  ✗ {rel}:{line} {name}()  涉及 {','.join(models)}")

    print(f"软删统计自检：扫了 {scanned} 个函数，命中 {len(findings)} 处")
    if not findings:
        print("✓ 统计类查询都排了软删（或有已登记的例外）")
        return 0
    for item in findings:
        print(item)
    print()
    print("处置：这些统计可能把**已删除的数据**算进去。确认要包含时，把理由写进")
    print("EXCEPTIONS（不要删判据）。过滤若在其他层，把 `deleted_at` 那一步的")
    print("函数名写进本函数体内即可被识别。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
