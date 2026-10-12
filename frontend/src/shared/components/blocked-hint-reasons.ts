/**
 * 「为什么这个按钮点不了」的原因推导 —— **纯函数，没有 React、没有 DOM**。
 *
 * 主人 2026-10-12 反馈（原话）：
 *   「这种我点又点不了，又不给提示为什么点不了，这种体验很不好，类似的情况都要修」
 * 当时那一屏是「从来源建立订单草稿」：报价 Q202610120001 V1 一条明细都没有，
 * 确认按钮从打开那一刻起就是灰的，界面上**一个字都没说**，用户只能自己猜。
 *
 * 与 `toast-guard.ts`（提示条不许只有红点没有字）是同一类问题：
 * 那边管「弹出来的提示」，这边管「按不下去的按钮」。所以做法也照它：
 * **把判据收在一个地方，让每个禁用按钮都能说出原因**，而不是各页面各写一遍。
 *
 * 为什么单独一个文件：这里有真正会算错的东西（漏掉一条原因 = 用户又卡住一次），
 * 所以它必须能被直接跑断言 —— 见 `ops/blocked_hint.test.mjs`（走 CI）。
 * 组件在 `BlockedHint.tsx`，它只负责把这些函数的返回值画出来 / 弹出来。
 */

/** 「条件没满足 → 该说什么」的一条对应关系。 */
export interface BlockedReason {
  /** 条件成立（true）表示**已经满足**，不再是阻塞原因。 */
  ok: boolean
  /** 条件没满足时给用户看的一句话（要写「怎么才能继续」，不要只写「不可用」）。 */
  reason: string
}

/**
 * 取出所有「还没满足」的原因文案，顺序与传入顺序一致（先传更根本的原因）。
 *
 * 为什么要**全部**返回而不是只返回第一条：有的按钮同时卡在两件事上
 * （既没选目标客户、又有冲突没处理），只说一条会让人改完一处再撞一次。
 *
 * 边界（都有断言）：
 * - `blocked` 为 `undefined` / `null` → 空数组。调用方常常在数据还没到时先算一遍，
 *   这时候不该冒出一个原因；数据没到该由 `loadingReason` 那条单独说。
 * - `reason` 是空串或纯空格 → 不算原因。宁可少说一句，也不要在界面上留一行空白
 *   （同 toast-guard：空白提示等于没有提示）。
 * - 全部满足 → 空数组，调用方据此不渲染任何东西。
 */
export function blockedReasons(blocked: readonly BlockedReason[] | null | undefined): string[] {
  if (!blocked) return []
  const reasons: string[] = []
  for (const item of blocked) {
    if (!item || item.ok) continue
    if (typeof item.reason !== 'string' || item.reason.trim() === '') continue
    reasons.push(item.reason)
  }
  return reasons
}

/**
 * 同一个原因去掉重复。
 *
 * 场景：明细有 8 行都没填数量，逐行说会得到 8 句一样的话。汇总时先用它去重，
 * 免得界面上糊一大片重复文字（各行的行内提示仍然逐行显示，那是为了就近指出问题）。
 */
export function distinctReasons(reasons: readonly string[]): string[] {
  return [...new Set(reasons)]
}

/**
 * 「数据还没读完」这一条与业务原因分开。
 *
 * 两者对用户的含义完全不同：一个是等两秒就好，一个要用户动手去补。
 * 混在一句话里会让人以为要自己去改什么。
 */
export const LOADING_REASON = '正在读取数据，请稍候…'

/**
 * 点了灰按钮之后，那条提示条（Toast）该说什么。
 *
 * 为什么要有这个函数：主人 2026-10-06 定的规矩是**两条都要**
 * （`PriceCenterPage.tsx:880` 的原话注释）——
 *   ① 禁用时按钮旁给一行灰字，说清缺什么（`BlockedHint` 负责）；
 *   ② 禁用时点一下要**有反应**，而不是石沉大海（这里负责）。
 * 两条说的是同一件事，所以**文案必须同源**：都从 `reasons` 来。
 * 各写一份的话，改了灰字忘了提示条，用户看到的就又是两套说法。
 *
 * 只说第一条原因 + 「还有 N 条」：提示条是一闪而过的，塞三句没人读得完；
 * 完整清单在灰字块里（那是常驻的，可以慢慢看）。
 *
 * 返回**空串**表示「没有原因、这个按钮本来是能点的」——调用方据此不发提示
 * （`Toast.warning('')` 会被 `installToastGuard()` 兜成别的句子，反而更糟）。
 */
export function blockedToastText(
  reasons: readonly string[],
  action?: string,
): string {
  const list = distinctReasons(reasons.filter((text) => text.trim() !== ''))
  if (list.length === 0) return ''
  const lead = action ? `还不能${action}：` : ''
  // 「还有 N 条」数的是**去重之后**的条数：8 行都缺数量时，8 句一样的话
  // 不该被说成「还有 7 条原因」。
  const rest = list.length > 1 ? `（另有 ${list.length - 1} 条原因，见弹窗底部的说明）` : ''
  return `${lead}${list[0]}${rest}`
}
