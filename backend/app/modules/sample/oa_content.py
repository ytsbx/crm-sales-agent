"""打样提交正文：保留单头和每条明细，不用合计数代替逐行车间依据。"""

HEADER_FIELDS = (
    ('产品名称', 'product_name'), ('打样类型', 'sample_type'),
    ('产品大类', 'product_category'), ('制作单位', 'maker_unit'),
    ('要求完成日期', 'target_completion_date'), ('打样费用', 'sample_fee'),
    ('打样目的', 'sample_purpose'), ('紧急程度', 'urgency'),
    ('样品情况', 'sample_situation'), ('主要材质和规格', 'material_spec'),
    ('产品尺寸', 'product_size'), ('产品印刷', 'printing'),
    ('主要辅料', 'accessories'), ('包装要求', 'packaging'),
    ('做法要求', 'craft_requirement'), ('备注', 'remark'),
)
ITEM_FIELDS = (
    ('名称', 'item_name'), ('SKU编号', 'sku_id'),
    ('来源询价编号', 'inquiry_no_snapshot'), ('规格', 'specification'),
    ('数量', 'quantity'), ('材质', 'material'), ('工艺', 'craft'),
    ('图纸版本', 'drawing_version'), ('备注', 'remark'),
)


def submission_summary(sample, items, *, note='', applicant_name=''):
    lines = [f'打样申请：{getattr(sample, "id", None) or "待编号"}']
    if applicant_name:
        lines.append(f'申请人：{applicant_name}')
    if note:
        lines.append(f'打样需求说明：{note}')
    for label, key in HEADER_FIELDS:
        value = getattr(sample, key, None)
        if value is not None and value != '':
            lines.append(f'{label}：{value}')
    lines.append(f'打样明细（共{len(items)}条）：')
    for index, item in enumerate(items, 1):
        lines.append(f'第{index}条')
        for label, key in ITEM_FIELDS:
            value = getattr(item, key, None)
            if value is not None and value != '':
                lines.append(f'  {label}：{value}')
    return '\n'.join(lines)


def dingtalk_requirement_note(note='', *, attachment_name=''):
    """钉钉正文只保留说明，产品资料和逐条明细统一看 Excel 附件。

    钉钉模板已经有单独的原生字段（类型、产品大类、制作单位、完成日期、积分），
    申请表 Excel 也承载产品名称、规格、材质、包装、做法和逐条明细；把完整快照再
    塞进「打样需求说明」会导致审批详情重复且难以阅读。
    """
    lines = []
    text = str(note or '').strip()
    if text:
        lines.append(f'打样需求说明：{text}')
    filename = attachment_name or '打样申请单，打样物料需求单，物料信息单模板，下载后使用.xlsx'
    lines.append(f'完整产品资料、样品明细和制作要求见附件《{filename}》。')
    return '\n'.join(lines)
