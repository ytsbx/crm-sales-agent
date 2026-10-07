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

export function currencyPrefix(currency?: string | null): string {
  if (!currency) return ''
  return currency === 'CNY' ? '¥' : `${currency} `
}

/** 单笔金额：`¥1,000` / `USD 1,000` / `1,000（币种待核实）`；空值给 `-`。 */
export function formatMoney(
  value: number | null | undefined,
  currency?: string | null,
): string {
  if (value === null || value === undefined) return '-'
  const text = value.toLocaleString('zh-CN')
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
