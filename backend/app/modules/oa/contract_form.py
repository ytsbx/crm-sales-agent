"""Use the active OA contract definition for CRM submission fields."""
from sqlalchemy import select
from app.core.errors import AppError, ErrorCode
from app.modules.oa import client, service
from app.modules.file.model import BusinessFile, FileRecord
from app.modules.file.storage import absolute_path

# Facts fixed when uploading the contract or derived from its formal business basis.
LOCKED = {'applicant_name', 'department', 'contract_name', 'contract_files', 'start_type',
          'contract_number', 'contract_number_rule_type', 'contract_number_rule_fix',
          'contract_amount', 'contract_amount_type', 'contract_mine_info', 'contract_opposite_info',
          'contract_biz_id', 'contract_operation_status', 'contract_performance_status', 'generated_number'}


SOURCE_CODE = 'form-dccc0c7614584d1692414c167221b0cd'
PARTIES = {
    'our_parties': ('contract_mine_info', 'mine_company_name'),
    'other_parties': ('contract_opposite_info', 'opposite_company_name'),
}
SOURCE_FIELDS = {
    'contract_classify_cascade': 'file_category', 'contract_number_rule_type': 'number_method',
    'contract_number': 'manual_number', 'contract_number_rule_fix': 'number_rule',
    'contract_amount_type': 'amount_type', 'contract_term_type': 'term_type',
    'contract_period_start_date': 'start_date', 'contract_period_end_date': 'end_date',
    'contract_archive': 'archiver', 'contract_description': 'contract_note',
    'contract_attachments': 'additional_files', 'contract_mine_info': 'our_parties',
    'contract_opposite_info': 'other_parties', 'contract_seal_type': 'seal_types',
    'contract_sign_mode': 'sign_method', 'contract_sign_date': 'signed_on',
}


def locked_fields(template):
    return ({SOURCE_FIELDS.get(key, key) for key in LOCKED}
            if template.get('code') == SOURCE_CODE else LOCKED)


def source_defaults(template, defaults):
    if template.get('code') != SOURCE_CODE:
        return defaults
    mapped = {SOURCE_FIELDS.get(key, key): value for key, value in defaults.items()}
    if mapped.get('file_category') in {'销售合同', '采购合同'}:
        mapped['file_category'] = '合同 / ' + mapped['file_category']
    for key, old in [('our_parties', 'mine_company_name'), ('other_parties', 'opposite_company_name')]:
        mapped[key] = [{'name': row.get(old, ''), 'contact': row.get('contact', '')}
                       for row in defaults.get('contract_mine_info' if key == 'our_parties' else 'contract_opposite_info', [])]
    return {field['id']: mapped[field['id']] for field in template['fields']
            if field['id'] in mapped
            and (field['id'] != 'file_category' or mapped['file_category'] in field.get('options', []))}


async def active_template(user):
    identity, code = service._template_config()
    applicant = client.applicant_identity(user)
    if not applicant:
        raise AppError(ErrorCode.STATUS_NOT_ALLOWED, "请联系管理员为当前账号绑定同序人员", 422)
    try:
        templates = await client.list_templates(applicant)
    except (client.OaError, client.OaNotConfigured, client.OaUnknownOutcome) as exc:
        raise AppError(ErrorCode.EXTERNAL_ERROR, '同序合同表单暂时无法读取，请稍后重试', 502) from exc
    matches = [t for t in templates if (t['id'] == identity if identity else t['code'] == code)]
    if not matches:
        raise AppError(ErrorCode.NOT_FOUND, '同序合同模板不存在或无发起权限', 422)
    return max(matches, key=lambda t: t['version'])


