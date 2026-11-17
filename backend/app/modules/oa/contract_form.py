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
          'contract_biz_id', 'contract_operation_status', 'contract_performance_status'}


async def active_template(user):
    identity, code = service._template_config()
    try:
        templates = await client.list_templates(user.dingtalk_userid)
    except (client.OaError, client.OaNotConfigured) as exc:
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
    filled = doc.filled_data or {}
    amount = await service._contract_amount(session, doc)
    defaults = {f['id']: f['default'] for f in template['fields'] if 'default' in f}
    defaults.update({
        'applicant_name': user.name, 'department': await _department_name(session, user),
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
    defaults.update({k:v for k,v in (filled.get('_oa_fields') or {}).items() if k not in LOCKED})
    return {'fields': [{**f, 'locked': f['id'] in LOCKED} for f in template['fields']],
            'defaults': defaults, 'people': template.get('people', [])}


async def apply_extra_fields(session, user, doc, payload, extras):
    template = await active_template(user)
    definitions = {f['id']: f for f in template['fields']}
    invalid = set(extras) - (set(definitions) - LOCKED)
    if invalid:
        raise AppError(ErrorCode.PARAM_ERROR, '不可修改的合同字段：' + '、'.join(sorted(invalid)), 422)
    for key, value in extras.items():
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
                saved = await client.upload_file(user.dingtalk_userid, row.file_name, path.read_bytes(), row.mime_type)
                attachments.append(str(saved['id']))
            payload['attachment_ids'].extend(attachments)
            value = attachments
        payload['fields'][key] = value
