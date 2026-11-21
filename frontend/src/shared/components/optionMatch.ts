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
 * 写法统一成「编号 N」（主人 2026-10-12 看着审计页问：「报价单 #8。这个 # 啥意思」）。
 * ⚠️ 这里原来写的是 `#编号`，注释还专门说"和界面上其它地方（如「已转需求 #123」）
 * 保持一致" —— 那个"一致"现在改了：**`#` 是程序员写编号的习惯，中文界面上
 * 没必要让用户认它**，全站统一成「编号 N」（`ops/check_hash_ids.py` 守着）。
 */
export function withCode(label: string, code: unknown): string {
  const text = textOf(code).trim()
  if (!text) return label
  return `${label} 编号 ${text}`
}

/**
 * 按「重名才显示编号」的规矩，把列表转成下拉选项。
 *
 * 背景（主人 2026-10-12）：选项里一律显示「甲编号 11」不好看，也容易让人以为
 * 系统在往外暴露客户总量。编号真正有用的场合只有一个 —— **名字撞车时分得清谁是谁**。
 *
 * 规矩：同一份列表里，显示文案只有一条的 → 只显示文案，不带编号；
 *       有两条及以上完全一样的 → 这几条都带上编号（不带就分不出来了）。
 *
 * 判重口径就是**最终显示文案本身**。所以像「甲（C 级）」和「甲（A 级）」这种
 * 靠等级后缀已经分得开的，不加编号；只有文案一模一样才加。
 *
 * 注意：不显示编号**不影响按编号搜**。搜索走 `optionMatcher`（label 和 value 一起匹配），
 * 编号还挂在 value 上，输 11 照样搜得到这个客户。
 *
 * 用法：`optionList={optionsWithCodeOnDuplicate(items, (i) => i.name, (i) => i.id)}`
 */
export function optionsWithCodeOnDuplicate<T>(
  items: readonly T[],
  labelOf: (item: T) => string,
  idOf: (item: T) => number,
): { value: number; label: string }[] {
  // 先数一遍：同样的文案出现了几次
  const counts = new Map<string, number>()
  for (const item of items) {
    const key = labelOf(item).trim()
    counts.set(key, (counts.get(key) ?? 0) + 1)
  }
  return items.map((item) => {
    const label = labelOf(item)
    const repeated = (counts.get(label.trim()) ?? 0) > 1
    return {
      value: idOf(item),
      label: repeated ? withCode(label, idOf(item)) : label,
    }
  })
}
