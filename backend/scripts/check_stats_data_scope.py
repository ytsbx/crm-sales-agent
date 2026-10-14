#!/usr/bin/env python3
"""静态检查：**统计/仪表盘类查询有没有套数据范围**。

## 为什么

统一的数据范围入口是 `app/core/data_scope.py`：
  · `scoped_owner_ids(session, user)` —— 返回可见的负责人 id 列表（`None` = 全部可见）；
  · `ensure_in_scope(...)` —— 对**单个**对象判范围（明细/详情用）。
各模块在此之上包了自己的语句级 helper：
  · `analytics/service.py::_scope_filter(stmt, user, column, session)`
  · `inquiry/service.py::apply_scope(stmt, user, session)`

这条判据来自一个**反复出现的真实缺陷**（2026-10-14 审查）：

  · 工作台「待审批」用的是**全公司**计数，而点进去只列自己的 ——
    同一个数字在两处口径不同（`dashboard_summary`）；
  · 团队「本月成交」用 `Opportunity.updated_at >= 月初` 判"本月"，
    于是**改一次备注就把几个月前的成交算进本月**（`team_summary`）；
  · AI 建议里的**商机**计数既无权限也无数据范围，而紧邻的**任务**计数两道都有。

三处的共同点：**详情路径有范围、统计路径漏了**。数字对不上时没人会去查代码，
所以适合静态守。

## 判据（只查"聚合"，不查明细列表）

对**含聚合函数**（`func.count/sum/avg/min/max`）的函数报警，当它同时满足：
  · 查询里出现了**按负责人划分数据**的模型（下表）；
  · 且函数体里**没有**任何范围入口（`_scope_filter` / `apply_scope` /
    `scoped_owner_ids` / `ensure_in_scope` / `followup_visibility_filter` 等）。

**明细列表不查**：列表几乎都走统一分页 helper，报出来全是噪声。

## 用法

    cd backend && PYTHONPATH=. .venv/bin/python scripts/check_stats_data_scope.py
"""

from __future__ import annotations

import pathlib
import re
import sys

APP = pathlib.Path(__file__).resolve().parents[1] / "app"

#: 按 `owner_id` 划数据范围的模型（有 `owner_id` 列的那些）
OWNER_SCOPED = {
    "Customer",
    "Contact",
    "Lead",
    "Opportunity",
    "Quote",
    "SalesOrder",
    "SalesCase",
    "CustomInquiry",
    "SampleRequest",
    "SalesTarget",
    "Task",
    "ApprovalInstance",
}

AGGREGATE = re.compile(r"\bfunc\.(count|sum|avg|min|max)\s*\(")
MODEL_USE = re.compile(r"\b(" + "|".join(sorted(OWNER_SCOPED)) + r")\b")
#: ⚠️ 判据**只能看代码，不能看注释**。
#: 我第一版把 `data_scope` 也当闸名，结果 `dashboard_summary` 里那句
#: 「于是 data_scope=self 的业务员在工作台看到**全公司**待审批数」——
#: 一句**描述缺陷的注释**——就把假阴性放过去了（反向验证时"撤掉范围过滤"仍然全绿）。
#: 注释里出现判据关键词是常态（尤其"以前这里漏了 X"这类说明），必须剥掉再判。
def strip_comments_and_strings(code: str) -> str:
    """去掉整行注释与行尾注释，保留代码行。"""
    out = []
    for line in code.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        out.append(line[:indent] + stripped.split("  #")[0])
    return "\n".join(out)


#: 另一条同样有效的路径：**调用方把已定范围的 id 列表传进来**
#: （`contact_counts(session, customer_ids)`、`stage_counts_map(session, customer_ids)`…）。
#: 这类函数本身不该自己再判范围 —— 强行加一道反而会跟调用方冲突。
CALLER_SCOPED = re.compile(r"\b\w+_ids\b")