async def submission_form(session, actor, doc):
    from app.modules.user.model import User
    from app.modules.oa.inquiry import _department_name
    user = await session.get(User, actor.id)
    template = await active_template(user)
    # 归属部门以同序为准：同序里每位申请人都有完整部门，CRM 的部门表可能没有对应项。
    identity = template.get('applicant') or {}
    department = identity.get('department_name') or await _department_name(session, user)
    filled = doc.filled_data or {}
    amount = await service._contract_amount(session, doc)
    defaults = {f['id']: f['default'] for f in template['fields'] if 'default' in f}
    defaults.update({
        'applicant_name': identity.get('name') or user.name, 'department': department,
        'contract_name': doc.title, 'contract_files': '已上传的合同正文原文件',
        'start_type': '上传文件发起',
        'contract_number': filled.get('_contract_number') or doc.doc_no,
        'contract_number_rule_type': '选择已有合同编号规则' if filled.get('_contract_number_rule_type') == 'rule' else '手动填写',
        'contract_number_rule_fix': filled.get('_contract_number_rule_fix') or '系统默认规则',
        'contract_amount_type': '无金额' if amount is None else '收入',
        'contract_amount': str(amount or 0),
        'contract_term_type': '固定期限' if doc.expiry_date else '无固定期限',
        'contract_period_start_date': str(doc.effective_date or ''),
        'contract_period_end_date': str(doc.expiry_date or ''),
        'contract_mine_info': [{'mine_company_name': filled.get('_our_company') or ''}],
        'contract_opposite_info': [{'opposite_company_name': filled.get('_counterparty_name') or ''}],
        'contract_classify_cascade': '月结协议' if doc.doc_type == 'monthly' else '销售合同',
        'contract_sign_mode': '纸质签署',
        'contract_biz_id': '审批生成后自动带入', 'contract_operation_status': '待审批',
        'contract_performance_status': '尚未履约',
    })
    locked = locked_fields(template)
    defaults = source_defaults(template, defaults)
    saved_fields = filled.get('_oa_fields') or {}
    defaults.update({k: v for k, v in saved_fields.items() if k not in locked})
    if template.get('code') == SOURCE_CODE:
        for key in PARTIES:
            saved_parties = saved_fields.get(key) or []
            for index, party in enumerate(defaults.get(key, [])):
                if index < len(saved_parties) and isinstance(saved_parties[index], dict):
                    party['contact'] = saved_parties[index].get('contact') or ''
    return {'fields': [{**f, 'locked': f['id'] in locked,
                        'editable_contact': template.get('code') == SOURCE_CODE and f['id'] in PARTIES} for f in template['fields']],
            'defaults': defaults, 'people': template.get('people', [])}


async def apply_extra_fields(session, user, doc, payload, extras, *, uploads=None, template=None):
    template = template if template is not None else await active_template(user)
    definitions = {f['id']: f for f in template['fields']}
    editable_parties = set(PARTIES) & set(definitions) if template.get('code') == SOURCE_CODE else set()
    invalid = set(extras) - ((set(definitions) - locked_fields(template)) | editable_parties)
    if invalid:
        raise AppError(ErrorCode.PARAM_ERROR, '不可修改的合同字段：' + '、'.join(sorted(invalid)), 422)
    for key, value in extras.items():
        if key in editable_parties:
            source_key, name_key = PARTIES[key]
            parties = payload['fields'][source_key]
            if (not isinstance(value, list) or len(value) != len(parties)
                    or any(not isinstance(item, dict) or not isinstance(item.get('contact'), str) for item in value)):
                raise AppError(ErrorCode.PARAM_ERROR, '合同联系人格式无效', 422)
            payload['fields'][key] = [{'name': party[name_key], 'contact': entry['contact'].strip()}
                                      for party, entry in zip(parties, value)]
            continue
        if definitions[key]['type'] == 'attachment':
            if not isinstance(value, list) or any(type(v) is not int for v in value) or len(value) > 30:
                raise AppError(ErrorCode.PARAM_ERROR, '附件选择无效', 422)
            ids = list(dict.fromkeys(value))
            rows = (await session.execute(select(FileRecord).join(BusinessFile, BusinessFile.file_id == FileRecord.id).where(
                BusinessFile.business_type == 'contract', BusinessFile.business_id == doc.id, FileRecord.id.in_(ids)))).scalars().unique().all()
            if {row.id for row in rows} != set(ids):
                raise AppError(ErrorCode.PARAM_ERROR, '所选附件不属于当前合同', 422)
            attachments = []
            for row in rows:
                path = absolute_path(row.object_key)
                if not path.is_file() or path.stat().st_size != row.size:
                    raise AppError(ErrorCode.PARAM_ERROR, '合同附件缺失或不完整，请重新上传', 422)
                if uploads is None:
                    saved = await client.upload_file(client.applicant_identity(user), row.file_name, path.read_bytes(), row.mime_type)
                else:
                    saved = await uploads.stage(client.applicant_identity(user), row.file_name, path, row.mime_type)
                attachments.append(str(saved['id']))
            payload['attachment_ids'].extend(attachments)
            value = attachments
        payload['fields'][key] = value
