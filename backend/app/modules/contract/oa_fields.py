"""按合同模板真实定义组装主体信息，不把控件 ID 前缀当成控件类型。"""
import json

from app.core.errors import AppError, ErrorCode


def prepare_contract_fields(schema, field_map, *, our_company, customer_name):
    definitions = []

    def walk(value):
        if isinstance(value, str):
            try:
                walk(json.loads(value))
            except (ValueError, TypeError):
                pass
        elif isinstance(value, list):
            for child in value:
                walk(child)
        elif isinstance(value, dict):
            props = value.get('props') or {}
            if props.get('id') and props.get('label') and value.get('componentName'):
                definitions.append({'id': props['id'], 'label': props['label'],
                                    'type': value['componentName'], 'bizAlias': props.get('bizAlias')})
            for key, child in value.items():
                if key != 'props':
                    walk(child)

    walk(schema)
    output = dict(field_map)
    for alias, label, child_label, child_alias, name in (
        ('contract_mine_info', '我方信息', '我方主体名称', 'contract_mine_company_name', our_company),
        ('contract_opposite_info', '对方信息', '对方主体名称', 'contract_opposite_company_name', customer_name),
    ):
        table = next((d for d in definitions if d['bizAlias'] == alias and d['type'] == 'TableField'), None)
        if table is None:
            continue
        if not str(name or '').strip():
            raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, f'请先配置{label}的主体名称，再提交合同审批', 422)
        # 采用已成功实例的创建格式；详情接口的 rowValue 格式不能回填到创建接口。
        output[table['id']] = json.dumps([[{'name': child_label, 'value': str(name).strip(),
                                          'bizAlias': child_alias}]], ensure_ascii=False)
    # 智能合同内部 UUID 和状态由钉钉生成，CRM 不把自己的编号/状态写入系统字段。
    for definition in definitions:
        if definition['bizAlias'] in ('contract_biz_id', 'contract_operation_status', 'contract_performance_status'):
            output.pop(definition['id'], None)
    return output, definitions
