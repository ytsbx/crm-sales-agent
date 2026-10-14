"""把**服务端自己拼出来的**文本收进列宽之内。

## 为什么需要这个

有一类 500 是"服务端自己拼的串超了列宽"，跟用户输入无关：

    title = f"{DOC_TYPE_LABEL[doc_type]}-{customer.name}"   # 写进 String(200)
    reason = f"废弃：{reason}"                                # 写进 String(255)

两段都是**合法输入**，拼起来就越界：客户名本身允许 200 字（`String(200)`），
再加一个"销售合同-"前缀就是 204；废弃原因接口允许 255 字，加 3 字前缀是 258。
PostgreSQL 会抛 `value too long for type character varying(N)` → 冒成 **500**，
用户只看到"服务器内部错误"，既不知道哪里长、也不知道该怎么改。
（SQLite 单测不校验 varchar 长度，所以这类问题**测不出来**。）

## 口径

- **只在服务端拼串的落库路径上用**：用户直接填的字段应当由 schema 的
  `max_length` 挡住并给 422（那种错用户能改），而不是在这里悄悄截断；
- 截断加省略号（`…`），让"被截过"看得出来，而不是无声丢字；
- 留了余量：`suffix` 是拼在前缀之后还要额外占位的东西（例如"（副本）"）。
"""

from __future__ import annotations

#: 省略号本身占 1 个字符
ELLIPSIS = "\u2026"


def fit_column(text: str | None, limit: int, *, prefix: str = "", suffix: str = "") -> str:
    """把 `prefix + text + suffix` 收进 `limit` 个字符。

    超长时**优先保留 `prefix` 与 `suffix`**（它们通常是有语义的标记，
    例如"【草稿】"、"销售合同-"），只截中间的用户可读部分。
    """
    body = text or ""
    keep = limit - len(prefix) - len(suffix)
    if keep <= 0:
        # 极端情况：前缀本身就把预算吃光了 —— 连前缀一起截，保证不越界
        return (prefix + body + suffix)[:limit]
    if len(body) <= keep:
        return prefix + body + suffix
    if keep == 1:
        return prefix + ELLIPSIS + suffix
    return prefix + body[: keep - 1] + ELLIPSIS + suffix
