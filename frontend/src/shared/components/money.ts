/**
 * 金额与币种的统一渲染（第九批 §9.9）。
 *
 * 背景：订单详情早就按订单币种显示了，但**订单列表**与**客户页的订单/报价表**
 * 一直写死 `¥`；客户概览更是把不同币种的金额直接相加 —— 100 CNY + 100 USD
 * 显示成「¥200」。这比不显示更糟：它看起来像个可以拿去用的数字。
 *
 * 约定：
 * - 单笔金额一律带**实际币种**；人民币保持 `¥`（最常用，视觉不变）；
 * - 其它币种写成 `USD 1,000`（币种码 + 空格），避免与 `$` / `€` 混淆；
 * - **没有币种**的历史数据如实写「币种待核实」，不默认当成人民币；
 * - 这里**不做任何跨币种折算** —— 那是业务规则，拍板前不替它决定。
 */

/**
 * 把金额补成**两位小数**，只补位数、不改数值（"0" → "0.00"，"2100.5" → "2100.50"）。
 *
 * 为什么要它：库里的精确金额是十进制字符串，但**位数并不统一** ——
 * 一分没付的那几行存的是 "0"，收到钱的那几行是 "2100.00"。
 * 同一列里一半带两位、一半不带，看着像两个系统拼出来的。
 * 先补位再交给 `formatMoney`，千分位与币种仍由它负责。
 * 浮点不要参与：数字走 `toFixed(2)`，字符串按小数点切段补零。
 */
export function toTwoDecimals(
  value: number | string | null | undefined,
): number | string | null | undefined {
  if (value === null || value === undefined) return value
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) return value
    return value.toFixed(2)
  }
  if (!/^-?\d+(?:\.\d+)?$/.test(value)) return value
  const [whole, fraction = ''] = value.split('.')
  if (fraction.length >= 2) return `${whole}.${fraction.slice(0, 2)}`
  return `${whole}.${fraction.padEnd(2, '0')}`
}

/**
 * 单价与费率的展示：**保真**（最多保留 4 位），最少补到 2 位，多余的尾零去掉。
 *
 * 为什么不能像金额那样截两位（2026-11-17，CI 抓到）：
 * 库里 `unit_price` 是 numeric(16,4)，一张 1000 件、单价 33.3333 的单子
 * 金额是 33,333.30。单价截成 ¥33.33 之后，"单价 × 数量" 就不再等于金额，
 * 客户一对账就能发现差 3 块多。CI 的 confirmation_browser_smoke 断言
 * 订单明细单价必须显示 ¥33.3333，正是这条防线。
 *
 * 反过来也不能原样印 "150.0000"：那是库里的存储格式，不是给人看的数字，
 * 所以尾零要去掉、最少留两位（¥150.00）。
 *
 * 一句话：金额（总额/应收/回款）两位；单价、费率保真。
 */
export function formatUnitPrice(
  value: number | string | null | undefined,
  currency?: string | null,
): string {
  if (value === null || value === undefined) return '-'
  const raw = typeof value === 'number'
    ? (Number.isFinite(value) ? String(value) : '')
    : String(value).trim()
  // 非纯数字（'abc'、'1e5'）交回 formatMoney 处理，那里会给出 '-' 或合理兜底
  if (!/^-?\d+(?:\.\d+)?$/.test(raw)) return formatMoney(value, currency)
  const [whole, fraction = ''] = raw.split('.')
  // 只保留 4 位（库里就是 4 位），再把尾零去掉；少于两位补齐
  const trimmed = fraction.slice(0, 4).replace(/0+$/, '')
  const frac = trimmed.length < 2 ? trimmed.padEnd(2, '0') : trimmed
  // 千分位仍交给 formatMoney：它认得 "1234.56" 这种字符串
  return formatMoney(`${whole}.${frac}`, currency)
}

export function currencyPrefix(currency?: string | null): string {
  if (!currency) return ''
  return currency === 'CNY' ? '¥' : `${currency} `
}

/** 单笔金额：`¥1,000` / `USD 1,000` / `1,000（币种待核实）`；空值给 `-`。 */
export function formatMoney(
  value: number | string | null | undefined,
  currency?: string | null,
): string {
  if (value === null || value === undefined) return '-'
  // 非有限数（NaN / Infinity）不能原样 toLocaleString —— 会渲染成字面量
  // 「NaN」「∞」贴到金额位置，是最不该出现的东西（2026-10-14 审查）。
  // 宁可显示 '-' 让人一眼看出这个数是坏的，也不要装作有个数。
  if (typeof value === 'number' && !Number.isFinite(value)) return '-'
  let text: string
  if (typeof value === 'string' && /^-?\d+(?:\.\d+)?$/.test(value)) {
    const [whole, fraction] = value.split('.')
    // BigInt('-0') 会丢掉负号；小于一元的负数仍须保留原金额方向。
    const sign = whole.startsWith('-') ? '-' : ''
    const magnitude = sign ? whole.slice(1) : whole
    text = sign + BigInt(magnitude).toLocaleString('zh-CN') + (fraction === undefined ? '' : `.${fraction}`)
  } else if (typeof value === 'number') {
    text = value.toLocaleString('zh-CN')
  } else {
    // 字符串里可能是 'abc'、'NaN'、'1e5' 这类非纯数字写法；非有限就显示 '-'。
    const numeric = Number(value)
    if (!Number.isFinite(numeric)) return '-'
    text = numeric.toLocaleString('zh-CN')
  }
  const prefix = currencyPrefix(currency)
  return prefix ? `${prefix}${text}` : `${text}（币种待核实）`
}

/**
 * 按币种分组的一串金额：`¥1,000 ／ USD 200`。
 * 用于"**不合并**不同币种"的场景（客户概览的订单合计）。
 */
export function formatMoneyGroups(
  groups: { currency?: string | null; amount: number }[] | null | undefined,
): string {
  if (!groups || groups.length === 0) return '-'
  return groups
    .map((item) => formatMoney(item.amount, item.currency))
    .join(' ／ ')
}

/** 分析页保留整元展示；精确字符串按既有Math.round方向取整，不先转Number。 */
export function formatWholeCny(value: number | string | null | undefined): string {
  if (typeof value !== 'string' || !/^-?\d+(?:\.\d+)?$/.test(value)) {
    const numeric = Number(value ?? 0)
    return Number.isFinite(numeric) ? `¥${Math.round(numeric).toLocaleString('zh-CN')}` : '-'
  }
  const negative = value.startsWith('-')
  const [whole, fraction = ''] = (negative ? value.slice(1) : value).split('.')
  const first = Number(fraction[0] ?? '0')
  // Math.round的负数半值向正无穷取整；保留旧页面对负数和负零的显示。
  const increment = first > 5 || (first === 5 && (!negative || /[1-9]/.test(fraction.slice(1))))
  const integer = BigInt(whole) + (increment ? 1n : 0n)
  return `¥${negative ? '-' : ''}${integer.toLocaleString('zh-CN')}`
}
