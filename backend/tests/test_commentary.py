"""AI 叙述的事实压缩（`commentary._compact` / `_facts_text`）。

守的是这条规矩：**"只给界面看的提示"不许进提示词。**

踩过的坑（2026-10-05）：报价草稿的 `note` 里写死了
「确认后用 POST /quotes 生成正式报价单」，这个字段和"事实"（金额、明细、风险）
混在同一个返回里，被整体喂给了模型。模型很守规矩地"只用结果里出现的东西"，
于是 AI 叙述的末尾就跟着冒出一句接口路径，给一线业务员看。

修法不是把模型教聪明，而是**从一开始就别给它看**（见 `_HIDDEN_KEYS`）。
这组测试守的就是那道闸。
"""

from app.modules.agent.commentary import SYSTEM_PROMPT, _compact, _facts_text

#: 原来那句把整件事引爆的文案（接口路径在末尾）。
LEAKY_NOTE = "这只是草稿建议，未落库；确认后用 POST /quotes 生成正式报价单。"


def test_note_is_not_sent_to_model():
    packed = _compact({"item_count": 0, "suggested_total": 0, "note": LEAKY_NOTE})
    assert "note" not in packed
    # 事实一个都不能少：只剔 note，别顺手把业务字段也吞了
    assert packed["item_count"] == 0
    assert packed["suggested_total"] == 0


def test_note_is_stripped_in_nested_dict():
    packed = _compact({"summary": {"level": "normal", "note": "内部提示"}})
    assert "note" not in packed["summary"]
    assert packed["summary"]["level"] == "normal"


def test_note_is_stripped_inside_list():
    packed = _compact({"items": [{"sku": "A", "note": "内部提示"}]})
    assert "note" not in packed["items"][0]
    assert packed["items"][0]["sku"] == "A"


def test_private_keys_are_still_stripped():
    """下划线开头是内部键（比如前端自己塞的行序号），这条是既有行为，别被改坏。"""
    packed = _compact({"_idx": 0, "keep": 1})
    assert "_idx" not in packed
    assert packed["keep"] == 1


def test_facts_text_carries_no_api_path():
    text = _facts_text({"note": LEAKY_NOTE, "count": 1})
    assert "note" not in text
    assert "/quotes" not in text


def test_prompt_forbids_api_paths_and_code():
    """提示词里必须留着"不许出现接口地址 / 字段名 / 代码"这条纪律 —— 防有人顺手删掉。"""
    assert "接口地址" in SYSTEM_PROMPT
    assert "字段名" in SYSTEM_PROMPT
