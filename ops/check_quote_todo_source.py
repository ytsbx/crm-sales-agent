#!/usr/bin/env python3
"""报价「这一步还差什么」清单 vs 页面真实闸门：两侧都不许漂移。

用法：
    cd frontend && python3 ../ops/check_quote_todo_source.py

## 为什么需要它

待办区（`shared/quote-todo.ts`）的价值全在**判得准**上，而它有两种坏法：

1. **编理由**：清单说"差运费"，页面上其实早就没这道闸了 —— 用户去点了，
   发现能点，从此不信这个区。
2. **漏闸门**：页面上新加了一道拦截（比如"客户逾期不许发送"），清单没跟上 ——
   用户又回到"自己找卡在哪"，而那正是这次要修掉的毛病。

单测（`ops/quote_todo.test.mjs`）只能证明"给定输入算得对"，证明不了
"清单里的门在页面上真的存在"。所以这里做静态核对：

- 清单里每一项的 `source`，都要能在**前端页面或后端源码**里找到对应判据；
- 清单里每一项的 `anchor`，都要是页面上真实存在的锚点 id；
- 清单里每一项的 `action`，都要是页面上真实存在的按钮文案；
- 页面/后端里已知的闸门，都要在清单里有对应项（**反向**：不许漏）。

判据以"页面/后端的源码文本"为准 —— 与项目里其他静态检查同一手法
（文案不能当判据，但"这道闸在不在"是结构事实，可以扫）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend" / "src"
TODO_SRC = FRONTEND / "shared" / "quote-todo.ts"
QUOTE_PAGE = FRONTEND / "modules" / "quote" / "QuoteDetailPage.tsx"
BACKEND = ROOT / "backend" / "app"

if not TODO_SRC.exists():
    print("✗ 找不到 frontend/src/shared/quote-todo.ts —— 路径变了？")
    sys.exit(1)

todo_text = TODO_SRC.read_text(encoding="utf-8")
page_text = QUOTE_PAGE.read_text(encoding="utf-8") if QUOTE_PAGE.exists() else ""

failures: list[str] = []


def fail(message: str) -> None:
    failures.append(message)


# ---------------------------------------------------------------- 解析清单

# 每项形如  key: 'xxx', 或 key: "xxx" 之后跟着 anchor / action / source
entries = re.findall(
    r"key:\s*'([a-z_]+)'.*?source:\s*'([^']+)'.*?anchor:\s*(null|'([a-z-]+)').*?action:\s*(null|'([^']+)')",
    todo_text,
    re.S,
)
if len(entries) < 8:
    fail(f"只解析出 {len(entries)} 项待办 —— 至少应有 8 项（各阶段 + 终态），解析规则可能失效了")

parsed = []
for key, source, anchor_raw, anchor, action_raw, action in entries:
    parsed.append(
        {
            "key": key,
            "source": source,
            "anchor": anchor or None,
            "action": action or None,
        }
    )

print(f"清单：解析出 {len(parsed)} 项待办")


# ---------------------------------------------------------------- ① source 要真的有判据

#: 每个 key 对应的"闸门"必须能在这些文件里找到。左边是 key，右边是
#: (要在哪个文件里找, 要找的片段)。片段写得**足够具体**，避免"文件里恰好有这个词"。
SOURCE_GATES: dict[str, tuple[Path, str]] = {
    "master_unconfirmed": (QUOTE_PAGE, "detail.master_warnings"),
    "freight_unconfirmed": (QUOTE_PAGE, "freightUnconfirmed"),
    "submit_approval": (QUOTE_PAGE, "提交审批"),
    "approval_pending": (QUOTE_PAGE, "approval_status === 'pending'"),
    "approval_rejected": (QUOTE_PAGE, "approval_status === 'rejected'"),
    "send_to_customer": (QUOTE_PAGE, "approval_status === 'approved'"),
    "await_customer": (QUOTE_PAGE, "canRespond"),
    "convert_to_order": (QUOTE_PAGE, "转销售订单"),
    "declined": (QUOTE_PAGE, "客户拒绝"),
    "converted": (QUOTE_PAGE, "quote.status"),
}

for item in parsed:
    gate = SOURCE_GATES.get(item["key"])
    if gate is None:
        fail(f"清单里的 '{item['key']}' 没有登记对应闸门 —— 新加项请同时在 SOURCE_GATES 里写明它来自哪")
        continue
    path, needle = gate
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if needle not in text:
        fail(
            f"'{item['key']}' 声称的判据在 {path.name} 里找不到（找的是 {needle!r}）—— "
            "要么这道闸被去掉了（清单该删），要么改写法了（清单的 source 该更新）"
        )


# ---------------------------------------------------------------- ② anchor 要真的存在

anchors = set(re.findall(r'id="([a-z-]+)"', page_text))
for item in parsed:
    if item["anchor"] and item["anchor"] not in anchors:
        fail(
            f"'{item['key']}' 指向锚点 #{item['anchor']}，但页面上没有这个 id —— "
            f"当前页面锚点：{sorted(anchors) or '（无）'}"
        )


# ---------------------------------------------------------------- ③ action 要真的是页面上的按钮

def _button_labels(text: str) -> set[str]:
    """页面上真实的按钮文案。

    这个函数我改了三版，把踩过的坑记下来（否则后来人会重蹈）：

    1. 只用一个正则 `<Button[^>]*>\s*([^<>{}]{2,20}?)\s*</Button>`：
       只认"开闭标签紧挨着"的单行写法，本页按钮几乎都是多行 JSX → 只解析出 3 个，
       反过来把 7 个真实存在的按钮判成"页面上没有"。
    2. 换成 `<Button\b[^>]*?>` 配平：**仍然是错的** —— `[^>]` 会在
       **属性值里的 `>`** 处提前停下，而 `onClick={() => x.mutate(false)}` 里就有一个 `>`。
       于是从 `<Bu` 开始扫，数组下标全乱，`新建版本 / 提交审批 / 转销售订单` 一个都认不出。
    3. 现在：**逐字符扫描**，正确跳过引号字符串与 `{}` 表达式，
       再按标签配平取出这一对 `<Button>…</Button>`；有 `</Button>` 就截到它为止。
    """
    labels: set[str] = set()

    def _skip_quoted(s: str, i: int) -> int:
        quote = s[i]
        i += 1
        while i < len(s) and s[i] != quote:
            i += 2 if s[i] == "\\" else 1
        return i + 1

    def _skip_braces(s: str, i: int) -> int:
        depth = 0
        while i < len(s):
            ch = s[i]
            if ch in "\"'`":
                i = _skip_quoted(s, i)
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return i + 1
            i += 1
        return i

    cursor = 0
    while True:
        open_tag = text.find("<Button", cursor)
        if open_tag < 0:
            break
        after = open_tag + len("<Button")
        if after < len(text) and (text[after].isalnum() or text[after] == "_"):
            cursor = after
            continue
        # 找到这个开标签真正的结束 '>'
        i = after
        while i < len(text):
            ch = text[i]
            if ch in "\"'`":
                i = _skip_quoted(text, i)
                continue
            if ch == "{":
                i = _skip_braces(text, i)
                continue
            if ch == ">":
                break
            i += 1
        tag_end = i + 1
        if tag_end >= len(text) or (tag_end - 2 >= 0 and text[tag_end - 2] == "/"):
            cursor = tag_end
            continue
        # 配平找 </Button>
        depth = 1
        j = tag_end
        while depth > 0 and j < len(text):
            nxt_open = text.find("<Button", j)
            nxt_close = text.find("</Button>", j)
            if nxt_close < 0:
                break
            if 0 <= nxt_open < nxt_close:
                depth += 1
                j = nxt_open + len("<Button")
            else:
                depth -= 1
                j = nxt_close + len("</Button>")
        inner = text[tag_end : max(j - len("</Button>"), tag_end)]
        if "</Button>" in inner:
            inner = inner[: inner.index("</Button>")]
        cursor = max(j, tag_end)

        for literal in re.findall(r"'([^']{2,20})'|\"([^\"]{2,20})\"", inner):
            value = literal[0] or literal[1]
            if re.search(r"[\u4e00-\u9fa5]", value):
                labels.add(value.strip())
        plain = re.sub(r"\{[^{}]*\}", "", inner)
        plain = re.sub(r"<[^>]+>", "", plain).strip()
        if plain and re.search(r"[\u4e00-\u9fa5]", plain) and len(plain) <= 20:
            labels.add(plain)
    return labels


button_labels = _button_labels(page_text)
for item in parsed:
    if item["action"] and item["action"] not in button_labels:
        fail(
            f"'{item['key']}' 让用户点「{item['action']}」，但页面上没有这个文案的按钮 —— "
            f"当前按钮：{sorted(button_labels)}"
        )


# ---------------------------------------------------------------- ④ 反向：页面上的闸门不许漏

#: 页面上"会拦住推进"的闸门，必须都能在清单里被说出来。这些串是**结构事实**
#: （判据变量名 / 状态分支），不是文案，所以可以扫。
MUST_BE_COVERED: tuple[tuple[str, Path, str], ...] = (
    ("主数据未确认会拦正式发送", QUOTE_PAGE, "detail.master_warnings"),
    ("运费未确认会拦提交审批", QUOTE_PAGE, "freightUnconfirmed"),
    ("审批中", QUOTE_PAGE, "approval_status === 'pending'"),
    ("审批未通过", QUOTE_PAGE, "approval_status === 'rejected'"),
    ("待发送（已通过未发送）", QUOTE_PAGE, "approval_status === 'approved'"),
    ("客户答复（接受/拒绝）", QUOTE_PAGE, "canRespond"),
)
covered_keys = {item["key"] for item in parsed}
for label, path, needle in MUST_BE_COVERED:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if needle not in text:
        # 闸门本身没了：那不是清单的错，是页面的事，这里只提示
        print(f"  · 注意：页面上找不到「{label}」这道闸（{needle!r}）—— 若是有意去掉，请同步删清单项")
        continue
    if not covered_keys:
        fail("清单解析为空，无法核对覆盖")

# 明确要求：上面每道闸都要有对应的清单项
REQUIRED_KEYS = {
    "master_unconfirmed",
    "freight_unconfirmed",
    "submit_approval",
    "approval_pending",
    "approval_rejected",
    "send_to_customer",
    "await_customer",
    "convert_to_order",
}
missing = REQUIRED_KEYS - covered_keys
if missing:
    fail(f"页面上的这些闸门在清单里没有对应项（会漏报）：{sorted(missing)}")


# ---------------------------------------------------------------- ⑤ 页面上真的在渲染这份清单

if "quoteTodos(" not in page_text:
    fail("QuoteDetailPage 没有调用 quoteTodos() —— 清单算了但没显示，等于没做")
if "quote-todo" not in page_text:
    fail("QuoteDetailPage 没有从 shared/quote-todo 引入 —— 判据可能被复制了一份，会漂移")


# ---------------------------------------------------------------- ⑥ 闸门要真的被渲染出来

#: 这些闸门不能只是"算了一下"：必须在页面上**真的渲染**，用户看得见。
#:
#: ⚠️ 判据不能写成"变量在文件里出现 ≥2 次" —— 我第一版就是这么写的，
#: **实测漏报**：删掉整个渲染块之后，这个变量在别处还出现 8 次
#: （计算清单、提交弹窗的 disabled、注释），计数照样 ≥2。
#: 换成**要求存在真实的渲染条件**：`{gate &&`（真值守卫）或 `gate.length > 0 &&`。
RENDERED_GATES: tuple[tuple[str, str], ...] = (
    ("detail.master_warnings", "主数据未确认的提示块"),
    ("freightUnconfirmed", "运费未确认的提示"),
)
for name, label in RENDERED_GATES:
    # 允许包一层括号：页面上真实写法是 `{(detail.master_warnings ?? []).length > 0 && (`
    # —— 我第一版没容忍括号，于是把它判成"没渲染"（反向验证时误报）。
    # 要求**真的渲染一块内容**：`{guard && (` 后面紧跟一个 JSX 元素。
    # ⚠️ 只写 `{gate &&` 还不够 —— 实测漏报：提交弹窗里还有
    # `okButtonProps={{ disabled: Boolean(freightUnconfirmed) }}` 也匹配，
    # 于是"只灰一个按钮、页面上不再说明"也算通过，而那正是要防的坏法。
    guard = re.search(
        rf"\{{\s*\(?\s*{re.escape(name)}\s*(?:\?\?\s*\[\s*\]\s*\)?)?\s*(?:\.length\s*>\s*0\s*)?&&\s*\(\s*<",
        page_text,
    )
    if guard is None:
        fail(
            f"「{label}」在页面上没有渲染条件（找不到 `{{ {name} &&` 或 `{name}.length > 0 &&`）"
            " —— 闸门可能只算不显示：清单会一直提示它，用户却看不到那行说明"
        )

print()
if failures:
    print(f"✗ 发现 {len(failures)} 处问题：")
    for message in failures:
        print(f"  - {message}")
    sys.exit(1)
print("✓ 待办区的每一项都能对上真实闸门；页面上的闸门也都说得出来；锚点与按钮都存在")
