/** 瞬时时间统一显示为北京时间；日期控件的纯日期不作时区平移。 */
export const BUSINESS_TIME_ZONE = 'Asia/Shanghai'
type TimeValue = string | number | Date | null | undefined

export function parseBusinessTime(value: TimeValue): Date | null {
  if (value == null || value === '') return null
  let input = value
  if (typeof input === 'string') {
    input = input.trim()
    if (/^\d{4}-\d{2}-\d{2}$/.test(input)) input += 'T00:00:00+08:00'
    // CRM 的无偏移 datetime 与后端 timebase 约定一致，按 UTC 解释。
    else if (/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$/.test(input)) input = input.replace(' ', 'T') + 'Z'
  }
  const date = input instanceof Date ? input : new Date(input)
  return Number.isNaN(date.getTime()) ? null : date
}

export function formatBeijingTime(value: TimeValue, options: Intl.DateTimeFormatOptions = {}): string {
  const date = parseBusinessTime(value)
  return date ? date.toLocaleString('zh-CN', { hour12: false, ...options, timeZone: BUSINESS_TIME_ZONE }) : '-'
}

export function formatBeijingDate(value: TimeValue, options: Intl.DateTimeFormatOptions = {}): string {
  const date = parseBusinessTime(value)
  return date ? date.toLocaleDateString('zh-CN', { ...options, timeZone: BUSINESS_TIME_ZONE }) : '-'
}

export function formatBeijingClock(value: TimeValue, options: Intl.DateTimeFormatOptions = {}): string {
  const date = parseBusinessTime(value)
  return date ? date.toLocaleTimeString('zh-CN', { hour12: false, ...options, timeZone: BUSINESS_TIME_ZONE }) : '-'
}

/** 钉钉 workflow 实测以北京时间生成分钟精度的 wall time，却附加 Z。
 * 仅对这个已验证的格式兼容；秒精度 Z、显式偏移和 epoch 仍保留其瞬时语义。
 * 仅用于钉钉原始节点/操作时间，不能用于 CRM synced_at / created_at。
 */
export function formatDingTalkTime(value: TimeValue): string {
  const input = typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z$/.test(value)
    ? value.slice(0, -1) + ':00+08:00' : value
  return formatBeijingTime(input)
}
