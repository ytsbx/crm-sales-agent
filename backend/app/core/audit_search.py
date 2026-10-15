"""审计日志的**中文**检索：把界面上显示的中文，反查回库里的英文码。

## 为什么需要它

审计页上用户看到的是中文（「定制需求」「修改状态」），因为前端有一层翻译。
但库里存的是**内部码**（`custom_inquiry`、`update_status`）。

原来的 `keyword` 筛选只在 `action` / `business_type` 两个**英文码**字段上做
`ilike` —— 于是用户在搜索框里打「定制需求」，**一个都搜不到**：

    界面上显示    库里存的
    「定制需求」 ←→ 'custom_inquiry'
    「打样单」   ←→ 'sample'

主人 2026-10-15 的原话是「现在太乱了，也没有搜索功能」——
带宽有了、接口参数早就有，**缺的就是中文这一层**。

## 中文名的真源在哪

- **对象类型**：前端 `frontend/src/shared/audit-labels.ts` 的 `AUDIT_TYPE_LABEL`
  → 由 `ops/gen_audit_type_labels.py` 生成 `app/core/audit_type_labels.json`
- **动作**：`app/core/audit_action_labels.json`（它才是真源，前端 TS 由它生成）

**这里不手抄**：两份都从 JSON 读。中文名改了而忘了同步，
表现是"搜得到旧词、搜不到新词"——这种毛病在界面上很难查出来。

## 一个刻意的取舍：歧义词不参与"包含匹配"

有些中文名对应多个码（比如两个动作都叫「新增明细」）。如果用户打「明细」，
按包含匹配会把两个码都捞进来 —— 那通常是**用户想要的**（他就要看所有带"明细"的），
但也可能把不相干的记录混进来。

所以这里的规矩是：

- **完全等于某个中文名** → 该中文名对应的**所有**码都算命中（用户就是要这个词）；
- **只是包含关系**（打字打了一半）→ 只认**一对一**的那些中文名，
  歧义的（一个名字对多个码）不参与 —— 宁可不匹配，也不给一堆看着像错的记录。

这样"打全称"精确、"打半截"也基本够用，且不会因为歧义把结果搞脏。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

#: 中文名 → 码 的两份真源
_TYPE_LABELS_PATH = Path(__file__).with_name("audit_type_labels.json")
_ACTION_LABELS_PATH = Path(__file__).with_name("audit_action_labels.json")


@lru_cache(maxsize=1)
def _type_labels() -> dict[str, str]:
    """`{对象类型码: 中文名}`。"""
    return json.loads(_TYPE_LABELS_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _action_labels() -> dict[str, str]:
    """`{动作码: 中文名}`。"""
    return json.loads(_ACTION_LABELS_PATH.read_text(encoding="utf-8"))


def _reverse(labels: dict[str, str]) -> dict[str, list[str]]:
    """`{中文名: [码, ...]}` —— 一个中文名可能对应多个码。"""
    out: dict[str, list[str]] = {}
    for code, label in labels.items():
        out.setdefault(label, []).append(code)
    return out


def match_codes(keyword: str, labels: dict[str, str]) -> set[str]:
    """给定用户输入，返回它可能指向的**码**集合。

    规则见模块文档：完全等于 → 全给；只是包含 → 只给一对一的名字。
    输入是英文码片段时也直接命中（有人习惯照库里的码搜）。
    """
    text = (keyword or "").strip()
    if not text:
        return set()

    reverse = _reverse(labels)
    hits: set[str] = set()

    # ① 完全等于某个中文名（或某个码）—— 精确命中，歧义也全给
    if text in reverse:
        hits.update(reverse[text])
    if text in labels:
        hits.add(text)

    # ② 部分匹配：中文名包含输入（打半截），只认一对一的名字
    for label, codes in reverse.items():
        if len(codes) == 1 and text in label:
            hits.add(codes[0])

    # ③ 英文码片段：直接当码用（`ilike` 交给 SQL 做，这里给个精确值没意义，
    #    所以只在"输入是纯 ASCII"时补一个原样返回，供 SQL 做 `ilike`）
    if text.isascii():
        hits.add(text)

    return hits


def business_type_codes(keyword: str) -> set[str]:
    """输入可能指向的对象类型码。"""
    return match_codes(keyword, _type_labels())


def action_codes(keyword: str) -> set[str]:
    """输入可能指向的动作码。"""
    return match_codes(keyword, _action_labels())


def label_lookup_codes(keyword: str) -> tuple[set[str], set[str]]:
    """一次给出 `(对象类型码, 动作码)` —— 调用方少读两次表。"""
    return business_type_codes(keyword), action_codes(keyword)
