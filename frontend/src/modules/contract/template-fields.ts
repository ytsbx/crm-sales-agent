/** 页面使用中文字段，保存时转换为后端既有模板语法。 */
export const TEMPLATE_FIELDS: Record<string, string> = {
  'customer.name': '客户名称',
  'customer.contact_name': '客户联系人',
  'customer.phone': '客户电话',
  'customer.address': '客户地址',
  'order.order_no': '订单编号',
  'order.total_amount': '订单金额',
  'quote.quote_no': '报价编号',
  'quote.total_amount': '报价金额',
  'quote.currency': '报价币种',
  'quote.payment_terms': '付款条款',
  'quote.trade_terms': '贸易条款',
  'quote.delivery_terms': '交货条款',
  today: '当天日期',
}

export function templateFieldLabel(token: string): string {
  const key = token.trim()
  if (key.startsWith('extra.')) return `待填写：${key.slice(6).trim()}`
  return TEMPLATE_FIELDS[key] ?? '未识别的模板字段'
}

export function displayTemplate(body: string): string {
  return body.replace(/\{\{([^{}]+)\}\}/g, (_, token: string) => `【${templateFieldLabel(token)}】`)
}

export function encodeTemplate(body: string): string {
  const keys = Object.entries(TEMPLATE_FIELDS)
  return body.replace(/【([^【】]+)】/g, (original, label: string) => {
    if (label.startsWith('待填写：') && label.slice(4).trim()) {
      return `{{extra.${label.slice(4).trim()}}}`
    }
    const field = keys.find(([, name]) => name === label)
    return field ? `{{${field[0]}}}` : original
  })
}
