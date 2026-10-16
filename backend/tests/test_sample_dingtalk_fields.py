"""打样钉钉模板取值与逐字段校验（`collect_template_values`）。

守 2026-10-16 修的一条：模板里**选填**的日期/积分格留空时，原先直接对空串做
`date.fromisoformat('')` / `Decimal('')` —— 两者都抛，用户什么都没填却被告知
"要求完成日期格式无效""打样积分必须是0到99990之间的有限数字"。
`props.required=False` 是钉钉模板如实给回来的，所以"选填留空"必须走得通。

⚠️ 这里测的是**从 2026-10-16 起**的抽取函数；`submit` 已改为调用它，
行为与原先内联循环一致（必填、选项、日期、数字范围四类校验一条没少）。
"""
import pytest

from app.core.errors import AppError, ErrorCode
from app.modules.sample.dingtalk import collect_template_values

SYSTEM_LABELS = {'打样申请编号', '申请人员', '归属部门', '样品明细明细表'}


def _field(idx, label, kind, *, options=(), required=False, system=False):
    """与 `template()` 真实产出的形状一致（dict，不是对象）。"""
    return {
        'id': f'id{idx}', 'label': label, 'type': kind,
        'options': list(options), 'required': required, 'system': system,
    }


def _template(*, date_required=False, points_required=False, type_required=True):
    """一份和真实模板同形的字段清单（标签与 `template()` 的 INPUT_LABELS 一致）。"""
    return [
        _field(0, '打样类型', 'DDSelectField', options=['销售客户打样'], required=type_required),
        _field(1, '打样产品大类', 'DDSelectField', options=['高频类产品']),
        _field(2, '样品制作单位', 'DDSelectField', options=['义乌打样部']),
        _field(3, '要求完成日期', 'DDDateField', required=date_required),
        _field(4, '本次打样积分', 'NumberField', required=points_required),
        _field(5, '打样需求说明', 'TextareaField'),
        _field(6, '申请人员', 'InnerContactField', system=True),
        _field(7, '归属部门', 'DepartmentField', system=True),
        _field(8, '样品明细明细表', 'DDAttachment', system=True),
    ]


def test_optional_date_and_points_left_blank_do_not_raise():
    """回归：选填的日期/积分留空是"不填"，不是格式错。"""
    values, by_label = collect_template_values(
        _template(), {'id0': '销售客户打样'}
    )

    assert by_label['要求完成日期'] == ''
    assert by_label['本次打样积分'] == ''
    assert values['id3'] == '' and values['id4'] == ''


def test_blank_optional_fields_still_go_to_the_form_as_empty():
    """空值也要进 field_map（钉钉那边就是空着），不能因为空就漏键。"""
    values, _ = collect_template_values(_template(), {'id0': '销售客户打样'})

    assert 'id3' in values and 'id4' in values


def test_missing_required_select_still_reports_which_field():
    with pytest.raises(AppError) as caught:
        collect_template_values(_template(), {})

    assert '打样类型' in caught.value.message
    assert caught.value.code == ErrorCode.REQUIRED_FIELD_MISSING


def test_required_date_blank_is_still_a_required_error_not_a_format_error():
    """必填的日期留空要说"请填写"，不能说"格式无效"。"""
    with pytest.raises(AppError) as caught:
        collect_template_values(
            _template(date_required=True), {'id0': '销售客户打样'}
        )

    assert '请填写要求完成日期' in caught.value.message


def test_bad_date_format_is_rejected():
    with pytest.raises(AppError) as caught:
        collect_template_values(
            _template(), {'id0': '销售客户打样', 'id3': '2026/10/20'}
        )

    assert '格式无效' in caught.value.message


def test_points_out_of_range_is_rejected():
    for bad in ('-1', '99991'):
        with pytest.raises(AppError) as caught:
            collect_template_values(
                _template(), {'id0': '销售客户打样', 'id4': bad}
            )
        assert '打样积分' in caught.value.message


def test_points_boundaries_are_accepted():
    for good in ('0', '99990'):
        _, by_label = collect_template_values(
            _template(), {'id0': '销售客户打样', 'id4': good}
        )
        assert by_label['本次打样积分'] == good


def test_invalid_select_option_is_rejected():
    with pytest.raises(AppError) as caught:
        collect_template_values(_template(), {'id0': '不存在的选项'})

    assert '选项无效' in caught.value.message


def test_system_fields_are_never_read_from_the_client():
    """申请人员/归属部门/样品明细表由服务端填，客户端传什么都不采信。"""
    values, by_label = collect_template_values(
        _template(), {'id0': '销售客户打样', 'id6': '伪造的人', 'id7': '伪造的部门'}
    )

    assert 'id6' not in values and 'id7' not in values
    assert 'id8' not in values
    assert '申请人员' not in by_label


def test_newlines_and_spaces_are_stripped_like_before():
    """与原先的内联循环一致：值先 strip 再校验、再落库。"""
    values, _ = collect_template_values(
        _template(), {'id0': ' 销售客户打样 '}
    )

    assert values['id0'] == '销售客户打样'
