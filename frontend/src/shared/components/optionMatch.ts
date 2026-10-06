/**
 * 下拉选项的搜索匹配：**支持按编号搜**。
 *
 * 背景（主人 2026-10-06 反馈）：「选品下单的下拉输编号没用（按名字过滤）——
 * 你输入 3680 时像坏了一样」。
 *
 * 根因：这个组件库的 `filter` 传 `true` 时，**只按显示出来的文字（label）匹配**。
 * 而全项目的下拉 label 都是「名称」「名称（等级）」这类，**不含编号**；
 * 编号在 `value` 上。于是用户输 3680 一条都搜不出来 —— 看着像坏了。
 *
 * 做法：传一个自定义匹配函数给 `filter`，把 **label + value 一起纳入搜索范围**。
 * 因为本项目的 `value` 放的就是业务编号（客户 id / SKU id / 单号…），
 * 所以不必逐个改 41 处选项数据，就能让"输编号搜得到"。
 *
 * 另外顺带解决两个体验问题：
 *  · **大小写不敏感** —— 输 `q2026` 也能搜到 `Q202610060001`。
 *  · **label 是 React 元素也能搜** —— 有些下拉的 label 里塞了标签组件，
 *    直接 String(label) 会变成 `[object Object]`，等于搜不到。这里递归取文字。
 */

/** 从任意「能当文案的东西」里取出可搜索的纯文本。 */
export function textOf(node: unknown): string {
  if (node === null || node === undefined) return ''
  if (typeof node === 'string') return node
  if (typeof node === 'number' || typeof node === 'boolean') return String(node)
  if (Array.isArray(node)) return node.map(textOf).join(' ')
  if (typeof node === 'object') {
    // React 元素：取它的 children 继续递归（够本项目用，不需要真渲染）
    const children = (node as { props?: { children?: unknown } }).props?.children
    if (children !== undefined) return textOf(children)
  }
  return ''
}

/** 选项里可能承载编号的字段（本项目不同下拉用的名字不完全一致）。 */
const CODE_KEYS = ['code', 'no', 'keyword', 'sku_code', 'quote_no']

/**
 * 传给 `Select` 的 `filter`：同时按 label、value 和几个常见编号字段匹配。
 *
 * 用法：`<Select filter={optionMatcher} ... />`
 */
export function optionMatcher(input: string, option: unknown): boolean {
  const query = input.trim().toLowerCase()
  if (!query) return true
  const record = (option ?? {}) as Record<string, unknown>
  const parts = [textOf(record.label), textOf(record.value)]
  for (const key of CODE_KEYS) parts.push(textOf(record[key]))
  const haystack = parts.join(' ').toLowerCase()
  // 支持多词：'周 6040' 这类按空格拆开全部命中才算匹配
  return query.split(/\s+/).every((word) => haystack.includes(word))
}

/**
 * 把编号拼进选项文案，让它"看得见"（主人另一条要求：选项里把编号显示全）。
 *
 * 统一用 `#编号` 的形式，和界面上其它地方（如「已转需求 #123」）保持一致。
 */
export function withCode(label: string, code: unknown): string {
  const text = textOf(code).trim()
  if (!text) return label
  return `${label} #${text}`
}
