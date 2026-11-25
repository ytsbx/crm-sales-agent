import json

import pytest

from app.core.errors import AppError
from app.modules.contract.oa_fields import prepare_contract_fields
from app.modules.dingtalk.service import build_form_component_values


def schema():
    return {'schemaContent': json.dumps({'items': [{'componentName': 'DDBizSuite', 'children': [
        {'componentName': kind, 'props': {'id': identifier, 'label': label, 'bizAlias': alias}}
        for kind, identifier, label, alias in [
            ('DDSelectField', 'SelectField_type', '发起方式', 'start_type'),
            ('DDAttachment', 'AttachmentField_file', '合同正文', 'contract_files'),
            ('TableField', 'TableField-mine', '我方信息', 'contract_mine_info'),
            ('TableField', 'TableField-other', '对方信息', 'contract_opposite_info'),
            ('TextField', 'TextField_uuid', '合同uuid', 'contract_biz_id'),
        ]
    ]}]}, ensure_ascii=False)}


def test_contract_subjects_and_real_component_types():
    fields, definitions = prepare_contract_fields(schema(), {
        'SelectField_type': '上传文件发起', 'AttachmentField_file': '[{"fileId":"original"}]',
        'TextField_uuid': 'CRM-123',
    }, our_company='我方企业', customer_name='客户企业')
    assert json.loads(fields['TableField-mine'])[0][0]['value'] == '我方企业'
    assert json.loads(fields['TableField-other'])[0][0]['value'] == '客户企业'
    assert 'TextField_uuid' not in fields
    values = {v['id']: v for v in build_form_component_values(fields, field_definitions=definitions)}
    assert values['SelectField_type']['name'] == '发起方式'
    assert values['SelectField_type']['componentType'] == 'DDSelectField'
    assert values['AttachmentField_file']['componentType'] == 'DDAttachment'
    assert values['TableField-mine']['componentType'] == 'TableField'


def test_missing_signing_party_is_rejected_without_fabrication():
    with pytest.raises(AppError, match='主体名称'):
        prepare_contract_fields(schema(), {}, our_company='', customer_name='客户企业')
