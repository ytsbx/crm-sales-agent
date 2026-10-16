"""钉钉 `formComponentValues` 的组装规则。

这里钉的是**类型与空值**这两条 —— 它们的共同后果都很隐蔽：
传错了钉钉不会说"类型不对"，而是回一句 `Missingvalue / value is mandatory
for this action.`，**不说是哪个控件**，排查极难。
"""
from app.modules.dingtalk.service import build_form_component_values


def test_values_are_always_strings():
    """`value` 必须是**字符串** —— 传数字会让钉钉把这格判成"空"。

    主人 2026-10-16：「模板肯定没错，你再试试」。实测（同模板、同字段，只差类型）：

        归属部门 value = 1115706176（数字）  → Missingvalue（说"必填项没值"）
        归属部门 value = "1115706176"（字符串）→ 表单校验通过

    来源就在我们这边：`get_user_dept_ids()` 返回的是**整数数组**，
    直接塞进 values 就是数字。
    """
    items = build_form_component_values({"DepartmentField_X": 1115706176, "NumberField_Y": 10})
    assert [item["value"] for item in items] == ["1115706176", "10"]
    assert all(isinstance(item["value"], str) for item in items)


def test_blank_values_are_skipped():
    """空值不提交：钉钉会把它当成"清空"，驳回后重提会把上次填的抹掉。"""
    items = build_form_component_values(
        {"A": "", "B": None, "C": [], "D": {}, "E": "真值"}
    )
    assert [item["id"] for item in items] == ["E"]


def test_bool_is_not_rendered_as_text():
    """布尔不是钉钉的表单值类型，别渲染成 "True" 混进去。"""
    items = build_form_component_values({"A": True, "B": 0, "C": "x"})
    # 0 是合法数字 → "0"；True 不是表单值 → 跳过
    assert [item["id"] for item in items] == ["B", "C"]
    assert [item["value"] for item in items] == ["0", "x"]


def test_component_type_is_derived_from_id_prefix():
    """`componentType` 从 id 前缀推导（服务端 69 个控件实测与前缀一致）。"""
    items = build_form_component_values({"DDSelectField_ABC": "x", "没有下划线": "y"})
    assert items[0]["componentType"] == "DDSelectField"
    assert "componentType" not in items[1]