#: ⚠️ 闸名**不要手工枚举** —— 我前三版就是这么写的，连着漏了
#: `apply_data_scope`、`scoped(`、`get_visible_or_404` 三个真实存在的入口，
#: 每次都是"检查报出来 → 一看发现是合法闸 → 补一个名字"。
#: 这个仓库的闸名有强规律：**标识符里带 `scope` 或 `visible`**。
#: 按规律判，新写的 `xxx_scope` / `get_visible_xxx` 自动被认。
SCOPE_GATE_PATTERN = re.compile(r"\w*(?:scope|visible)\w*", re.I)

#: 少数不带这两个词的入口，单独列出（已核实的）
SCOPE_GATES_EXTRA = (
    "not_deleted(",  # inquiry/service.py：内部即 `deleted_at.is_(None)`，但它**只管软删**
)

#: `.in_(...)` 里若出现 `owner` 也说明按负责人收窄了
OWNER_IN = re.compile(r"\.in_\(\s*\w*owner", re.I)

#: 已核实的例外：这些统计**故意**是全公司口径（或不按负责人划分）。
EXCEPTIONS: dict[tuple[str, str], str] = {
    ("app/modules/customer/service.py", "count_of"): (
        "它把**明细的 base 语句**折成计数（`select(func.count()).select_from(base...)`）——"
        "范围由调用方在建 base 时套好（`scoped(select(Opportunity...), Opportunity)`）。"
        "这个写法本身正是为了「计数与明细同一份 where，口径不可能再漂」，不该再自己判范围。"
    ),
    ("app/modules/payment/service.py", "order_finance_summary"): (
        "接收单个 `order_id`；调用方 `payment/router.py` 先 `get_order_or_404` + "
        "`assert_order_visible` 才调它（另一个调用方是 AI 工具，用的是已过范围的 `order` 对象）。"
    ),
    ("app/modules/order/service.py", "create_shipment_batch"): (
        "只为**单个订单**取下一个批次序号（`func.max(OrderShipmentBatch.batch_no)`）；"
        "调用方路由先 `get_visible_order`。取序号不需要范围。"
    ),
    ("app/modules/lead/io_router.py", "import_leads"): (
        "那条 `func.count(Lead.id)` 是**同一批导入内的查重**（按手机号/公司名比对本批已有行），"
        "不是跨库统计；跨范围时它反而查不到，属于安全的方向。"
    ),
    ("app/modules/payment/service.py", "serialize_payment"): (
        "序列化**单条**回款记录；范围在取这条记录时已经判过。"
    ),
    ("app/modules/wecom/service.py", "readiness"): (
        "**管理员诊断**用的健康检查（企微同步就绪度：绑定数、待归一数）——"
        "按定义就是全公司视角，且入口需要管理权限。"
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
            # 只看代码：注释里常常写着"以前这里漏了范围"这类说明，会被当闸名误判
            code_only = strip_comments_and_strings(body)
            if SCOPE_GATE_PATTERN.search(code_only):
                continue
            if any(gate in code_only for gate in SCOPE_GATES_EXTRA):
                continue
            if OWNER_IN.search(code_only):
                continue
            if CALLER_SCOPED.search(body):
                continue
            # 单对象/内存计数不算"统计查询"：形如 `tally(outcome)`、
            # `serialize_payment(session, record)` —— 范围在调用方已经判过。
            if "select(" not in body and "session.execute" not in body:
                continue
            rel = path.relative_to(APP.parent)
            if (str(rel), name) in EXCEPTIONS:
                continue
            models = sorted(set(MODEL_USE.findall(body)))
            findings.append(f"  ✗ {rel}:{line} {name}()  涉及 {','.join(models)}")

    print(f"统计范围自检：扫了 {scanned} 个函数，命中 {len(findings)} 处")
    if not findings:
        print("✓ 统计类查询都套了数据范围（或有已登记的例外）")
        return 0
    for item in findings:
        print(item)
    print()
    print("处置：统计数字可能与详情页口径不一致（详情有范围、统计没有）。")
    print("确认应当是全公司口径时，把理由写进 EXCEPTIONS；范围过滤若在调用方，")
    print("把该 helper 名写进本函数体即可被识别。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
