#!/usr/bin/env python3
"""登录态（持久化的 user）**不许被当成"字段一定存在"**。

## 为什么要有这道检查（2026-10-13，真崩过）

`crm-auth` 是 **localStorage 持久化**的登录态，旧版本留下的那份数据会被新版
代码直接读出来用。而登录接口的 `_user_brief` 只返回 `{id, name, username}` ——
**没有 `roles`**。两者一叠加：

```
TypeError: Cannot read properties of undefined (reading 'includes')
    at hasPermission (src/app/menu.ts:240:17)
    at src/app/layout/AppLayout.tsx:206:23
```

`hasPermission` 在 `AppLayout` 里对**左侧每一个菜单项**都调一次，
所以它不是"某个页面坏了"，而是**每一页都崩**，用户看到的是 React 的英文报错
和调用栈（`Unexpected Application Error!` + `at hasPermission (...)`）——
既是白屏，又完全不是人话。排查脚本实测复现、反向验证过。

TypeScript 拦不住：类型标注写的是 `roles: string[]`，但数据是从 localStorage
读出来的 JSON，**类型只是"我以为的形状"**。所以这类地方必须写防御。

## 判据

扫所有从 `useAuthStore` 取 `user` / `currentUser` 的**组件与模块文件**，
找出对这几个字段的直接成员访问（`user.roles.includes` 这种），要求写成
`user?.roles` / `user?.roles ?? []` / 先判空。

**判定为安全、不报的写法**（这些在本项目里是允许的）：
- 可选链：`user?.roles`、`user?.name?.slice(...)`
- 先兜底成数组：`const roles = user?.roles ?? []`、`(user?.roles ?? [])`
- 只取到 `user` 本身再用（`user?.id`、`if (!user) return`）

**唯一允许的例外**在下面的 `ALLOWED` 里，每条都要写明理由。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "frontend" / "src"

#: 登录态里"字段可能缺"的那几个（对应 `fetchMe()` 的返回形状）。
FIELDS = ("roles", "permissions", "department", "data_scope", "name", "username", "id")

#: 例外：`文件:行` → 理由。**必须写理由**，否则下次没人敢动这里。
ALLOWED: dict[str, str] = {}

#: "这个文件碰了登录态的 user" 的判据。
#:
#: ⚠️ **不能只认"从 store 取 user"**（我第一版就是这样，于是**恒绿**）：
#: 真正崩的 `hasPermission` 在 `menu.ts` 里，它是**接收 user 当参数**的工具函数，
#: 文件里根本没有 `useAuthStore(...)` 那一行 —— 整份文件被跳过，
#: 反向验证三种写法全都不红。所以判据放宽成"文件里出现了登录态的 user"，
#: 再用下面 `offender_lines` 的两步数据流去伪（避免把接口返回的 user 列表误报）。
STORE_READ = re.compile(
    r"useAuthStore\(\(state\) => state\.user\)|\buser\s*:\s*\{[^}]*\broles\b"
)
#: 有问题的直取：`user.roles` / `currentUser.permissions.includes` 之类。
BAD_ACCESS = re.compile(
    r"\b(user|currentUser|authUser|me)\.(" + "|".join(FIELDS) + r")\b"
)
#: 安全的写法特征（可选链、判空、取到变量再兜底）。
SAFE_PREFIX = re.compile(r"\?\.$")


def _safe_assignment(code: str, field: str) -> bool:
    """这一行把登录态字段取出来时有没有兜底。

    安全的两种写法：
      · `const roles = user?.roles ?? []`   —— 可选链 + `?? []`
      · `const roles = user?.roles || []`
    不安全的：
      · `const roles = user.roles`          —— 旧数据里字段不存在就是 undefined
      · `const roles = user?.roles`         —— 仍然可能是 undefined
    """
    # 对象侧的可选链（`u?.roles`）与 `?? []` 都算兜底 —— 两处都要允许，
    # 只认其中一种会把 `user?.roles ?? []` 误判成危险（我踩过）。
    for pattern in (
        rf"=\s*\w+\??\.{field}\s*\?\?\s*\[\s*\]",
        rf"=\s*\w+\??\.{field}\s*\|\|\s*\[\s*\]",
        rf"=\s*\w+\?\.{field}\s*\?\?\s*0",
    ):
        if re.search(pattern, code):
            return True
    return False


def offender_lines(path: Path, changed: set[int] | None = None) -> list[tuple[int, str]]:
    """找出"把登录态字段当一定存在"的地方（只报改动行，见 main）。

    ⚠️ 判定走**两步数据流**，不能只看一行（第一版只看一行，恒绿）：

        const roles = user.roles ?? []      ← 安全
        if (roles.includes('admin'))        ← 用的时候已经保证是数组，也安全

    而这两种是危险的：

        if (user.roles.includes('admin'))   ← 直接取字段，没有 `?.`
        const roles = user.roles            ← 取出来没兜底，下面 roles.includes 就崩
        if (roles.includes('admin'))

    所以：① 先收集"从登录态字段派生、且**没有兜底**"的变量名；② 再看它们有没有被调用。
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    if not any(STORE_READ.search(line) for line in lines):
        return []

    fields = "|".join(FIELDS)
    unsafe_vars: set[str] = set()
    for line in lines:
        code = line.split("//")[0]
        m = re.search(rf"\b(?:const|let|var)\s+(\w+)\s*=\s*\w+\??\.({fields})\b", code)
        if m and not _safe_assignment(code, m.group(2)):
            unsafe_vars.add(m.group(1))

    found: list[tuple[int, str]] = []
    for index, line in enumerate(lines, start=1):
        if changed is not None and index not in changed:
            continue
        code = line.split("//")[0]
        stripped = code.strip()
        if not stripped or stripped.startswith("*"):
            continue
        # 赋值行交给"派生变量"那套判定（`const roles = user.roles` 是否兜底），
        # 否则 `const roles = user.roles ?? []` 会被这里误报成"直接取字段"。
        if re.search(rf"\b(?:const|let|var)\s+\w+\s*=\s*\w+\??\.(?:{fields})\b", code):
            pass
        # ① 直接取字段（没有 `?.`）
        hit = False
        for match in (BAD_ACCESS.finditer(code) if not re.search(rf"\b(?:const|let|var)\s+\w+\s*=\s*\w+\??\.(?:{fields})\b", code) else []):
            if SAFE_PREFIX.search(code[: match.start(2)]):
                continue
            found.append((index, stripped))
            hit = True
            break
        if hit:
            continue
        # ② 没兜底的派生变量被调用（或当数组用）
        for name in sorted(unsafe_vars):
            if re.search(rf"\b{re.escape(name)}\??\.\w+\(", code) or re.search(
                rf"\b{re.escape(name)}\??\.length\b", code
            ):
                found.append((index, stripped))
                break
    return found


def main() -> int:
    problems: list[str] = []
    scanned = 0
    for path in sorted(SRC.rglob("*.ts*")):
        if path.suffix not in (".ts", ".tsx"):
            continue
        if "/api/" in str(path):
            continue
        scanned += 1
        for line_no, text in offender_lines(path):
            key = f"{path.relative_to(REPO)}:{line_no}"
            if key in ALLOWED:
                continue
            problems.append(f"{key}  {text[:110]}")

    print(f"扫描 {scanned} 个文件：读登录态的地方检查完毕")
    if problems:
        print()
        print("✗ 这些地方把登录态字段当成「一定存在」—— 旧版本的持久化数据会让它们抛异常：")
        for item in problems:
            print(f"    {item}")
        print()
        print("  改法：可选链 + 兜底（`user?.roles ?? []`）。")
        print("       TypeScript 拦不住这个：数据是 localStorage 里的 JSON，类型只是「我以为的形状」。")
        return 1
    print("✓ 登录态字段处处都做了兜底")
    return 0


if __name__ == "__main__":
    sys.exit(main())
