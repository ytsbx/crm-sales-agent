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

/** 日期统一写法：`2026-11-16`。
 * 为什么不用 zh-CN 的默认日期：`toLocaleDateString('zh-CN')` 输出的是
 * `2026/11/16`（斜杠），而页面里大量"有效期至 / 到期日"是后端原值 `2026-12-16`
 * （短横线）—— 同一个页面上两种日期写法并存，看着就是没收拾过
 * （2026-11-17 统一）。`en-CA` 的日期格式恰好就是 `2026-11-16`，最省事。
 * 传了自定义 options 时也一样：日期段走 en-CA（短横线），时间段单独拼，
 * 不要再退回 zh-CN 的 `2026/11/16`（2026-11-17 复验 R-02）。 */
/** 哪些 options 属于「日期部分」：这些交给 en-CA（它天生输出 2026-11-16 的短横线写法）。 */
const DATE_PART_KEYS = ['year', 'month', 'day', 'weekday', 'era'] as const

function splitDateOptions(options: Intl.DateTimeFormatOptions): {
  date: Intl.DateTimeFormatOptions
  time: Intl.DateTimeFormatOptions
} {
  const date: Intl.DateTimeFormatOptions = {}
  const time: Intl.DateTimeFormatOptions = {}
  for (const [key, value] of Object.entries(options)) {
    if (value === undefined) continue
    if ((DATE_PART_KEYS as readonly string[]).includes(key)) {
      ;(date as Record<string, unknown>)[key] = value
    } else {
      ;(time as Record<string, unknown>)[key] = value
    }
  }
  return { date, time }
}

/**
 * 传了自定义 options 时的统一拼法：**日期一律 YYYY-MM-DD，时间单独拼在后面**。
 * 为什么不直接 `toLocaleString('zh-CN', options)`：zh-CN 的日期是 `2026/11/16`，
 * 跟页面里后端原值 `2026-12-16` 两种写法并存（2026-11-17 视觉审视 R-02）。
 * 也不直接交给 en-CA 拼整串：它会插一个英文逗号（`2026-11-16, 16:38`）。
 */
export function formatBeijingTime(value: TimeValue, options: Intl.DateTimeFormatOptions = {}): string {
  const date = parseBusinessTime(value)
  if (!date) return '-'
  if (Object.keys(options).length === 0) return formatBeijingDateTime(value)
  const parts = splitDateOptions(options)
  const hasDatePart = Object.keys(parts.date).length > 0
  const hasTimePart = Object.keys(parts.time).length > 0
  const day = hasDatePart
    ? date.toLocaleDateString('en-CA', { ...parts.date, timeZone: BUSINESS_TIME_ZONE })
    : ''
  const clock = hasTimePart
    ? date.toLocaleTimeString('zh-CN', { hour12: false, ...parts.time, timeZone: BUSINESS_TIME_ZONE })
    : ''
  return [day, clock].filter(Boolean).join(' ')
}

/**
 * 带时分的时间：`2026-11-16 16:38`（工作台动态、待办到期、审计这类字段统一用它）。
 * 跟 `formatBeijingTime` 默认值（带秒）区分开：列表/卡片上秒没有信息量，只添噪音。
 */
export function formatBeijingDateTime(value: TimeValue, withSeconds = false): string {
  const date = parseBusinessTime(value)
  if (!date) return '-'
  const day = date.toLocaleDateString('en-CA', { timeZone: BUSINESS_TIME_ZONE })
  const clock = date.toLocaleTimeString('zh-CN', {
    hour12: false,
    hour: '2-digit',
    minute: '2-digit',
    ...(withSeconds ? { second: '2-digit' } : {}),
    timeZone: BUSINESS_TIME_ZONE,
  })
  return `${day} ${clock}`
}

export function formatBeijingDate(value: TimeValue, options: Intl.DateTimeFormatOptions = {}): string {
  const date = parseBusinessTime(value)
  if (!date) return '-'
  if (Object.keys(options).length > 0) {
    // 同上：日期部分走 en-CA，保证短横线；调用方只传日期字段时才走到这里
    return date.toLocaleDateString('en-CA', { ...options, timeZone: BUSINESS_TIME_ZONE })
  }
  // 只有日期时输出 2026-11-16，跟详情页里"有效期至"这类原值同一种写法
  return date.toLocaleDateString('en-CA', { timeZone: BUSINESS_TIME_ZONE })
}

/** 中文星期几：`星期二`。en-CA 的 weekday 是英文（Tuesday），日期走它的同时
 *  星期必须单独用 zh-CN 取，否则页面会出现「Tuesday, November 17, 2026」这种混搭。 */
export function formatBeijingWeekdayCN(value: TimeValue): string {
  const date = parseBusinessTime(value)
  return date ? date.toLocaleDateString('zh-CN', { weekday: 'long', timeZone: BUSINESS_TIME_ZONE }) : '-'
}

export function formatBeijingClock(value: TimeValue, options: Intl.DateTimeFormatOptions = {}): string {
  const date = parseBusinessTime(value)
  return date ? date.toLocaleTimeString('zh-CN', { hour12: false, ...options, timeZone: BUSINESS_TIME_ZONE }) : '-'
}

/** 同序 OA workflow 实测以北京时间生成分钟精度的 wall time，却附加 Z。
 * 仅对这个已验证的格式兼容；秒精度 Z、显式偏移和 epoch 仍保留其瞬时语义。
 * 仅用于同序 OA 原始节点/操作时间，不能用于 CRM synced_at / created_at。
 */
export function formatDingTalkTime(value: TimeValue): string {
  const input = typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z$/.test(value)
    ? value.slice(0, -1) + ':00+08:00' : value
  return formatBeijingTime(input)
}
