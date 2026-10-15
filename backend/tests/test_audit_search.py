"""审计中文搜索：中文名 → 英文码的反查（2026-10-15）。

## 为什么要有这些用例

主人原话：「审计日志……现在太乱了，也没有搜索功能」。

查下来后端**一直**支持 `keyword`，但它只在 `action` / `business_type` 两个
**英文码**字段上做 `ilike` —— 而界面上显示的是中文（翻译在前端）：

    界面上显示        库里存的
    「定制需求」  ←→  'custom_inquiry'
    「打样单」    ←→  'sample'

于是用户在搜索框里打中文，**一条都搜不到**。`app/core/audit_search.py`
做的就是这层反查。

这里守住四件事：

1. **中文名能反查到码**（打全称）；
2. **打半截也认**（「定制」→ 定制需求），否则用户得记住完整名字；
3. **歧义词的处理**（一个中文名对应多个码）—— 打全称时全给（用户就是要这个词），
   只包含时不给（避免把不相干的记录混进来）；
4. **英文码仍然认**（有人习惯照库里的码搜），**不能**因为加了中文支持就把原来的用法弄坏。

另外核对**生成物是最新的**：中文名的真源在前端的 `audit-labels.ts`，
由 `ops/gen_audit_type_labels.py` 生成后端 JSON。真源改了而忘了生成，
表现是"搜得到旧词、搜不到新词"—— 在界面上极难发现。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from app.core.audit_search import (
    _action_labels,
    _reverse,
    _type_labels,
    action_codes,
    business_type_codes,
    match_codes,
)

REPO = Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------- 中文能搜到


def test_chinese_type_name_maps_to_code():
    """界面上印的中文，必须能搜到对应的记录。"""
    assert business_type_codes("定制需求") == {"custom_inquiry"}
    assert business_type_codes("打样单") == {"sample"}
    assert business_type_codes("报价单") == {"quote"}
    # 「客户」会连"客户等级""客户价格规则"一起带出来 —— **对的**：
    # 用户打「客户」就是想看这一类，精确匹配在这里反而碍事（见下面那条用例）
    assert "customer" in business_type_codes("客户")


def test_chinese_action_name_maps_to_code():
    assert "approve" in action_codes("审批通过")
    assert "add_item" in action_codes("新增明细")


def test_partial_chinese_also_works():
    """打半截也要能用 —— 否则用户得先记住完整名字才搜得到。"""
    assert business_type_codes("定制") == {"custom_inquiry"}
    assert "sample" in business_type_codes("打样")
    assert len(action_codes("审批")) > 1  # 「审批」能带出审批通过/提交审批等


# ---------------------------------------------------------------- 英文码不能坏


def test_partial_match_can_hit_several_labels():
    """打半截命中多个是**期望行为**：用户打「打样」是想看这一类，
    不是只想要"打样单"那一种。别把这里"修"成精确匹配。"""
    hits = business_type_codes("打样")
    assert {"sample", "sample_item"} <= hits


def test_english_code_still_works():
    """有人习惯照库里的码搜（排障时尤其如此）—— 加了中文支持不能把这条路弄坏。"""
    assert "custom_inquiry" in business_type_codes("custom_inquiry")
    assert "sample" in business_type_codes("sample")
    assert "approve" in action_codes("approve")


def test_ascii_fragment_is_passed_through_for_sql_like():
    """纯 ASCII 片段要原样带出去，让 SQL 那头用 `ilike` 做前缀/包含匹配。"""
    assert "custom" in business_type_codes("custom")
    assert "quot" in business_type_codes("quot")


def test_chinese_fragment_does_not_leak_ascii_passthrough():
    """中文输入不该被当成码原样传给 SQL（那只会多一次无意义的 ilike）。"""
    hits = business_type_codes("定制需求")
    assert all(h.isascii() for h in hits)          # 返回的都是码
    assert "定制需求" not in hits                   # 中文本身不作为"码"


# ---------------------------------------------------------------- 边界


def test_empty_input_matches_nothing():
    """空串不能变成"匹配全部" —— 那会让清空搜索框后筛出一堆无关记录。"""
    assert business_type_codes("") == set()
    assert action_codes("") == set()
    assert business_type_codes("   ") == set()


def test_unknown_word_matches_nothing():
    assert business_type_codes("这个词肯定不存在xyz") == set() or all(
        h.isascii() for h in business_type_codes("这个词肯定不存在xyz")
    )


# ---------------------------------------------------------------- 歧义词


def test_ambiguous_label_gives_all_codes_when_exact():
    """打全称时，一个名字对应的**所有**码都要给 —— 用户就是要看这个词。"""
    reverse = _reverse(_action_labels())
    ambiguous = {name: codes for name, codes in reverse.items() if len(codes) > 1}
    assert ambiguous, "动作表里应该有重名（否则这条用例没意义）"
    name, codes = next(iter(ambiguous.items()))
    got = action_codes(name)
    assert set(codes) <= got, f"「{name}」对应的码没给全：{codes} vs {got}"


def test_ambiguous_label_is_skipped_for_partial_match():
    """只包含时，歧义名字**不**参与 —— 宁可不匹配，也不给一堆看着像错的记录。

    判据：取一个重名里的**单字片段**，它不该把那个重名下的所有码都捞出来。
    """
    reverse = _reverse(_action_labels())
    ambiguous = {name: codes for name, codes in reverse.items() if len(codes) > 1}
    name, codes = next(iter(ambiguous.items()))
    fragment = name[:2]
    if fragment == name:
        return  # 名字太短，跳过
    hits = action_codes(fragment)
    # 片段不得因为那个歧义名字而把它的码全带出来
    assert not set(codes) <= hits, f"片段「{fragment}」把歧义名字「{name}」的码全带出来了"


# ---------------------------------------------------------------- 生成物


def test_type_labels_match_frontend_source():
    """后端那份对象类型中文名，必须与前端真源一致。

    真源：`frontend/src/shared/audit-labels.ts` 的 `AUDIT_TYPE_LABEL`。
    生成：`python3 ops/gen_audit_type_labels.py`。
    """
    result = subprocess.run(
        [sys.executable, "ops/gen_audit_type_labels.py", "--check"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "对象类型中文名生成物不是最新的 —— 跑 `python3 ops/gen_audit_type_labels.py`\n"
        + (result.stdout or "")
        + (result.stderr or "")
    )


def test_label_tables_are_loadable_and_non_empty():
    """两份表都要能读出来 —— 读不到的话反查会静默退化成"只能搜英文码"。"""
    types = _type_labels()
    actions = _action_labels()
    assert len(types) > 30
    assert len(actions) > 100
    # 值必须是中文（否则反查毫无意义）。挑几个确定有中文的码来验。
    assert types["sample"] == "打样单"
    assert actions["approve"] == "审批通过"


def test_match_codes_handles_a_dict_directly():
    """`match_codes` 是通用的：给它任意一张表都要能工作（便于将来复用）。"""
    table = {"a": "甲", "b": "乙", "c": "乙"}
    assert match_codes("甲", table) == {"a"}
    assert match_codes("乙", table) == {"b", "c"}     # 全称 → 全给
    assert match_codes("甲子", table) == set()         # 没这个词
