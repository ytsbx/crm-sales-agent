/** 企微来源的标准名。导入链路写「企业微信」，旧手工入口写过「企微添加」。 */
export const WECOM_SOURCE = '企业微信'

const WECOM_SOURCE_ALIASES = new Set([WECOM_SOURCE, '企微添加'])

/** 是否为企微来源；导入和手工添加都算同一个渠道。 */
export function isWecomCustomerSource(source?: string | null): boolean {
  return Boolean(source && WECOM_SOURCE_ALIASES.has(source))
}

/** 展示用来源名；旧别名统一显示为「企业微信」。 */
export function displayCustomerSource(source?: string | null): string {
  if (!source) return ''
  return source === '企微添加' ? WECOM_SOURCE : source
}
