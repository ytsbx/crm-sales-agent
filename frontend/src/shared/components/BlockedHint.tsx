import type { CSSProperties, ReactNode } from 'react'
import { Toast } from '@douyinfe/semi-ui'
import {
  blockedReasons as collectReasons,
  blockedToastText,
  distinctReasons,
  type BlockedReason,
} from './blocked-hint-reasons'

/**
 * 「这个按钮为什么点不了」的全站共用件 —— 页面里不要再手写。
 *
 * 主人 2026-10-12 反馈（原话）：
 *   「这种我点又点不了，又不给提示为什么点不了，这种体验很不好，类似的情况都要修」
 * 起因是「从来源建立订单草稿」：来源报价 Q202610120001 V1 一条明细都没有
 * （13:15 建的草稿，`quote_items` 0 行），于是确认按钮从打开那一刻就是灰的，
 * 界面上**一个字都没说**。**凡是「灰按钮 + 没有解释」的地方都属于这一类。**
 *
 * 规矩不是这里新定的 —— 主人 2026-10-06 就为「查价按钮灰着没有任何解释」定过
 * 两条（`modules/pricing/PriceCenterPage.tsx:880` 的注释是原始判例），本文件只是
 * 把它抽成**唯一出口**（同 `toast-guard.ts` 抽提示条、`ConfirmRequiredModal` 抽确认弹窗
 * 的理由：逐个页面手写一定会漏，以后新写的代码照样会漏）：
 *
 *   ① **看得见**：禁用时旁边常驻一行灰字，说清缺什么；
 *   ② **点得动**：禁用时点一下**要有反应**，不能石沉大海。
 *
 * 为什么不用 hover 气泡（tooltip）：禁用控件的 tooltip 出了名的靠不住 ——
 * `pointer-events: none`、触屏没有 hover、键盘根本够不着。主人要的是「点不了的时候
 * **看得见**为什么」，所以 ① 是常驻文字。
 *
 * ⚠️ 两个都**不改变按钮的可用性**：灰按钮该灰还是灰。少了这层，改动就从
 * 「补提示」变成了「放松口径」，那是另一件事。
 */
export default function BlockedHint({
  reasons,
  blocked,
  action,
  style,
  compact = false,
}: {
  /** 已经算好的原因文案；空数组 = 不渲染任何东西。 */
  reasons?: readonly string[]
  /**
   * 也可以直接把「条件 → 文案」传进来，由组件取出没满足的那些
   * （判据在 `blocked-hint-reasons.ts`，那边有单测）。两种写法二选一。
   */
  blocked?: readonly BlockedReason[] | null
  /**
   * 被禁用的是哪个动作，用来起头，例如「建立订单草稿」→
   * 「还不能建立订单草稿：…」。不传就不写这句头。
   */
  action?: string
  style?: CSSProperties
  /**
   * **紧凑模式**：一行小字，去掉底色/内边距/项目符号。
   *
   * 默认的块状样式是给**弹窗底部**那种宽松地方用的（12px 上边距 + 警示底色 +
   * 内边距 + 项目符号）。放进紧凑的**表单行/工具栏**里就太占地了 ——
   * 主人 2026-10-16 截图（打样「产品名称 + 数量 + 添加」那一行）：
   * 「红色这一块占着那么一大块空白，不美观」。
   *
   * 紧凑模式仍然**看得见**（规矩① 不打折）：原因以普通灰字显示在按钮下面，
   * 只是不再铺一大块底色。点一下弹提示（规矩②）完全不变。
   */
  compact?: boolean
}): ReactNode {
  const unique = distinctReasons(
    (reasons ? [...reasons] : collectReasons(blocked)).filter((text) => text.trim() !== ''),
  )
  if (unique.length === 0) return null
  if (compact) {
    return (
      <div
        className="blocked-hint-compact"
        style={style}
        aria-live="polite"
        data-blocked-hint="1"
      >
        {/* 紧凑模式把"要做什么"压成一句话：多条原因用顿号连起来，不列项目符号 */}
        {action ? `还不能${action}：` : ''}
        {unique.join('；')}
      </div>
    )
  }
  return (
    <div className="blocked-hint" style={style} aria-live="polite" data-blocked-hint="1">
      <div className="blocked-hint-lead">{action ? `还不能${action}，原因：` : '暂时不能继续，原因：'}</div>
      <ul className="blocked-hint-list">
        {unique.map((text) => (
          <li key={text}>{text}</li>
        ))}
      </ul>
    </div>
  )
}

/**
 * 第 ② 条「点一下要有反应」的实现钩子。
 *
 * Semi 的按钮禁用走的是**原生 `disabled` 属性**，浏览器对原生禁用控件
 * **根本不派发点击事件** —— 所以光在按钮外面包一层容器是收不到点击的。
 * 判例里的破法是：禁用态给按钮加 `style={{ pointerEvents: 'none' }}`，
 * 按钮对点击透明，落点变成外层容器，容器上的 onClick 才接得住
 * （`PriceCenterPage.tsx:886-889` 有完整说明；`disabled-hint-click` 这个
 * className 就是给那种容器用的，见 index.css）。
 *
 * 用法（外层容器接点击，按钮自己透明）：
 *
 * ```tsx
 * const notifyBlocked = useBlockedNotify(reasons, '建立订单草稿')
 * <div className="blocked-hint-click" onClick={notifyBlocked}>
 *   <Button disabled={blocked} style={blocked ? { pointerEvents: 'none' } : undefined}
 *     onClick={run}>建立订单草稿</Button>
 * </div>
 * ```
 *
 * 返回的函数的两个性质：
 * - 按钮**本来就能点**时调用它 → 什么都不做（不发提示、不吞事件）。
 *   可用态下点击会冒泡到容器，这一次必须放过去，否则就变成「能点也不响应」。
 * - 光标：由 `.blocked-hint-click` 的 CSS 处理（可点态 `pointer`、禁用态 `not-allowed`），
 *   页面不用自己写 `cursor`。
 */
export function useBlockedNotify(reasons: readonly string[], action?: string): () => void {
  return () => {
    const text = blockedToastText(reasons, action)
    // 空串 = 没有原因（按钮本来能点）：这时绝不能发提示条，
    // 否则可用态的冒泡会被弹一句莫名其妙的警告。
    if (!text) return
    Toast.warning(text)
  }
}
