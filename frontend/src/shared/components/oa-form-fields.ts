/** 读取已同步的钉钉表单，包括生产部后续填写的回复；不渲染外部 HTML。 */
const object = (value: unknown): Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}

function text(value: unknown, depth = 0): string {
  if (value === null || value === undefined || value === '') return ''
  if (depth > 10) return '复杂内容请在钉钉查看'
  if (typeof value === 'string') {
    const trimmed = value.trim()
    if (trimmed.startsWith('[') || trimmed.startsWith('{')) {
      try { return text(JSON.parse(trimmed), depth + 1) } catch { /* 普通文本原样显示 */ }
    }
    return value
  }
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (typeof value === 'number') return String(value)
  if (Array.isArray(value)) return value.map(item => text(item, depth + 1)).filter(Boolean).join('\n')
  const row = object(value)
  if (typeof row.fileName === 'string') return row.fileName
  if ('value' in row) {
    const content = text(row.value, depth + 1)
    const label = typeof row.label === 'string' ? row.label : typeof row.name === 'string' ? row.name : ''
    return label && content ? `${label}：${content}` : content
  }
  if (typeof row.name === 'string') return row.name
  if (typeof row.text === 'string') return row.text
  // 表格明细采用 rowValue；未知复杂结构不把内部字段名和编号直接印到业务页面。
  if ('rowValue' in row) return text(row.rowValue, depth + 1)
  return '复杂内容请在钉钉查看'
}

export function oaFormFields(result: unknown): Array<{ name: string; value: string }> {
  const payload = object(object(result).payload)
  const fields = payload.formComponentValues ?? payload.formValueVOS
  if (!Array.isArray(fields)) return []
  return fields.map(field => {
    const row = object(field)
    return { name: typeof row.name === 'string' ? row.name : '钉钉表单字段', value: text(row.value) }
  }).filter(field => field.value !== '')
}
