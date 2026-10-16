"""提交钉钉询价前的必填项检查（后端侧）。

守 2026-10-16 补的一条：「材质及工艺要求」「预估订购数量」原先**只在前端拦**
（`KnowledgePage` 保存前校验），后端接口接受它们为空并照发给钉钉 —— 生产部会
收到一张没有需求说明、没有数量的询价单，而那正是他们要据以报价的两项。
文档口径是「提交前检查审批必填项」，这个检查不能只长在界面上。

⚠️ 判据只用于**提交**，不用于保存草稿：草稿允许暂缺是同一份文档的明确要求
（`test_inquiry_draft.py` 守的是那一侧）。
"""
from types import SimpleNamespace as Obj

from app.modules.dingtalk.router import SUBMIT_REQUIRED_FIELDS, missing_submit_fields


def _inquiry(**over):
    row = Obj(description="铜版纸 300g，四色印刷", quantity=1000)
    for key, value in over.items():
        setattr(row, key, value)
    return row


def test_complete_inquiry_passes():
    assert missing_submit_fields(_inquiry()) == []


def test_no_description_is_reported():
    assert missing_submit_fields(_inquiry(description=None)) == ["材质及工艺要求"]


def test_no_quantity_is_reported():
    assert missing_submit_fields(_inquiry(quantity=None)) == ["预估订购数量"]


def test_blank_and_whitespace_count_as_missing():
    """前端清空一个字段存下来的就是空串/空白，不能算"填了"。"""
    assert missing_submit_fields(_inquiry(description="")) == ["材质及工艺要求"]
    assert missing_submit_fields(_inquiry(description="   ")) == ["材质及工艺要求"]


def test_both_missing_are_listed_together():
    """两项都缺时一次说清，别让用户改一次报一次。"""
    assert missing_submit_fields(_inquiry(description=None, quantity=None)) == [
        "材质及工艺要求",
        "预估订购数量",
    ]


def test_numeric_zero_quantity_is_not_treated_as_missing():
    """0 是"填了"，由 schema 的 gt=0 去拦；这里只判"有没有填"。"""
    assert missing_submit_fields(_inquiry(quantity=0)) == []


def test_labels_match_the_field_names_the_ui_uses():
    """报错里用的中文名要和界面上那块输入框的名字一致，否则用户找不到改哪里。"""
    assert [label for label, _ in SUBMIT_REQUIRED_FIELDS] == ["材质及工艺要求", "预估订购数量"]
    assert [attr for _, attr in SUBMIT_REQUIRED_FIELDS] == ["description", "quantity"]
