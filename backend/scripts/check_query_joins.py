#!/usr/bin/env python3
"""静态检查：**查询里引用的模型有没有对应的 JOIN**（防笛卡尔积）。

## 为什么（2026-10-14 真实发生）

我在 `contract/service.py` 的台账查询里加了一条数据范围判据，用到了
`SalesOrder.owner_id` —— 但那个 `select(ContractDocument).join(Customer, ...)`
**只 join 了 `Customer`**。SQLAlchemy 对"WHERE 里出现但没 join 的表"不会报错，
而是把它加进 `FROM`：

    FROM contract_documents, sales_orders ...

于是变成**笛卡尔积**：行数被放大、分页与 `total` 全错。
CI 上 `check_contract_documents` 四条断言同时红才被发现 —— **而我本地没跑那个套件**。

这类错误有两个特点，所以特别适合静态守：
  · **语法完全合法**，`pyflakes` 与类型检查都看不见；
  · 症状是"数字不对"，不是报错，人工审查很容易滑过去。

## 判据

对每个 `select(...)` 链，收集：
  · **引用了哪些模型**（出现在 `.where(...)` / `.order_by(...)` 等位置的 `Model.attr`）；
  · **join 了哪些模型**（`.join(X, ...)` / `.outerjoin(X, ...)`）。
在 `.where(...)` 里出现、但既没 join、也不是 `select()` 主体或 `select_from()` 的模型
→ 报警。

**只查 `.where()`**（范围内最明确的一处）：`order_by`/`group_by` 里引用未 join 模型
同样会笛卡尔积，但误报率更高（有时是刻意 cross join），先不查。

## 用法

    cd backend && PYTHONPATH=. .venv/bin/python scripts/check_query_joins.py
"""

from __future__ import annotations

import ast
import pathlib
import sys

APP = pathlib.Path(__file__).resolve().parents[1] / "app"

#: 已核实的例外（`(文件, 函数名)` → 理由）
EXCEPTIONS: dict[tuple[str, str], str] = {
    ("app/modules/analytics/service.py", "sales_user_stats"): (
        "相关子查询写法：`select(User.id, User.name, select(func.count(Customer.id))"
        ".where(Customer.owner_id == User.id).scalar_subquery(), ...)` —— "
        "`Customer`/`Opportunity`/`Quote` 等都在**各自的子查询**里（各有自己的 FROM），"
        "不是外层链的笛卡尔积来源。本检查的解析器**不进入嵌套 select**（这是刻意的："
        "进了反而要在子查询里重新判定一遍 join），所以对这种「子查询直接作为 "
        "select() 参数」的写法会误报。人工确认：外层只 select `User`，无笛卡尔积。"
    ),
}


def _model_name(node: ast.AST) -> str | None:
    """`SalesOrder.owner_id` → `SalesOrder`；`Customer.id` → `Customer`。"""
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        name = node.value.id
        return name if name[:1].isupper() else None
    return None


def _is_select_call(node: ast.Call) -> bool:
    func = node.func
    return (getattr(func, "attr", "") == "select") or (
        isinstance(func, ast.Name) and func.id == "select"
    )


def _walk_shallow(node: ast.AST):
    """遍历该节点，但**不进入嵌套的 `select(...)` 子查询**。"""
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, ast.Call) and _is_select_call(cur):
            continue
        yield cur
        for child in ast.iter_child_nodes(cur):
            stack.append(child)


def _scan_chain(node: ast.AST) -> tuple[set[str], set[str], set[str]]:
    """从 `select(...)` 链收集 (主体模型, join 的模型, where 里引用的模型)。"""
    base: set[str] = set()
    joined: set[str] = set()
    where_used: set[str] = set()

    def walk(call: ast.Call) -> None:
        func = call.func
        attr = func.attr if isinstance(func, ast.Attribute) else ""
        if attr == "join" or attr == "outerjoin":
            for arg in call.args:
                if isinstance(arg, ast.Name) and arg.id[:1].isupper():
                    joined.add(arg.id)
                elif isinstance(arg, ast.Attribute):
                    m = _model_name(arg)
                    if m:
                        joined.add(m)
            for sub in call.args:
                walk(sub) if isinstance(sub, ast.Call) else None
            return
        if attr == "select_from":
            for arg in call.args:
                if isinstance(arg, ast.Name) and arg.id[:1].isupper():
                    joined.add(arg.id)
            return
        if attr == "where":
            # ⚠️ **不要把嵌套 `select(...)` 子查询里的模型算进来**（我第一版算进来了，
            # 于是 `sales_user_stats` 那种"相关子查询"被误报）：子查询有自己的
            # FROM，里面的模型由它自己负责，不是外层链的笛卡尔积来源。
            for sub in call.args:
                for inner in _walk_shallow(sub):
                    if isinstance(inner, ast.Call) and _is_select_call(inner):
                        continue  # 子查询内部跳过
                    if isinstance(inner, ast.Attribute):
                        m = _model_name(inner)
                        if m:
                            where_used.add(m)
        if attr == "select" or (isinstance(func, ast.Name) and func.id == "select"):
            for arg in call.args:
                if isinstance(arg, ast.Name) and arg.id[:1].isupper():
                    base.add(arg.id)
                elif isinstance(arg, ast.Call):
                    walk(arg)
        # 继续沿链往内走（`.select(...).join(...).where(...)`）
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Call):
            walk(func.value)

    if isinstance(node, ast.Call):
        walk(node)
    return base, joined, where_used


def main() -> int:
    findings: list[str] = []
    scanned = 0
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = path.relative_to(APP.parent)
        for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            for node in ast.walk(fn):
                if not isinstance(node, ast.Call):
                    continue
                attr = node.func.attr if isinstance(node.func, ast.Attribute) else ""
                func = node.func
                is_select = attr == "select" or (
                    isinstance(func, ast.Name) and func.id == "select"
                )
                if not is_select:
                    continue
                scanned += 1
                base, joined, where_used = _scan_chain(node)
                allowed = base | joined
                missing = sorted(m for m in where_used if m not in allowed)
                if not missing:
                    continue
                if (str(rel), fn.name) in EXCEPTIONS:
                    continue
                findings.append(
                    f"  ✗ {rel}:{node.lineno} {fn.name}()  where 里引用了 "
                    f"{'、'.join(missing)}，但这条链没有 join 它"
                )
    print(f"查询 JOIN 自检：扫了 {scanned} 条 select，命中 {len(findings)} 处")
    if not findings:
        print("✓ where 里引用的模型都在这条链的 FROM/JOIN 里")
        return 0
    for item in findings:
        print(item)
    print()
    print("处置：没 join 的表会被 SQLAlchemy 加进 FROM → **笛卡尔积**（行数放大、")
    print("分页与 total 全错，而且不报错）。补 `.join()` / `.outerjoin()` 即可；")
    print("确实要 cross join 的把理由写进 EXCEPTIONS。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
